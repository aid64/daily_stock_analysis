# -*- coding: utf-8 -*-
"""①-D 每日摘要总线测试：入总线/去重/分级置顶/持久化/拦截钩子/定时判定"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from src.services import notification_digest_service as dsvc
from src.services.notification_digest_service import (
    append_to_bus,
    digest_background_task,
    digest_already_sent,
    mark_digest_sent,
    read_day_entries,
    render_digest,
    run_daily_digest,
    should_send_digest_now,
)


@pytest.fixture()
def bus_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "bus"
    monkeypatch.setattr(dsvc, "BUS_ROOT", d)
    return d


def _append(content, route="report", severity="info", dedup=None, **kw):
    return append_to_bus(content=content, route_type=route, severity=severity,
                         dedup_key=dedup, now=kw.pop("now", None), **kw)


class TestBus:
    def test_append_and_dedup(self, bus_dir):
        queued1, e1 = _append("事件套利：SH113062 出现要约收购", dedup="ev-1")
        queued2, _ = _append("事件套利：SH113062 出现要约收购", dedup="ev-1")
        assert queued1 is True and queued2 is False  # 同 key 当日去重
        assert len(read_day_entries()) == 1
        assert e1["route_type"] == "report"

    def test_dedup_survives_restart(self, bus_dir):
        """持久化：重建"进程"（新调用）后同 key 仍去重"""
        _append("转债打新：123456 申购", dedup="bond-1")
        # 模拟重启：BUS_ROOT 不变（落盘），直接再 append
        queued, _ = _append("转债打新：123456 申购", dedup="bond-1")
        assert queued is False

    def test_different_days_no_cross_dedup(self, bus_dir):
        _append("每日例行", dedup="x", now=datetime(2026, 9, 4, 9, 0))
        queued, _ = _append("每日例行", dedup="x",
                            now=datetime(2026, 9, 5, 9, 0))
        assert queued is True  # 跨日不去重

    def test_same_logical_event_replaces_old_version(self, bus_dir, monkeypatch):
        monkeypatch.setattr(dsvc, "_held_symbols", lambda: {"000899"})
        payload = {
            "artifact_type": "daily_operation_sheet",
            "logical_id": "daily_operation_sheet:2026-09-09",
            "symbols": ["000899"],
            "source": "daily_pipeline",
        }
        first, _ = _append("【今日操作单】旧数据", dedup="pipeline-old",
                           structured_payload=payload)
        second, entry = _append("〔修正版〕【今日操作单】新数据", dedup="manual-new",
                                structured_payload=payload)

        assert first is True and second is True
        entries = read_day_entries()
        assert len(entries) == 1
        assert entries[0]["content"].endswith("新数据")
        assert entry["holding_related"] is True

    def test_symbol_variants_match_holdings(self, bus_dir, monkeypatch):
        monkeypatch.setattr(dsvc, "_held_symbols", lambda: {"SH113062", "113062"})
        _, entry = _append(
            "操作单已更新", dedup="cb-plan",
            structured_payload={
                "artifact_type": "daily_operation_sheet",
                "logical_id": "daily_operation_sheet:2026-09-09",
                "symbols": ["113062.SH"],
            },
        )
        assert entry["holding_related"] is True


class TestRendering:
    def test_holding_related_tops_and_flagged(self, bus_dir, monkeypatch):
        monkeypatch.setattr(dsvc, "_held_symbols",
                            lambda: {"113062", "SH113062"})
        _append("大盘复盘：今日市场震荡", route="report")
        _append("事件套利：113062.SH 现金选择权申报开启", route="alert",
                severity="critical")
        _append("转债打新：新券申购", route="report")
        _append("ETF 溢价告警：511380 溢价 3.2%", route="alert",
                severity="critical")
        entries = read_day_entries()
        text = render_digest(entries, day="2026-09-05")
        lines = text.splitlines()
        related_idx = next(i for i, ln in enumerate(lines) if "持仓相关" in ln)
        important_idx = next(i for i, ln in enumerate(lines) if "◆ 重要" in ln)
        other_idx = next(i for i, ln in enumerate(lines) if "◆ 其他" in ln)
        assert related_idx < important_idx < other_idx  # 置顶
        assert "113062" in text
        assert "共 4 条" in lines[0]

    def test_empty_bus_message(self, bus_dir):
        assert "无待汇总" in render_digest([])


class TestScheduleGate:
    def test_time_gate(self):
        dt = datetime(2026, 9, 5, 18, 1)
        assert should_send_digest_now(dt, "18:00") is True
        dt2 = datetime(2026, 9, 5, 9, 0)
        assert should_send_digest_now(dt2, "18:00") is False

    def test_once_per_day_marker(self, bus_dir):
        mark_digest_sent()
        assert digest_already_sent() is True
        # 跨日重置
        assert digest_already_sent("2026-09-04") is False


class TestRunDailyDigest:
    def test_send_via_bypass_and_marker(self, bus_dir, monkeypatch):
        sent = {}
        bus_dir.mkdir(parents=True, exist_ok=True)
        _append("持仓相关事件：113062 强赎公告", route="alert",
                severity="critical", dedup="a")
        _append("普通日报", route="report", dedup="b")

        class _FakeNotifier:
            def send_with_results(self, content, **kw):
                sent["content"] = content
                sent["kw"] = kw
                return type("R", (), {"dispatched": True, "success": True})()

        import src.notification as notification_mod
        orig = notification_mod.NotificationService
        monkeypatch.setattr(notification_mod, "NotificationService",
                            _FakeNotifier)
        text = run_daily_digest(force=True)
        assert text and "每日摘要" in sent["content"]
        assert sent["kw"]["dedup_key"].startswith("daily_digest:")
        assert digest_already_sent() is True
        # bypass 标志：第二次 force 发送时 hook 不再拦截（FakeNotifier 不经过
        # send_with_results 的总线路径——此处验证 digest 条目含分级文本）
        assert "持仓相关" in sent["content"]

    def test_empty_bus_marks_sent(self, bus_dir):
        run_daily_digest(force=True)
        assert digest_already_sent() is True
        run_daily_digest()  # 已发送 → None
        entries = read_day_entries()
        assert entries == []


class TestBackgroundTask:
    def test_task_entry_shape(self):
        cfg = type("C", (), {"notification_daily_digest_enabled": True,
                             "notification_digest_time": "18:00"})()
        entry = digest_background_task(cfg)
        assert entry["name"] == "notification_daily_digest"
        assert entry["interval_seconds"] == 300
        entry["task"]()  # 未到点/空总线 → 静默不抛

    def test_disabled_config_noop(self, bus_dir):
        cfg = type("C", (), {"notification_daily_digest_enabled": False,
                             "notification_digest_time": "18:00"})()
        entry = digest_background_task(cfg)
        entry["task"]()
        assert read_day_entries() == []


class TestInterceptHook:
    """notification.py 挂点：开关开启时静态推送入总线"""

    def _service_with_config(self, enabled: bool, monkeypatch=None):
        from src.notification import NotificationService
        from src.config import get_config

        svc = NotificationService()
        if monkeypatch is not None:
            monkeypatch.setattr(svc._config,
                                "notification_daily_digest_enabled", enabled)
        return svc

    def test_enabled_routes_to_bus(self, bus_dir, monkeypatch):
        svc = self._service_with_config(True, monkeypatch)
        result = svc.send_with_results("个股日报 600519", route_type="report",
                                       dedup_key="daily-1")
        assert result.status == "digest_queued"
        entries = read_day_entries()
        assert len(entries) == 1
        assert entries[0]["content"].startswith("个股日报")

    def test_system_error_bypasses_bus(self, bus_dir, monkeypatch):
        svc = self._service_with_config(True, monkeypatch)
        result = svc.send_with_results("系统错误", route_type="system_error",
                                       dedup_key="err-1")
        assert result.status != "digest_queued"
        assert read_day_entries() == []

    def test_disabled_direct_send(self, bus_dir, monkeypatch):
        svc = self._service_with_config(False, monkeypatch)
        result = svc.send_with_results("个股日报", route_type="report",
                                       dedup_key="x")
        assert result.status != "digest_queued"
        assert read_day_entries() == []

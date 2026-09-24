# -*- coding: utf-8 -*-
"""P4.5 决策包归档契约测试：完整判断往返 / hash 校验 / 旧档兼容 / 历史回放。

隔离纪律：所有落盘指向 tmp（SNAPSHOT_DIR），构建路径不连真实 DB/持仓/PIT。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from src.services import decision_snapshot_service as dss
from src.services.decision_snapshot_service import (
    build_snapshot,
    normalize_archive_day,
    save_snapshot,
    verify_snapshot,
)
from src.services.strategies.types import (
    AccountDecision,
    PositionDecision,
    VERDICT_REDUCE,
)


def _decision(day: str | None = None) -> AccountDecision:
    day = day or date.today().isoformat()
    return AccountDecision(
        trade_date=day,
        positions=[PositionDecision(
            symbol="002475", name="立讯精密", quantity=200, avg_cost=56.77,
            last_price=52.78, price_date=day, pnl=-798.0, pnl_pct=-7.0,
            weight_pct=87.2, verdict=VERDICT_REDUCE, target_quantity=100,
            reason="[集中度规则] 权重 87% 超阈值，保底一手降低",
            rule_state="未触发止损", ai_note="",
            breach={"consecutive_days": 3, "ma_structure": "空头排列",
                    "volume_note": "缩量", "urgency": "观察"})],
        cash=1549.0, cash_weight_pct=12.8, cash_verdict="持有",
        cash_reason="E2 分位偏高，不新增配置",
        e2_percentile=0.908, e2_as_of="2026-09-18",
        concentration_threshold_pct=35.0)


@pytest.fixture()
def snap_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "snap"
    monkeypatch.setattr(dss, "SNAPSHOT_DIR", d)
    return d


def _stub_build_deps(monkeypatch):
    """把 build_snapshot 的外部依赖钉死，只测归档契约本身。"""
    monkeypatch.setattr(dss, "_pit_data_version",
                        lambda: {"pit_end": "2026-09-18", "pit_sha256": "z"})
    monkeypatch.setattr(dss, "_resolved_database_path", lambda: "/tmp/x.db")
    import src.services.cb_signal_bridge as bridge
    monkeypatch.setattr(bridge, "load_cb_plan",
                        lambda: {"pit_end": "2026-09-18",
                                 "generated_at": "2026-09-18T08:00:00",
                                 "targets": {}})


class TestNormalizeArchiveDay:
    def test_valid_iso(self):
        assert normalize_archive_day("2026-09-24") == "2026-09-24"

    def test_datetime_prefix_trimmed(self):
        assert normalize_archive_day("2026-09-24T09:00:00") == "2026-09-24"

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            normalize_archive_day("../../etc/passwd")

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            normalize_archive_day("not-a-date")


class TestDecisionCapture:
    def test_captures_full_judgement(self, monkeypatch):
        _stub_build_deps(monkeypatch)
        d = _decision()
        snap = build_snapshot(
            decision=d, rendered_text="今日待办 …完整原文…",
            portfolio_snapshot={"total_cash": 1549.0, "accounts": []},
            order_sheet=None, execution_mode="generated")
        ad = snap["account_decision"]
        assert ad["context_version"] == dss.DECISION_CONTEXT_VERSION
        p = ad["positions"][0]
        assert p["symbol"] == "002475" and p["verdict"] == VERDICT_REDUCE
        assert p["target_quantity"] == 100
        assert p["breach"]["consecutive_days"] == 3      # 破位诊断进档
        assert ad["cash_verdict"] == "持有"
        assert snap["rendered_text"].startswith("今日待办")
        assert snap["execution_mode"] == "generated"
        assert "recomputed" not in ad                    # 当时生成，非重算

    def test_rejects_decision_without_text(self, monkeypatch):
        _stub_build_deps(monkeypatch)
        with pytest.raises(ValueError):
            build_snapshot(decision=_decision(), rendered_text="",
                           portfolio_snapshot={"total_cash": 0, "accounts": []})

    def test_recompute_marked_when_no_decision(self, monkeypatch):
        _stub_build_deps(monkeypatch)
        monkeypatch.setattr(
            "src.services.account_decision_service.build_account_decision",
            lambda **kw: _decision())
        monkeypatch.setattr(
            "src.services.account_decision_service.load_price_rules",
            lambda: {})
        snap = build_snapshot(
            portfolio_snapshot={"total_cash": 1549.0, "accounts": []},
            order_sheet=None)
        assert snap["account_decision"]["recomputed"] is True
        assert snap["execution_mode"] == "generated"


class TestHashVerification:
    def test_roundtrip_verifies(self, snap_dir, monkeypatch):
        _stub_build_deps(monkeypatch)
        snap = build_snapshot(
            decision=_decision(), rendered_text="原文",
            portfolio_snapshot={"total_cash": 1549.0, "accounts": []},
            order_sheet=None)
        save_snapshot(snap)
        loaded = json.loads(
            (snap_dir / "archive" / f"{snap['trade_date']}.json")
            .read_text(encoding="utf-8"))
        assert verify_snapshot(loaded) is True             # 未改：校验通过

    def test_tamper_detected(self, snap_dir, monkeypatch):
        _stub_build_deps(monkeypatch)
        snap = build_snapshot(
            decision=_decision(), rendered_text="原文",
            portfolio_snapshot={"total_cash": 1549.0, "accounts": []},
            order_sheet=None)
        save_snapshot(snap)
        arc = snap_dir / "archive" / f"{snap['trade_date']}.json"
        loaded = json.loads(arc.read_text(encoding="utf-8"))
        loaded["account_decision"]["positions"][0]["target_quantity"] = 0  # 篡改
        assert verify_snapshot(loaded) is False            # 校验失败

    def test_full_length_hash(self, snap_dir, monkeypatch):
        _stub_build_deps(monkeypatch)
        snap = build_snapshot(
            decision=_decision(), rendered_text="原文",
            portfolio_snapshot={"total_cash": 1549.0, "accounts": []},
            order_sheet=None)
        save_snapshot(snap)
        loaded = json.loads(
            (snap_dir / "archive" / f"{snap['trade_date']}.json")
            .read_text(encoding="utf-8"))
        assert len(loaded["content_hash"]) == 64           # 全长非截断

    def test_missing_hash_is_compatible(self):
        assert verify_snapshot({"trade_date": "2026-01-01"}) is True

    def test_legacy_16char_hash_compat(self):
        body = {"trade_date": "2026-01-01", "x": 1}
        full = dss._content_hash(body)
        body["content_hash"] = full[:16]                   # 旧截断指纹
        assert verify_snapshot(body) is True
        body["x"] = 2                                       # 改内容
        assert verify_snapshot(body) is False


class TestHistoryRender:
    def _write(self, snap_dir, day, **over):
        _stub = {"total_cash": 1549.0, "accounts": []}
        snap = {
            "schema_version": 2, "snapshot_id": f"{day}_abcd1234",
            "trade_date": day, "generated_at": f"{day}T09:00:00",
            "execution_mode": over.get("mode", "generated"),
            "plan": {"pit_end": "2026-09-18", "blocked": {}},
            "order_sheet": over.get("sheet", {}),
            "account_decision": over.get("decision",
                                         dss._capture_decision(_decision(day))),
            "rendered_text": "原文", "data_versions": {}, "quality": {"warnings": []},
        }
        save_snapshot({**snap, **{k: v for k, v in over.items()
                                  if k not in ("mode", "sheet", "decision")}})
        return snap

    def test_history_shows_full_judgement(self, snap_dir, monkeypatch):
        from bot.commands.order_sheet import _render_history
        day = date.today().isoformat()
        self._write(snap_dir, day)
        text = _render_history(day)
        assert "〔判断〕002475 减仓→100" in text          # 判决回放
        assert "破位：连续 3 日" in text                   # 破位诊断回放
        assert "〔现金〕持有" in text                       # 现金结论回放
        assert "E2 90.8%" in text

    def test_history_flags_tamper(self, snap_dir, monkeypatch):
        from bot.commands.order_sheet import _render_history
        day = date.today().isoformat()
        self._write(snap_dir, day)
        arc = snap_dir / "archive" / f"{day}.json"
        loaded = json.loads(arc.read_text(encoding="utf-8"))
        loaded["rendered_text"] = "被人改过的原文"
        arc.write_text(json.dumps(loaded, ensure_ascii=False), encoding="utf-8")
        text = _render_history(day)
        assert "已被篡改" in text                           # 篡改可见

    def test_history_rejects_bad_date(self, snap_dir):
        from bot.commands.order_sheet import _render_history
        (snap_dir / "archive").mkdir(parents=True)
        assert "非法" in _render_history("../etc/passwd")

    def test_history_missing_day(self, snap_dir):
        from bot.commands.order_sheet import _render_history
        (snap_dir / "archive").mkdir(parents=True)
        assert "无归档快照" in _render_history("2026-01-01")

    def test_history_legacy_archive_no_decision_block(self, snap_dir):
        """旧档无 account_decision——历史命令降级只显示订单，不崩。"""
        from bot.commands.order_sheet import _render_history
        day = (date.today() - timedelta(days=1)).isoformat()
        arc = snap_dir / "archive"
        arc.mkdir(parents=True)
        (arc / f"{day}.json").write_text(json.dumps({
            "snapshot_id": f"{day}_old", "trade_date": day,
            "plan": {"pit_end": "2026-09-10", "blocked": {}},
            "order_sheet": {"orders": []}, "quality": {"warnings": []},
        }, ensure_ascii=False), encoding="utf-8")
        text = _render_history(day)
        assert day in text and "当日无订单" in text


class TestArchiveOverwriteStillOnePerDay:
    def test_same_day_atomic_overwrite(self, snap_dir, monkeypatch):
        _stub_build_deps(monkeypatch)
        import dataclasses
        for tgt in (100, 0):
            d = _decision()
            d.positions[0] = dataclasses.replace(
                d.positions[0], target_quantity=tgt)
            snap = build_snapshot(
                decision=d, rendered_text="原文",
                portfolio_snapshot={"total_cash": 1549.0, "accounts": []},
                order_sheet=None)
            save_snapshot(snap)
        archives = list((snap_dir / "archive").glob("????-??-??.json"))
        assert len(archives) == 1                           # 仍每日一档
        loaded = json.loads(archives[0].read_text(encoding="utf-8"))
        assert loaded["account_decision"]["positions"][0]["target_quantity"] == 0
        assert not list((snap_dir / "archive").glob("*.tmp"))  # 无残留 tmp

# -*- coding: utf-8 -*-
"""①-E 运行健康命令测试：五要素渲染与异常探测"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.commands.health import HealthCommand, _anomaly_lines


def _cmd():
    return HealthCommand()


def _fake_message():
    return type("M", (), {"platform": "test"})()


@pytest.fixture()
def plan_file(tmp_path: Path, monkeypatch) -> Path:
    import bot.commands.health as health_mod

    p = tmp_path / "current_plan.json"
    monkeypatch.setattr(health_mod, "_PLAN_PATH", p)
    return p


class TestHealthCommand:
    def test_all_green_with_fresh_plan(self, plan_file, isolated_db,
                                       monkeypatch):
        today = date.today()
        import src.services.cb_signal_bridge as bridge
        monkeypatch.setattr(bridge, "DEFAULT_PLAN_PATH", plan_file)
        plan_file.write_text(json.dumps({
            "schema": "cb_current_plan_v1", "pit_end": today.isoformat(),
            "capital_base": 10000.0,
            "targets": {"113062.SH": {"weight": 0.2, "t1_close": 128.0}},
        }), encoding="utf-8")
        text = _cmd().execute(_fake_message(), []).text
        assert "运行健康" in text
        assert f"PIT 末端 {today.isoformat()}" in text
        assert "1 只目标" in text
        assert "总线 0 条" in text
        assert "尚未生成" in text
        assert "一切正常" in text
        assert "⚠" not in text

    def test_stale_plan_flagged(self, plan_file, isolated_db):
        stale = (date.today() - timedelta(days=9)).isoformat()
        plan_file.write_text(json.dumps({
            "schema": "cb_current_plan_v1", "pit_end": stale,
            "targets": {}, "capital_base": 10000.0,
        }), encoding="utf-8")
        text = _cmd().execute(_fake_message(), []).text
        assert "超 7 天未更新" in text
        assert "一切正常" not in text

    def test_missing_plan_flagged(self, plan_file, isolated_db):
        text = _cmd().execute(_fake_message(), []).text
        assert "未发现 CB 信号契约" in text
        assert "⚠" in text


class TestAnomalyLines:
    def test_corrupt_plan(self, plan_file, monkeypatch):
        import bot.commands.health as health_mod

        plan_file.write_text("{broken", encoding="utf-8")
        lines = _anomaly_lines(date.today())
        assert any("解析失败" in ln for ln in lines)

    def test_future_plan_not_stale(self, plan_file):
        plan_file.write_text(json.dumps({
            "schema": "cb_current_plan_v1",
            "pit_end": date.today().isoformat(), "targets": {},
        }), encoding="utf-8")
        assert _anomaly_lines(date.today()) == []

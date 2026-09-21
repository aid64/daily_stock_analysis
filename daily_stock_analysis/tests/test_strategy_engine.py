# -*- coding: utf-8 -*-
"""策略部署引擎测试：注册表加载 / 逆回购窗口 / 打新汇总 / 容错降级"""
from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.services import strategy_engine as se
from src.services.strategy_engine import (
    StrategySignal,
    evaluate_strategies,
    load_strategies,
)

# tests/ 在 daily_stock_analysis/ 下：parents[2] 才是仓库根
_REPO_YAML = Path(__file__).resolve().parents[2] / "strategies.yaml"


class TestRegistry:
    def test_repo_yaml_loads(self):
        entries = load_strategies(_REPO_YAML)
        ids = {e["id"] for e in entries}
        assert {"reverse_repo_window", "cb_new_issue",
                "etf_momentum_rotation4"} <= ids

    def test_active_vs_registered_split(self):
        entries = load_strategies(_REPO_YAML)
        active = {e["id"] for e in entries if e.get("status") == "active"}
        registered = {e["id"] for e in entries if e.get("status") == "registered"}
        # 免回测两类 active；中风险全部 registered（不产生信号）
        assert active == {"reverse_repo_window", "cb_new_issue"}
        assert "etf_momentum_rotation4" in registered

    def test_missing_file_degrades_to_empty(self):
        # 不依赖 pytest tmp_path（本机存在权限问题），用确定不存在的路径
        assert load_strategies(Path("N:/nonexistent/strategies.yaml")) == []


def _fake_calendar(trading_days):
    """构造基于固定交易日集合的假日历（跳过 exchange_calendars 依赖）。"""
    def _is_open(day):
        return day in trading_days
    return _is_open


# 交易日集合：2026-09 的周一至周五（跳过 9/30→10/01 国庆起始）
_TRADING = set()
_d = date(2026, 9, 1)
while _d <= date(2026, 10, 15):
    if _d.weekday() < 5 and not (date(2026, 10, 1) <= _d <= date(2026, 10, 8)):
        _TRADING.add(_d)
    _d += timedelta(days=1)


class TestReverseRepoWindow:
    PARAMS = {"product_sh": "GC001", "product_sz": "R-001", "min_cash": 1000}

    def test_friday_weekend_window(self, monkeypatch):
        # 9/18 周五：明日周六非交易日 → 触发
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        ctx = {"today": date(2026, 9, 18), "cash": 9999}
        signals = se.reverse_repo_window(self.PARAMS, ctx)
        assert len(signals) == 1
        assert "GC001" in signals[0].action
        assert "¥9,000" in signals[0].action  # 9999 取 1000 整数倍

    def test_month_end_with_holiday_double_reason(self, monkeypatch):
        # 9/30 周三：10/1 国庆非交易 + 当月最后交易日 → 双理由
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        signals = se.reverse_repo_window(
            self.PARAMS, {"today": date(2026, 9, 30), "cash": 5000})
        assert len(signals) == 1
        assert "非交易日" in signals[0].reason
        assert "最后交易日" in signals[0].reason
        assert "¥5,000" in signals[0].action

    def test_plain_tuesday_no_signal(self, monkeypatch):
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        signals = se.reverse_repo_window(
            self.PARAMS, {"today": date(2026, 9, 22), "cash": 5000})
        assert signals == []

    def test_last_trading_friday_of_month_both_reasons(self, monkeypatch):
        # 月末最后交易日恰逢周五：窗口叠加但只出一条信号
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        signals = se.reverse_repo_window(
            self.PARAMS, {"today": date(2026, 9, 25), "cash": 3000})
        assert len(signals) == 1

    def test_below_min_cash_no_signal(self, monkeypatch):
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        signals = se.reverse_repo_window(
            self.PARAMS, {"today": date(2026, 9, 18), "cash": 800})
        assert signals == []

    def test_amount_floor_to_thousand(self, monkeypatch):
        # 现金 1500 → 金额 1000；现金 1999 → 1000
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        signals = se.reverse_repo_window(
            self.PARAMS, {"today": date(2026, 9, 18), "cash": 1999})
        assert "¥1,000" in signals[0].action


class TestCbNewIssue:
    def test_summarizes_reminders(self, monkeypatch):
        fake = {"bond_code": "123456", "bond_name": "测试转债",
                "stage": "subscribe", "reference_date": "2026-09-19",
                "note": "申购日"}
        monkeypatch.setattr(
            "src.services.bond_new_issue_calendar."
            "BondNewIssueCalendarService.upcoming_reminders",
            lambda self, days=2: [fake])
        signals = se.cb_new_issue({"lookahead_days": 2}, {})
        assert len(signals) == 1
        assert "测试转债" in signals[0].action
        assert "申购" in signals[0].action
        assert signals[0].risk == "near_zero"

    def test_no_reminders_no_signal(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.bond_new_issue_calendar."
            "BondNewIssueCalendarService.upcoming_reminders",
            lambda self, days=2: [])
        assert se.cb_new_issue({"lookahead_days": 2}, {}) == []


class TestEvaluateAll:
    def test_only_active_evaluated(self, monkeypatch):
        # yaml 里 registered 的 etf_momentum 无评估器也绝不会被执行
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        monkeypatch.setattr(
            "src.services.bond_new_issue_calendar."
            "BondNewIssueCalendarService.upcoming_reminders",
            lambda self, days=2: [])
        signals = evaluate_strategies(
            {"today": date(2026, 9, 22), "cash": 5000}, path=_REPO_YAML)
        # 周二无逆回购窗口、无打新 → 无信号
        assert signals == []

    def test_strategy_failure_isolated(self, monkeypatch):
        # 一个评估器抛异常，不影响其余（不依赖 pytest tmp_path）
        import tempfile

        import yaml

        cfg = {
            "strategies": [
                {"id": "bad", "status": "active", "evaluator": "boom",
                 "params": {}},
                {"id": "rr", "status": "active", "evaluator": "reverse_repo_window",
                 "params": {"product_sh": "GC001", "min_cash": 1000}},
                {"id": "unknown", "status": "active", "evaluator": "nope",
                 "params": {}},
            ]
        }
        p = Path(tempfile.mkdtemp()) / "strategies.yaml"
        p.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        def _boom(params, context):
            raise RuntimeError("策略内部错误")

        monkeypatch.setattr(se, "_EVALUATORS", {
            "boom": _boom,
            "reverse_repo_window": se.reverse_repo_window,
        })
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        signals = evaluate_strategies(
            {"today": date(2026, 9, 18), "cash": 5000}, path=p)
        assert len(signals) == 1
        assert signals[0].strategy_id == "reverse_repo_window"

    def test_signal_contract_fields(self, monkeypatch):
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        signals = se.reverse_repo_window(
            {"product_sh": "GC001", "min_cash": 1000},
            {"today": date(2026, 9, 18), "cash": 5000})
        d = signals[0].to_dict()
        assert set(d) == {"strategy_id", "name", "tier", "risk",
                          "action", "reason", "detail"}
        assert d["tier"] == "cash_management"
        assert d["risk"] == "zero"

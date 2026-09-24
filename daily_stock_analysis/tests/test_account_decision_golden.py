# -*- coding: utf-8 -*-
"""账户决策包黄金基线（v4.1 计划 P1 前置）

P1 要把 account_decision_service（915 行）拆成 strategies/ + portfolio_compositor。
拆分前先固化"当前决策包对固定输入的完整输出"为黄金基线——A 类·行为不变部分
拆分后必须逐字节一致；B 类·语义变更（e2 降级/破位改约束）走对照+人工确认，
不进本基线。

固定输入：monkeypatch 掉全部外部依赖（E2 分位/AI 解释/破位诊断/日线），
让 build_account_decision + render_decision_package 成为纯函数，输出确定。
"""
from __future__ import annotations

from typing import Any, Dict

from src.services.account_decision_service import (
    BreachDiagnosis,
    URGENCY_IMMEDIATE,
    build_account_decision,
    decision_to_sheet,
    render_decision_package,
)


def _position(symbol, qty, cost, price, name):
    return {"symbol": symbol, "quantity": qty, "avg_cost": cost,
            "last_price": price, "price_date": "2026-09-14", "name": name}


def _snapshot(positions, cash):
    return {"total_cash": cash, "accounts": [{"positions": positions}]}


RULES = {
    "000899": {"stop_loss": [10.87, 10.65], "take_profit": [11.77, 12.89]},
    "002475": {"stop_loss": [55.07, 53.93], "take_profit": [59.61, 65.29]},
}


def _fixed_decision(monkeypatch):
    """把所有外部依赖钉死，得到确定性决策包。"""
    import src.services.account_decision_service as ads

    monkeypatch.setattr(ads, "compute_e2_percentile", lambda: (0.875, "2026-09-11"))
    # 非交易日门（P4）：钉死为交易日，使挂单边界稳定渲染（黄金基线断言边界行）
    monkeypatch.setattr(
        "src.services.notification_events.is_trading_day",
        lambda market="cn", check=None: True)
    monkeypatch.setattr(
        ads, "_latest_ai_notes",
        lambda symbols, days=3: (
            {"000899": "观望——弱势整理（2026-09-14）",
             "002475": "减仓——空头排列（2026-09-14）"},
            {"000899": "赣能股份", "002475": "立讯精密"}))
    # 破位诊断钉死为 immediate（不依赖本地日线）。P1 拆分后 decide_position
    # 在 strategies.user_rules 里调用 diagnose_breach——patch 该命名空间。
    monkeypatch.setattr(
        "src.services.strategies.user_rules.diagnose_breach",
        lambda **kw: BreachDiagnosis(
            depth_pct=10.5, consecutive_days=22, above_ma_count=0,
            ma_structure="均线纠缠", volume_note="放量破位",
            urgency=URGENCY_IMMEDIATE,
            suggestion="技术面弱势证据充分——建议立即执行全部卖出"))
    snap = _snapshot([
        _position("000899", 100, 11.16, 9.75, "赣能股份"),
        _position("002475", 200, 56.77, 52.78, "立讯精密"),
    ], cash=1549.0)
    decision = build_account_decision(snapshot=snap, rules=RULES)
    sheet = decision_to_sheet(decision, available_cash=1549.0)
    return decision, sheet


# 黄金基线（实测捕获 2026-09-21，非想象）：固定输入下决策包的判决骨架。
# 逐字段断言而非整串比对——避免时间戳/浮点末位造成的脆性（P1 归一化字段）。
# immediate 破位诊断下两只均全卖（target=0）；现金高分位防守持有。
GOLDEN_VERDICTS = {"000899": ("sell", 0), "002475": ("sell", 0)}
GOLDEN_CASH = ("持有", 0.875)


class TestGoldenBaseline:
    def test_decision_skeleton_frozen(self, monkeypatch):
        """判决骨架：每只票的 verdict + target_quantity + 现金决策。
        拆分后此断言必须原样通过（A 类行为不变）。"""
        decision, _ = _fixed_decision(monkeypatch)
        got = {p.symbol: (p.verdict, p.target_quantity) for p in decision.positions}
        assert got == GOLDEN_VERDICTS
        assert decision.cash_verdict == GOLDEN_CASH[0]
        assert abs((decision.e2_percentile or 0) - GOLDEN_CASH[1]) < 1e-9

    def test_render_contains_load_bearing_lines(self, monkeypatch):
        """渲染承载行（卡片式待办，实测捕获）：拆分后不得丢失或改写。"""
        decision, sheet = _fixed_decision(monkeypatch)
        text = render_decision_package(decision, sheet=sheet)
        # 待办卡片标题（动作前置）
        assert "今日待办" in text
        # 000899 立即全卖 100 股 + 限价
        assert "卖出 000899" in text and "100股 @9.75" in text
        # 002475 全卖 200 股（immediate 全卖）
        assert "卖出 002475" in text and "200股" in text
        # 破位诊断依据
        assert "止损执行（immediate）" in text
        # 驱动规则标签（P1.5 发现#6：机制切换可见）
        assert "[止损规则]" in text
        # 依据溯源脚注（结构锚/破位启发式/E2 实验性）
        assert "结构锚生成" in text and "破位分级=启发式" in text
        # 执行闭环指引
        assert "/成交回填" in text

    def test_sheet_orders_match_decision(self, monkeypatch):
        """决策→操作单映射：sell 决策必须产出对应卖单腿（全卖）。"""
        decision, sheet = _fixed_decision(monkeypatch)
        orders = {o.symbol: (o.direction, o.quantity) for o in sheet.orders}
        assert orders.get("000899") == ("sell", 100)
        assert orders.get("002475") == ("sell", 200)

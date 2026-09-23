# -*- coding: utf-8 -*-
"""集中度约束单测（P1 step3）：apply_concentration_targets 的 REDUCE 目标修正。

黄金基线走 immediate 破位（sell），不触发 REDUCE 分支——本文件专门覆盖
集中度减仓路径：有效减仓 / 不足一手退化持有 / 非 REDUCE 不动。
"""
from __future__ import annotations

from src.services.strategies.concentration import apply_concentration_targets
from src.services.strategies.types import (
    PositionDecision, VERDICT_HOLD, VERDICT_REDUCE)


def _dec(symbol, qty, price, verdict, weight_pct=50.0):
    return PositionDecision(
        symbol=symbol, name=symbol, quantity=qty, avg_cost=0.0,
        last_price=price, price_date="2026-09-14", pnl=0.0, pnl_pct=0.0,
        weight_pct=weight_pct, verdict=verdict, target_quantity=qty,
        reason="", rule_state="")


def test_reduce_computes_threshold_bounded_target():
    # total_value=20000, 阈值 35% → 预算市值 7000；002475(lot100) 200股@52.78
    # target = int(7000/52.78/100)*100 = 100（减仓 100 股 = 1 手，有效）
    decisions = [_dec("002475", 200, 52.78, VERDICT_REDUCE)]
    apply_concentration_targets(
        decisions, total_value=20000.0, concentration_threshold_pct=35.0)
    assert decisions[0].verdict == VERDICT_REDUCE
    assert decisions[0].target_quantity == 100


def test_reduce_degrades_to_hold_when_under_one_lot():
    # 预算充裕 → target>=qty，减不出有效手数 → 退化为持有（target=当前量）
    decisions = [_dec("002475", 200, 52.78, VERDICT_REDUCE)]
    apply_concentration_targets(
        decisions, total_value=50000.0, concentration_threshold_pct=35.0)
    assert decisions[0].verdict == VERDICT_HOLD
    assert decisions[0].target_quantity == 200
    assert "不足一手" in decisions[0].reason


def test_non_reduce_untouched():
    hold = _dec("000899", 100, 9.75, VERDICT_HOLD)
    apply_concentration_targets(
        [hold], total_value=20000.0, concentration_threshold_pct=35.0)
    assert hold.verdict == VERDICT_HOLD
    assert hold.target_quantity == 100


def test_no_price_reduce_skipped():
    # last_price<=0（无报价）不参与集中度修正——保持占位
    decisions = [_dec("002475", 200, 0.0, VERDICT_REDUCE)]
    apply_concentration_targets(
        decisions, total_value=20000.0, concentration_threshold_pct=35.0)
    assert decisions[0].verdict == VERDICT_REDUCE
    assert decisions[0].target_quantity == 200


# ── P1.5 账户规模感知（v4.2 发现#5："减仓=清仓"不可执行建议）──────────


def test_reduce_floor_one_lot_when_budget_under_one_lot():
    """预算不足一手 + 多手持仓 → 保底一手分批降低，不再清仓。

    真实账户场景：total=13461，阈值 35% → 预算 ¥4,711；
    002475 一手 ¥5,495（@54.95×100）> 预算 → 旧逻辑 target=0（清仓 200 股），
    新逻辑保底一手：卖 100 留 100，并披露"卖出后仍超阈值"。
    """
    decisions = [_dec("002475", 200, 54.95, VERDICT_REDUCE, weight_pct=81.6)]
    apply_concentration_targets(
        decisions, total_value=13461.0, concentration_threshold_pct=35.0)
    d = decisions[0]
    assert d.verdict == VERDICT_REDUCE
    assert d.target_quantity == 100          # 保底一手，不是 0
    assert "保底一手" in d.reason
    assert "非清仓" in d.reason
    assert "仍将超阈值" in d.reason          # 诚实披露：一手仍超 35%
    assert "稀释" in d.reason


def test_reduce_holds_when_only_one_lot_and_budget_under_one_lot():
    """预算不足一手 + 仅持一手 → 持有 + 稀释路径提示（卖出即清仓，不执行）。"""
    decisions = [_dec("002475", 100, 54.95, VERDICT_REDUCE, weight_pct=81.6)]
    apply_concentration_targets(
        decisions, total_value=7000.0, concentration_threshold_pct=35.0)
    d = decisions[0]
    assert d.verdict == VERDICT_HOLD
    assert d.target_quantity == 100
    assert "已是最小持仓单位" in d.reason
    assert "账户规模过小" in d.reason
    assert "稀释" in d.reason


def test_real_account_002475_regression():
    """真实账户参数回归钉死（2026-09-23）：总值 13461 / 200股@54.95。

    变更前（机械减仓）：REDUCE target=0 → "卖出全部 200 股"（不可执行建议，
    零执行成因之一）。变更后：REDUCE target=100。
    对照记录见 docs/decision-layer-changelog.md。
    """
    decisions = [_dec("002475", 200, 54.95, VERDICT_REDUCE, weight_pct=81.6)]
    apply_concentration_targets(
        decisions, total_value=13461.0, concentration_threshold_pct=35.0)
    assert decisions[0].target_quantity == 100

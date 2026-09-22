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

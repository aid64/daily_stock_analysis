# -*- coding: utf-8 -*-
"""集中度约束（P1）：单票权重超阈值 → 减仓到阈值内的目标数量修正。

user_rules.decide_position 只把超阈值持仓标记为 VERDICT_REDUCE（占位目标=当前量）；
真正的减仓目标数量依赖组合总市值，是账户级信息，因此在这里做二次修正——
按当前总价值近似出"阈值内可持有量"（整手取整），减仓量不足一手则退化为持有。
"""
from __future__ import annotations

from typing import List

from src.services.strategies.types import (
    PositionDecision, VERDICT_HOLD, VERDICT_REDUCE)


def apply_concentration_targets(
    decisions: List[PositionDecision],
    *,
    total_value: float,
    concentration_threshold_pct: float,
) -> List[PositionDecision]:
    """就地修正 REDUCE 决策的 target_quantity（返回同一 list，逐项替换）。

    目标 = 阈值内可持有量（按当前总市值近似，整手取整）。
    目标不足一手或不低于现持仓 → 退化为持有（卖不出有效手数）。
    """
    from src.services.order_sheet_service import lot_size_for

    for i, decision in enumerate(decisions):
        if decision.verdict != VERDICT_REDUCE or decision.last_price <= 0:
            continue
        lot = lot_size_for(decision.symbol)
        budget_mv = total_value * concentration_threshold_pct / 100
        target = int(budget_mv / decision.last_price / lot) * lot
        target = max(0, min(target, decision.quantity))
        # 目标不足一手或不低于现持仓 → 退化为持有（卖不出有效手数）
        if target >= decision.quantity or (decision.quantity - target) < lot:
            decisions[i] = PositionDecision(
                **{**decision.to_dict(),
                   "verdict": VERDICT_HOLD,
                   "target_quantity": decision.quantity,
                   "reason": (f"集中度 {decision.weight_pct:.1f}% 超阈值但减仓量"
                              f"不足一手——持有并提示，无指令")})
        else:
            decisions[i] = PositionDecision(
                **{**decision.to_dict(), "target_quantity": target})
    return decisions

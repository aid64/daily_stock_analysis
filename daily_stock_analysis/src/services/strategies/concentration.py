# -*- coding: utf-8 -*-
"""集中度约束（P1）：单票权重超阈值 → 减仓到阈值内的目标数量修正。

user_rules.decide_position 只把超阈值持仓标记为 VERDICT_REDUCE（占位目标=当前量）；
真正的减仓目标数量依赖组合总市值，是账户级信息，因此在这里做二次修正——
按当前总价值近似出"阈值内可持有量"（整手取整），减仓量不足一手则退化为持有。

P1.5 账户规模感知（v4.2 发现#5）：阈值内预算不足一手时，"减仓到阈值内"
在数学上等于清仓——对重仓标的输出清仓指令既不可执行也不合理（实测 002475：
35% 预算 ¥4,711 < 一手 ¥5,495 → 目标 0 股，很可能是零执行的成因）。
修正：持仓多于一手 → 保底一手分批降低并披露"卖出后仍超阈值"；
持仓仅一手 → 持有并标注"账户过小，需靠加仓其他标的或入金稀释"。
本变更属 B 类语义变更（改变每日判决），对照与留痕见
docs/decision-layer-changelog.md（P0.9 纪律）。
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
    阈值内预算不足一手时走账户规模感知路径（保底一手/持有提示），
    不再输出"减仓=清仓"的不可执行建议。
    """
    from src.services.order_sheet_service import lot_size_for

    for i, decision in enumerate(decisions):
        if decision.verdict != VERDICT_REDUCE or decision.last_price <= 0:
            continue
        lot = lot_size_for(decision.symbol)
        budget_mv = total_value * concentration_threshold_pct / 100
        target = int(budget_mv / decision.last_price / lot) * lot
        target = max(0, min(target, decision.quantity))

        if target < lot:
            # 阈值内预算不足一手：机械减到阈值内 = 清仓（不可执行建议）。
            # 账户规模感知：能降一手是一手，降无可降则持有并说明原因。
            one_lot_mv = lot * decision.last_price
            if decision.quantity <= lot:
                # 仅持一手：卖出至阈值内即清仓，退化为持有 + 稀释路径提示
                decisions[i] = PositionDecision(
                    **{**decision.to_dict(),
                       "verdict": VERDICT_HOLD,
                       "target_quantity": decision.quantity,
                       "reason": (f"集中度 {decision.weight_pct:.1f}% 超阈值"
                                  f"（阈值 {concentration_threshold_pct:.0f}%），"
                                  f"但已是最小持仓单位（一手）——卖出至阈值内"
                                  f"即清仓，不执行；账户规模过小，需靠加仓"
                                  f"其他标的或入金稀释")})
            else:
                # 多手持仓：保底一手分批降低，披露卖出后仍超阈值的事实
                decisions[i] = PositionDecision(
                    **{**decision.to_dict(),
                       "target_quantity": lot,
                       "reason": (f"集中度修复：单票占组合 "
                                  f"{decision.weight_pct:.1f}%（阈值 "
                                  f"{concentration_threshold_pct:.0f}%），但阈值内"
                                  f"预算（¥{budget_mv:,.0f}）不足一手"
                                  f"（¥{one_lot_mv:,.0f}）——保底一手分批降低"
                                  f"（非清仓），卖出后仍将超阈值：账户规模过小，"
                                  f"需靠加仓其他标的或入金稀释")})
        elif target >= decision.quantity or (decision.quantity - target) < lot:
            # 目标不足一手或不低于现持仓 → 退化为持有（卖不出有效手数）
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

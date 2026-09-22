# -*- coding: utf-8 -*-
"""用户价格规则决策（P1）：止损/止盈判定 + 破位诊断分档 + 集中度减仓 →
单持仓 verdict。破位诊断细节在 breach.py，共享类型在 types.py。"""
from __future__ import annotations

import logging
from typing import Dict, List

from src.services.strategies.types import (
    PositionDecision, VERDICT_HOLD, VERDICT_REDUCE, VERDICT_SELL)
from src.services.strategies.breach import (
    URGENCY_HALF, URGENCY_WAIT, diagnose_breach)

logger = logging.getLogger(__name__)


def decide_position(
    *,
    symbol: str,
    name: str,
    quantity: int,
    avg_cost: float,
    price: float,
    price_date: str,
    weight_pct: float,
    rules: Dict[str, List[float]],
    concentration_threshold_pct: float,
    ai_note: str = "",
) -> PositionDecision:
    """单持仓决策：规则 > 集中度 > 持有。"""
    pnl = (price - avg_cost) * quantity if avg_cost > 0 else 0.0
    pnl_pct = ((price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0

    stop_losses = list(rules.get("stop_loss") or [])
    take_profits = list(rules.get("take_profit") or [])

    breached_stops = [s for s in stop_losses if price < s]
    breached_takes = [t for t in take_profits if price > t]

    base = dict(
        symbol=symbol, name=name, quantity=quantity, avg_cost=avg_cost,
        last_price=price, price_date=price_date,
        pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
        weight_pct=round(weight_pct, 2), ai_note=ai_note,
    )

    if breached_stops:
        worst = min(breached_stops)
        depth = (worst - price) / worst * 100 if worst > 0 else 0.0
        levels = "/".join(f"{s:g}" for s in stop_losses)
        breached_text = "/".join(f"{s:g}" for s in breached_stops)
        rule_state_text = (
            f"止损位 {levels}，已跌破 {breached_text}"
            f"（低于最深一档 {depth:.1f}%）"
            if len(breached_stops) > 1 else
            f"止损位 {levels}，已跌破（低 {depth:.1f}%）")
        # 破位诊断：把"跌破=全卖"细化为深度/持续性/均线/量能驱动的分级建议。
        # 诊断建议减半时，目标数量必须真的减半——否则文案说"减半观察"、
        # 订单却全卖，指令与建议自相矛盾（比不给诊断更危险）。
        # WAIT 档同理（审视第 6 条修复）：暂缓一日 = 今日不执行卖出，
        # 不能再落进"全卖"分支。
        diag = diagnose_breach(symbol=symbol, price=price,
                               stop_levels=stop_losses)
        if diag is not None and diag.urgency == URGENCY_WAIT:
            # 暂缓观察：持有不动，明日仍收于止损下方再执行
            return PositionDecision(
                verdict=VERDICT_HOLD, target_quantity=quantity,
                rule_state=rule_state_text,
                reason=f"止损暂缓一日：{diag.suggestion}",
                breach=diag.to_dict(),
                **base)
        target_qty = 0
        reason = "止损执行：止损位已被跌破（价位为系统登记的规则，见依据披露），按规则卖出全部"
        if diag is not None:
            if diag.urgency == URGENCY_HALF:
                from src.services.order_sheet_service import lot_size_for
                lot = lot_size_for(symbol)
                target_qty = (quantity // 2 // lot) * lot
                if target_qty <= 0:
                    # 减半不足一手 → 退回全卖（小仓位无法拆分）
                    target_qty = 0
                    reason = (f"止损执行（{diag.urgency}，仓位不足一手无法拆分）："
                              f"{diag.suggestion}")
                else:
                    reason = (f"止损执行（{diag.urgency}，减半至 {target_qty}"
                              f"）：{diag.suggestion}")
            else:
                reason = f"止损执行（{diag.urgency}）：{diag.suggestion}"
        return PositionDecision(
            verdict=VERDICT_SELL, target_quantity=target_qty,
            rule_state=rule_state_text,
            reason=reason,
            breach=diag.to_dict() if diag is not None else None,
            **base)

    if breached_takes:
        levels = "/".join(f"{t:g}" for t in take_profits)
        return PositionDecision(
            verdict=VERDICT_SELL, target_quantity=0,
            rule_state=f"止盈位 {levels}，已上穿 {breached_takes[0]:g}",
            reason="止盈执行：止盈位已上穿（价位为系统登记的规则，见依据披露），按规则卖出全部",
            **base)

    if weight_pct > concentration_threshold_pct and quantity > 0:
        # 减仓目标：把该票权重压回阈值内（按当前组合市值近似）
        return PositionDecision(
            verdict=VERDICT_REDUCE, target_quantity=quantity,  # 占位，调用方按市值修正
            rule_state=(f"无价格规则触发；集中度 {weight_pct:.1f}% "
                        f"超阈值 {concentration_threshold_pct:.0f}%"),
            reason=(f"集中度修复：单票占组合 {weight_pct:.1f}%（阈值 "
                    f"{concentration_threshold_pct:.0f}%），建议减仓"),
            **base)

    rule_state = (f"价格规则未触发（止损 {levels_text(stop_losses)} · "
                  f"止盈 {levels_text(take_profits)})" if stop_losses or take_profits
                  else "未配置价格规则")
    return PositionDecision(
        verdict=VERDICT_HOLD, target_quantity=quantity,
        rule_state=rule_state,
        reason="无触发条件：规则未破位、集中度在阈值内" if stop_losses or take_profits
               else "无规则且集中度在阈值内——持有观察",
        **base)

def levels_text(levels: List[float]) -> str:
    return "/".join(f"{v:g}" for v in levels) if levels else "未设"

# -*- coding: utf-8 -*-
"""组合层（L2，计划 P1 收尾）：把各策略层输出编排成当日账户决策包。

当前只做 P1 的编排搬家（A 类·行为逐字节不变）：拆持仓 → 逐票规则决策
（user_rules）→ 集中度目标修正（concentration）→ 现金/E2（e2_timing）→
买入观察池 + 策略引擎信号 → 组装 AccountDecision。

冲突裁决的显式优先序（止损/止盈 > 集中度 > 现金管理 > 配置型）与买侧
目标持仓语义属 P2，届时在本模块落地。

注：load_price_rules / _latest_ai_notes / compute_e2_percentile 仍留在
account_decision_service 门面，本模块通过该命名空间调用——既避免循环导入，
又让既有测试的 monkeypatch 目标（ads.*）无需改动。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from src.services.strategies.types import (
    AccountDecision, PositionDecision, StrategyOutput, VERDICT_HOLD)
from src.services.strategies.user_rules import decide_position
from src.services.strategies.concentration import apply_concentration_targets
from src.services.strategies.e2_timing import decide_cash_verdict

logger = logging.getLogger(__name__)


# ── P2：组合器（v4.2 深化设计——优先级归并/容忍带/cadence 排他）────

@dataclass
class ComposeResult:
    """组合器输出：归并后的目标持仓（供 order_sheet 生成调仓单）"""
    targets: Dict[str, int] = field(default_factory=dict)   # symbol→目标数量
    winners: Dict[str, str] = field(default_factory=dict)   # symbol→主导策略 id
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def compose(strategy_outputs: List[StrategyOutput],
            *,
            current_positions: Dict[str, int],
            prices: Dict[str, float],
            tolerance_pct: Optional[float] = None) -> ComposeResult:
    """多策略目标持仓 → 归并后的调仓目标（v4.2 P2 组合器核心）。

    四步（社区调研结论，见计划附录）：
      1. 优先级归并：同一 symbol 多策略给目标时，高优先级（priority 小）
         先占位，低优先级让位并记入 conflicts（社区共识：相加会超配、
         取平均适合无优先级场景——本组合器有显式优先级链）。
      2. 容忍带：|目标-当前| 未出带的标的不进调仓。默认带宽 = 不足一手
         （与 order_sheet 既有"差额不足一手不出单"同口径，行为等价）；
         tolerance_pct 可选声明权重带宽（%总资产，P2-7 买侧启用前保持 None）。
      3. cadence 排他：当日多频触发只保留最高频（再平衡优先，跳过当日
         微调）。当前全部策略 daily——预留实现，首个多 cadence 策略
         接入时激活（诚实边界：不提前实现无消费者的分支）。
      4. 输出 targets → order_sheet 状态机（现有，不变）。

    position_sizer 预算闸门在 decision_to_sheet 前由调用方应用（P2-5）。
    """
    from src.services.order_sheet_service import lot_size_for

    result = ComposeResult()

    # 3. cadence 排他（预留：全 daily 时无操作）
    cadences = {o.cadence for o in strategy_outputs}
    if len(cadences) > 1:
        # 再平衡（weekly/monthly/quarterly）优先于 daily 微调
        keep = {"weekly", "monthly", "quarterly"}
        dropped = [o.strategy_id for o in strategy_outputs
                   if o.cadence == "daily" and cadences & keep]
        if dropped:
            strategy_outputs = [o for o in strategy_outputs
                                if o.strategy_id not in dropped]
            result.notes.append(
                f"cadence 排他：当日保留再平衡，跳过日频微调 {dropped}")

    # 1. 优先级归并（priority 升序 = 高优先级先占位）
    ordered = sorted(strategy_outputs, key=lambda o: o.priority)
    for out in ordered:
        for leg in out.legs:
            if leg.symbol in result.targets:
                result.conflicts.append({
                    "symbol": leg.symbol,
                    "winner": result.winners[leg.symbol],
                    "loser": out.strategy_id,
                    "loser_target": leg.target_quantity,
                })
                continue
            result.targets[leg.symbol] = leg.target_quantity
            result.winners[leg.symbol] = out.strategy_id

    # 2. 容忍带：目标与当前一致或差额不足一手 → 不动（从 targets 剔除）
    for symbol in list(result.targets):
        current = int(current_positions.get(symbol) or 0)
        target = result.targets[symbol]
        if target == current:
            del result.targets[symbol]
            continue
        lot = lot_size_for(symbol)
        if abs(target - current) < lot:
            # 差额不足一手：与 order_sheet 既有语义一致——不出单，
            # 但保留在 targets 供披露（order_sheet 侧同样过滤）
            result.notes.append(
                f"{symbol} 目标 {target} 与当前 {current} 差额不足一手，不动")
    return result


def apply_budget_gate(result: "ComposeResult",
                      *,
                      current_positions: Dict[str, int],
                      prices: Dict[str, float],
                      account_equity: float,
                      sizer: Any = None) -> "ComposeResult":
    """P2-5：position_sizer 预算闸门——只约束**买入**目标（target > 当前）。

    卖出/减仓不受预算约束（持有多少都能卖）；买入目标受三层仓位管理的
    单票上限（max_position_pct）与总敞口上限（max_total_exposure_pct）
    双重裁剪，整手取整。

    当前买侧只读（观察池不落单，见 P2-7），故正常无买入腿——闸门空转。
    买侧授权后（strategies.yaml buy_intent）买入腿出现，闸门自动生效。
    就地修正 result.targets 并把裁剪动作记入 notes；返回同一对象。
    """
    from src.services.order_sheet_service import lot_size_for

    if sizer is None:
        from src.services.position_sizer import build_position_sizer_from_config
        sizer = build_position_sizer_from_config()

    if account_equity <= 0:
        return result

    max_pos_pct = float(sizer.config.max_position_pct)
    max_total_pct = float(sizer.config.max_total_exposure_pct)

    # 当前已投入市值（用于总敞口余量计算）
    current_exposure = sum(
        int(q or 0) * float(prices.get(s) or 0)
        for s, q in current_positions.items())
    total_budget = account_equity * max_total_pct / 100.0
    remaining_budget = max(0.0, total_budget - current_exposure)

    single_cap_value = account_equity * max_pos_pct / 100.0

    for symbol in list(result.targets):
        current = int(current_positions.get(symbol) or 0)
        target = result.targets[symbol]
        if target <= current:
            continue  # 卖出/减仓/持平——不受预算约束
        price = float(prices.get(symbol) or 0)
        if price <= 0:
            continue
        lot = lot_size_for(symbol)
        buy_value = (target - current) * price

        # 单票上限：目标持仓市值（现有+新增）不超单票 cap
        target_value = target * price
        capped = False
        if target_value > single_cap_value:
            allowed_target_qty = int(single_cap_value / price / lot) * lot
            allowed_target_qty = max(current, allowed_target_qty)
            if allowed_target_qty < target:
                result.targets[symbol] = allowed_target_qty
                target = allowed_target_qty
                buy_value = (target - current) * price
                capped = True

        # 总敞口上限：本次买入金额不超剩余预算
        if buy_value > remaining_budget + 1e-9:
            affordable_qty = int(remaining_budget / price / lot) * lot
            new_target = current + max(0, affordable_qty)
            if new_target < target:
                result.targets[symbol] = new_target
                buy_value = (new_target - current) * price
                capped = True

        if capped:
            result.notes.append(
                f"{symbol} 买入受 position_sizer 闸门裁剪 → 目标 "
                f"{result.targets[symbol]}（单票≤{max_pos_pct:g}% · "
                f"总敞口≤{max_total_pct:g}%）")
            if result.targets[symbol] == current:
                del result.targets[symbol]
                continue
        remaining_budget = max(0.0, remaining_budget - buy_value)

    return result


def compose_account_decision(
    *,
    snapshot: Dict[str, Any],
    rules: Optional[Dict[str, Dict[str, List[float]]]] = None,
    concentration_threshold_pct: float = 35.0,
    include_e2: bool = True,
) -> AccountDecision:
    """从持仓快照编排账户决策包（详见 account_decision_service.build_account_decision）。"""
    # helper 经门面命名空间调用，保持测试 monkeypatch 目标不变（见模块 docstring）
    from src.services import account_decision_service as ads

    trade_date = date.today().isoformat()
    rules = rules if rules is not None else ads.load_price_rules()

    # U0 规则门控（台账最新签署为准，fail-open）：
    # R1 停用 → 决策引擎拿不到止损/止盈位（各票退化为 wait/hold 语义）
    from src.services.rule_confirmation_service import is_rule_enabled
    if not is_rule_enabled("R1"):
        rules = {}

    # 1. 拆持仓（股票为主体；转债若有真实持仓也走同一规则引擎）
    positions_raw: List[Dict[str, Any]] = []
    cash = float(snapshot.get("total_cash") or 0)
    for account in snapshot.get("accounts") or []:
        for position in account.get("positions") or []:
            qty = float(position.get("quantity") or 0)
            if qty > 0:
                positions_raw.append(position)

    total_mv = sum(
        float(p.get("last_price") or 0) * float(p.get("quantity") or 0)
        for p in positions_raw)
    total_value = total_mv + cash

    symbols = [str(p.get("symbol")) for p in positions_raw]
    ai_notes, names = ads._latest_ai_notes(symbols)

    decisions: List[PositionDecision] = []
    concentration_on = is_rule_enabled("R2")
    for position in sorted(positions_raw, key=lambda p: str(p.get("symbol"))):
        symbol = str(position.get("symbol"))
        quantity = int(float(position.get("quantity") or 0))
        price = float(position.get("last_price") or 0)
        if price <= 0:
            # 无报价：不能定价的持仓不给指令（与旧操作单"缺 T-1 价"语义一致）
            decisions.append(PositionDecision(
                symbol=symbol, name=names.get(symbol) or symbol,
                quantity=quantity,
                avg_cost=float(position.get("avg_cost") or 0),
                last_price=0.0, price_date="", pnl=0.0, pnl_pct=0.0,
                weight_pct=0.0, verdict=VERDICT_HOLD, target_quantity=quantity,
                rule_state="无有效报价", reason="无法定价——人工核实后处理",
                ai_note=ai_notes.get(symbol, "")))
            continue
        weight = (price * quantity / total_value * 100) if total_value > 0 else 0.0
        decisions.append(decide_position(
            symbol=symbol,
            name=names.get(symbol) or symbol,
            quantity=quantity,
            avg_cost=float(position.get("avg_cost") or 0),
            price=price,
            price_date=str(position.get("price_date") or ""),
            weight_pct=weight,
            rules=rules.get(symbol) or {},
            concentration_threshold_pct=concentration_threshold_pct,
            concentration_enabled=concentration_on,
            ai_note=ai_notes.get(symbol, "")))

    # 2. 减仓数量修正：目标 = 阈值内可持有量（strategies/concentration.py）
    # U0：R2（集中度）停用 → 跳过整层修正
    if is_rule_enabled("R2"):
        apply_concentration_targets(
            decisions,
            total_value=total_value,
            concentration_threshold_pct=concentration_threshold_pct)

    # 3. 现金决策（E2 择时层——strategies/e2_timing.py）
    # U0：R3（E2 门）停用 → 现金决策退化为中性（不取分位）
    e2_pct, e2_as_of = (
        ads.compute_e2_percentile()
        if (include_e2 and is_rule_enabled("R3")) else (None, ""))
    cash_weight = (cash / total_value * 100) if total_value > 0 else 0.0
    cash_verdict, cash_reason = decide_cash_verdict(e2_pct, e2_as_of)

    # 4. 买入观察池（观点层：候选池打分，展示不落单——闭合"卖出的钱去哪"）
    buy_candidates: List[Dict[str, Any]] = []
    buy_source_note = ""
    try:
        from src.services.buy_watchlist_service import build_buy_watchlist
        watchlist = build_buy_watchlist(exclude_symbols=symbols)
        buy_candidates = watchlist.get("candidates") or []
        buy_source_note = watchlist.get("source_note") or ""
    except Exception as exc:  # noqa: BLE001 - 观察池缺失不影响主决策
        logger.warning("[account-decision] 买入观察池构建失败: %r", exc)
        buy_source_note = f"买入观察池构建失败：{exc}"

    # 5. 策略引擎信号（active 策略：逆回购窗口/打新汇总——docs/strategy-catalog.md）
    strategy_signals: List[Dict[str, Any]] = []
    try:
        from src.services.strategy_engine import evaluate_strategies
        signals = evaluate_strategies({"today": date.today(), "cash": cash})
        strategy_signals = [s.to_dict() for s in signals]
    except Exception as exc:  # noqa: BLE001 - 策略信号缺失不影响主决策
        logger.warning("[account-decision] 策略引擎评估失败: %r", exc)

    return AccountDecision(
        trade_date=trade_date,
        positions=decisions,
        cash=round(cash, 2),
        cash_weight_pct=round(cash_weight, 2),
        cash_verdict=cash_verdict,
        cash_reason=cash_reason,
        e2_percentile=e2_pct,
        e2_as_of=e2_as_of,
        total_mv=round(total_mv, 2),
        concentration_threshold_pct=concentration_threshold_pct,
        buy_candidates=buy_candidates,
        buy_source_note=buy_source_note,
        strategy_signals=strategy_signals,
    )

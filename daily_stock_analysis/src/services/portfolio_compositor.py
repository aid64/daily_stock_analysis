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
from datetime import date
from typing import Any, Dict, List, Optional

from src.services.strategies.types import (
    AccountDecision, PositionDecision, VERDICT_HOLD)
from src.services.strategies.user_rules import decide_position
from src.services.strategies.concentration import apply_concentration_targets
from src.services.strategies.e2_timing import decide_cash_verdict

logger = logging.getLogger(__name__)


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
            ai_note=ai_notes.get(symbol, "")))

    # 2. 减仓数量修正：目标 = 阈值内可持有量（strategies/concentration.py）
    apply_concentration_targets(
        decisions,
        total_value=total_value,
        concentration_threshold_pct=concentration_threshold_pct)

    # 3. 现金决策（E2 择时层——strategies/e2_timing.py）
    e2_pct, e2_as_of = ads.compute_e2_percentile() if include_e2 else (None, "")
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

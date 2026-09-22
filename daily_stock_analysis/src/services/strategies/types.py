# -*- coding: utf-8 -*-
"""账户决策共享类型与常量（P1：下沉，供 strategies/* 与门面共用，避免循环依赖）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


VERDICT_SELL = "sell"        # 规则触发（止损/止盈）→ 清仓
VERDICT_REDUCE = "reduce"    # 集中度超限 → 减仓到阈值内
VERDICT_HOLD = "hold"        # 无触发 → 持有（含理由）

VERDICT_LABELS = {
    VERDICT_SELL: "卖出",
    VERDICT_REDUCE: "减仓",
    VERDICT_HOLD: "持有",
}

@dataclass(frozen=True)
class PositionDecision:
    """单只真实持仓的当日决策"""
    symbol: str
    name: str
    quantity: int
    avg_cost: float
    last_price: float
    price_date: str            # 报价日期（实时为空串）
    pnl: float                 # 浮动盈亏（元）
    pnl_pct: float             # 浮动盈亏（%）
    weight_pct: float          # 占组合权重（含现金）
    verdict: str               # sell / reduce / hold
    target_quantity: int       # 目标数量（sell=0, hold=当前, reduce=计算值）
    reason: str                # 决策依据（一行，规则优先）
    rule_state: str            # 规则状态描述（已跌破/未触发/无规则）
    ai_note: str = ""          # LLM 解释（一句话 + 日期，可空）
    breach: Optional[Dict[str, Any]] = None   # 破位诊断（规则触发时才有）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

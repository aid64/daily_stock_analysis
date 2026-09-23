# -*- coding: utf-8 -*-
"""账户决策共享类型与常量（P1：下沉，供 strategies/* 与门面共用，避免循环依赖）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
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


@dataclass
class AccountDecision:
    """当日账户决策包"""
    trade_date: str
    positions: List[PositionDecision] = field(default_factory=list)
    cash: float = 0.0
    cash_weight_pct: float = 0.0
    cash_verdict: str = ""     # 现金决策（持有/配置）
    cash_reason: str = ""
    e2_percentile: Optional[float] = None   # None = 择时不可用
    e2_as_of: str = ""
    total_mv: float = 0.0
    # 单票集中度阈值（%）：披露块必须显示引擎实际用的阈值，不能写死 35
    concentration_threshold_pct: float = 35.0
    # 买入观察池（观点非信号——未回测验证，只展示不落单）
    buy_candidates: List[Dict[str, Any]] = field(default_factory=list)
    buy_source_note: str = ""
    # 策略引擎信号（active 策略：逆回购窗口/打新汇总等，见 docs/strategy-catalog.md）
    strategy_signals: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "positions": [p.to_dict() for p in self.positions],
            "cash": round(self.cash, 2),
            "cash_weight_pct": round(self.cash_weight_pct, 2),
            "cash_verdict": self.cash_verdict,
            "cash_reason": self.cash_reason,
            "e2_percentile": (round(self.e2_percentile, 4)
                              if self.e2_percentile is not None else None),
            "e2_as_of": self.e2_as_of,
            "total_mv": round(self.total_mv, 2),
            "concentration_threshold_pct": round(self.concentration_threshold_pct, 2),
            "buy_candidates": list(self.buy_candidates),
            "buy_source_note": self.buy_source_note,
            "strategy_signals": list(self.strategy_signals),
        }

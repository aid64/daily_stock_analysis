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


# ── P2：策略统一输出契约（v4.2 B2——一切策略经此进入组合器）───────

# 优先级链（v4.2 P2 深化设计#1：冲突时高优先级先满足，数字越小越优先。
# 与社区共识对齐：离场 > 风控 > 现金管理（无风险）> 配置型（卫星））
TIER_EXIT_RULE = "exit_rule"           # 止损/止盈（用户价格规则）
TIER_RISK_CONTROL = "risk_control"     # 组合集中度
TIER_CASH_MANAGEMENT = "cash_management"  # 逆回购/打新/E2 防守
TIER_SATELLITE = "satellite"           # 配置型策略（未来）

TIER_PRIORITY = {
    TIER_EXIT_RULE: 10,
    TIER_RISK_CONTROL: 20,
    TIER_CASH_MANAGEMENT: 30,
    "low_risk": 35,             # 低风险核心仓（注册表风险谱系档，先于纯卫星）
    TIER_SATELLITE: 40,
}


@dataclass(frozen=True)
class TargetLeg:
    """目标持仓的一条腿（订单驱动语义）"""
    symbol: str
    target_quantity: int       # 目标数量（sell=0 / hold=当前 / reduce=计算值）
    weight_pct: float = 0.0    # 目标权重（% 总资产，含现金；0=不声明）
    constraint: str = ""       # 该腿约束满足情况（如"预算不足一手保底"）


@dataclass(frozen=True)
class StrategyOutput:
    """策略统一输出契约（P2，v4.2 发现#3 的清偿——P1 三模块异构输出
    （PositionDecision/原地改 list/Tuple）与 strategy_engine 纯展示
    StrategySignal 归并为此一契约）。

    语义：策略输出**目标持仓**（B2——买入=达成目标，卖出=达成目标，
    同源）；组合器对比目标 vs 当前账户 → 调仓单。
    """
    strategy_id: str
    name: str
    tier: str                   # TIER_* 之一（决定归并优先级）
    cadence: str                # daily / weekly / monthly / quarterly / event
    legs: List[TargetLeg] = field(default_factory=list)
    cash_action: str = ""       # 现金操作建议（"操作 GC001 ¥1,000"等）
    reason: str = ""
    constraint_notes: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def priority(self) -> int:
        return TIER_PRIORITY.get(self.tier, TIER_PRIORITY[TIER_SATELLITE])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id, "name": self.name,
            "tier": self.tier, "priority": self.priority,
            "cadence": self.cadence,
            "legs": [asdict(leg) for leg in self.legs],
            "cash_action": self.cash_action, "reason": self.reason,
            "constraint_notes": list(self.constraint_notes),
            "meta": dict(self.meta),
        }

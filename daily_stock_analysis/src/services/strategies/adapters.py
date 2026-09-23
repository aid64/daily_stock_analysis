# -*- coding: utf-8 -*-
"""P2 适配层：现有策略模块 → StrategyOutput 统一契约（v4.2 发现#3 清偿）。

设计边界（诚实工程，不假装一步到位）：
  - 不改各模块既有函数签名——黄金基线与测试 monkeypatch 依赖它们；
  - 本步只统一**输出形态**：user_rules/concentration/e2_timing 的既有
    编排产物（PositionDecision 列表）与 strategy_engine 的展示型
    StrategySignal 都适配为 StrategyOutput；
  - 编排顺序不变（compose_account_decision 仍是既有流程），P2-4 的
    compose() 才切换为消费 StrategyOutput 列表做优先级归并。
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.services.strategies.types import (
    PositionDecision, StrategyOutput, TargetLeg,
    TIER_CASH_MANAGEMENT, TIER_EXIT_RULE, TIER_RISK_CONTROL,
    VERDICT_REDUCE, VERDICT_SELL)


def user_rules_output(decisions: List[PositionDecision]) -> StrategyOutput:
    """用户价格规则（止损/止盈）→ exit_rule 层输出。

    从既有逐票决策中抽取 SELL 腿（止损/止盈触发，含破位诊断降档后的
    减半目标）；WAIT 暂缓与 HOLD 不产腿（今日不执行 = 无目标变更）。
    """
    legs = [
        TargetLeg(
            symbol=d.symbol,
            target_quantity=d.target_quantity,
            weight_pct=d.weight_pct,
            constraint=d.rule_state,
        )
        for d in decisions if d.verdict == VERDICT_SELL
    ]
    return StrategyOutput(
        strategy_id="user_rules", name="用户价格规则（止损/止盈）",
        tier=TIER_EXIT_RULE, cadence="daily", legs=legs,
        reason="；".join(d.reason for d in decisions
                         if d.verdict == VERDICT_SELL)[:200],
        constraint_notes=[d.rule_state for d in decisions
                          if d.verdict == VERDICT_SELL],
        meta={"breach": {d.symbol: d.breach for d in decisions
                         if d.verdict == VERDICT_SELL and d.breach}},
    )


def concentration_output(decisions: List[PositionDecision]) -> StrategyOutput:
    """组合集中度 → risk_control 层输出（REDUCE 腿，P1.5 保底一手语义）。"""
    legs = [
        TargetLeg(
            symbol=d.symbol,
            target_quantity=d.target_quantity,
            weight_pct=d.weight_pct,
            constraint=d.reason,       # 保底一手/稀释提示即约束说明
        )
        for d in decisions if d.verdict == VERDICT_REDUCE
    ]
    return StrategyOutput(
        strategy_id="concentration", name="组合集中度",
        tier=TIER_RISK_CONTROL, cadence="daily", legs=legs,
        reason="；".join(d.reason for d in decisions
                         if d.verdict == VERDICT_REDUCE)[:200],
    )


def e2_timing_output(e2_percentile: Any, e2_as_of: str,
                     cash_verdict: str, cash_reason: str) -> StrategyOutput:
    """E2 估值择时 → cash_management 层输出（防守约束，不产持仓腿）。"""
    return StrategyOutput(
        strategy_id="e2_timing", name="E2 估值择时",
        tier=TIER_CASH_MANAGEMENT, cadence="daily", legs=[],
        cash_action="" if cash_verdict != "持有" else "现金防守（不新增买入）",
        reason=cash_reason,
        meta={"e2_percentile": e2_percentile, "e2_as_of": e2_as_of,
              "cash_verdict": cash_verdict},
    )


def signal_to_output(signal: Any) -> StrategyOutput:
    """strategy_engine 展示型 StrategySignal → StrategyOutput（现金操作语义）。

    现金管理层（逆回购/打新）不产持仓腿，操作建议进 cash_action——
    执行仍由用户在券商完成，组合器不为其生成订单腿。
    """
    return StrategyOutput(
        strategy_id=signal.strategy_id, name=signal.name,
        tier=signal.tier, cadence="daily", legs=[],
        cash_action=signal.action, reason=signal.reason,
        meta={"risk": signal.risk, "detail": signal.detail,
              "render": signal.to_dict()},
    )


def decisions_to_outputs(decisions: List[PositionDecision], *,
                         e2_percentile: Any = None, e2_as_of: str = "",
                         cash_verdict: str = "", cash_reason: str = "",
                         engine_signals: List[Any] | None = None,
                         ) -> List[StrategyOutput]:
    """compositor 既有编排产物 → 全套 StrategyOutput（P2-4 compose 的输入）。

    一处组装、多处复用：compose_account_decision 编排完成后调用本函数，
    即得到与当日决策包一致的策略输出列表（含 strategy_engine 现金策略）。
    """
    outputs = [
        user_rules_output(decisions),
        concentration_output(decisions),
        e2_timing_output(e2_percentile, e2_as_of, cash_verdict, cash_reason),
    ]
    for s in engine_signals or []:
        outputs.append(signal_to_output(s))
    return outputs

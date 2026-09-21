# -*- coding: utf-8 -*-
"""策略部署引擎：统一注册表加载 + active 策略评估（重构第一步）

设计（对齐 docs/strategy-catalog.md 状态机）：
  - strategies.yaml（仓库根）是策略参数唯一真源——改参数不改代码；
  - 本引擎只评估 status=active 的策略，输出统一的 StrategySignal；
  - registered/shadow 的策略不产生信号（管道目标，见目录文档）；
  - 每条信号带 tier/risk 标注，决策包按"现金操作"渲染——
    铁律不变：策略给建议，人是执行引擎。

首批 active 策略（免回测的两类，目录文档第三节）：
  1. reverse_repo_window —— 国债逆回购窗口（无风险利率）
  2. cb_new_issue —— 转债打新汇总（规则性正期望，复用
     bond_new_issue_calendar 的扫描结果做盘前汇总，不重复推送）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 仓库根（strategies.yaml 所在）：本文件在
# daily_stock_analysis/src/services/ 下，parents[3] 为仓库根
_STRATEGIES_PATH = Path(__file__).resolve().parents[3] / "strategies.yaml"


@dataclass
class StrategySignal:
    """单条策略信号（统一契约，供决策包渲染）"""
    strategy_id: str
    name: str
    tier: str           # cash_management / low_risk / satellite
    risk: str           # zero / near_zero / bounded / mid / high
    action: str         # 一句话操作（如"操作 GC001 逆回购 ¥9,000"）
    reason: str         # 触发原因（如"明日休市，节前最后交易日"）
    detail: str = ""    # 补充细节（可选）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id, "name": self.name,
            "tier": self.tier, "risk": self.risk,
            "action": self.action, "reason": self.reason,
            "detail": self.detail,
        }


def load_strategies(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读 strategies.yaml；缺失/损坏时返回空表并告警（不阻塞决策）。"""
    p = path or _STRATEGIES_PATH
    try:
        import yaml

        with p.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        entries = data.get("strategies") or []
        return [e for e in entries if isinstance(e, dict)]
    except Exception as exc:  # noqa: BLE001 - 注册表不可读 = 无策略信号
        logger.warning("[strategy-engine] strategies.yaml 读取失败: %r", exc)
        return []


# ── 评估器注册表：yaml 的 evaluator 字段 → 本模块函数 ──────────


def _is_cn_trading_day(day: Any) -> bool:
    """交易日判断（复用 trading_calendar；失败时保守返回 True=不打扰）。"""
    try:
        from src.core.trading_calendar import is_market_open

        return bool(is_market_open("cn", day))
    except Exception as exc:  # noqa: BLE001 - 日历不可用不阻塞
        logger.warning("[strategy-engine] 交易日历不可用: %r", exc)
        return True


def reverse_repo_window(params: Dict[str, Any],
                        context: Dict[str, Any]) -> List[StrategySignal]:
    """国债逆回购窗口（无风险利率，免回测）。

    触发条件（任一）：
      - 次日非交易日（节假日前最后交易日 → 节前窗口）
      - 今日为当月最后交易日（月末窗口）
    """
    from datetime import date, timedelta

    today: date = context.get("today") or date.today()
    cash = float(context.get("cash") or 0)
    out: List[StrategySignal] = []

    if cash < float(params.get("min_cash") or 1000):
        return out

    tomorrow = today + timedelta(days=1)
    reasons: List[str] = []
    if not _is_cn_trading_day(tomorrow):
        reasons.append(f"明日非交易日（{tomorrow.isoformat()}）——今日操作 1 天期"
                        "可赚占款天数利息（周五/节前均跨日计息）")
    # 月末：今天有交易但明天起的下一个交易日已跨月
    next_trading = tomorrow
    for _ in range(7):
        if _is_cn_trading_day(next_trading):
            break
        next_trading += timedelta(days=1)
    if next_trading.month != today.month and _is_cn_trading_day(today):
        reasons.append("今日为本月最后交易日（月末资金价格窗口）")
    if not reasons:
        return out

    # 金额：1000 的整数倍，留 0 尾数
    amount = int(cash // 1000) * 1000
    if amount < 1000:
        return out
    out.append(StrategySignal(
        strategy_id="reverse_repo_window",
        name="国债逆回购窗口",
        tier="cash_management", risk="zero",
        action=f"操作 {params.get('product_sh', 'GC001')} 逆回购 ¥{amount:,}"
               f"（收盘前完成，注意尾盘利率跳水，14:30-15:00 间出手）",
        reason="；".join(reasons),
        detail="无风险利率（国债质押+中登担保）；到期自动回款，"
               "到期当日资金可用（不影响次日打新缴款）",
    ))
    return out


def cb_new_issue(params: Dict[str, Any],
                 context: Dict[str, Any]) -> List[StrategySignal]:
    """转债打新汇总（复用 bond_new_issue_calendar 的扫描，盘前汇总不重复推送）。"""
    out: List[StrategySignal] = []
    try:
        from src.services.bond_new_issue_calendar import (
            BondNewIssueCalendarService)

        service = BondNewIssueCalendarService()
        days = int(params.get("lookahead_days") or 2)
        reminders = service.upcoming_reminders(days=days)
    except Exception as exc:  # noqa: BLE001 - 打新日历失败不阻塞决策包
        logger.warning("[strategy-engine] 打新日历查询失败: %r", exc)
        return out

    if not reminders:
        return out

    lines = []
    for r in reminders:
        stage_text = {"subscribe": "申购", "payment": "缴款",
                      "listing": "上市卖出", "rights": "配债登记"}.get(
                          r.get("stage"), r.get("stage"))
        ref = r.get("reference_date") or ""
        lines.append(f"{r.get('bond_code')} {r.get('bond_name')}"
                     f"（{stage_text}{(' ' + ref) if ref else ''}）")
    out.append(StrategySignal(
        strategy_id="cb_new_issue",
        name="转债打新",
        tier="cash_management", risk="near_zero",
        action="按打新提醒操作：" + "；".join(lines),
        reason="信用申购零成本（中签才缴款）；2026 年新债首日平均涨幅 "
               "52.95%（45 只中 32 只顶格），上市首日 14:50 前卖出落袋",
        detail="打新细节以盘中独立推送为准（本汇总不重复推送，只做盘前清单）",
    ))
    return out


_EVALUATORS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]],
                                List[StrategySignal]]] = {
    "reverse_repo_window": reverse_repo_window,
    "cb_new_issue": cb_new_issue,
}


def evaluate_strategies(context: Dict[str, Any],
                        path: Optional[Path] = None) -> List[StrategySignal]:
    """评估所有 active 策略。单个策略失败只降级该策略，不影响其余。"""
    signals: List[StrategySignal] = []
    for entry in load_strategies(path):
        if str(entry.get("status") or "").lower() != "active":
            continue
        evaluator = _EVALUATORS.get(str(entry.get("evaluator") or ""))
        if evaluator is None:
            logger.warning("[strategy-engine] 未注册的评估器: %r（跳过）",
                           entry.get("evaluator"))
            continue
        try:
            signals.extend(evaluator(entry.get("params") or {}, context))
        except Exception as exc:  # noqa: BLE001 - 单策略失败不拖垮其余
            logger.warning("[strategy-engine] 策略 %s 评估失败: %r",
                           entry.get("id"), exc)
    return signals

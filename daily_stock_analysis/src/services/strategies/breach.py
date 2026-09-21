# -*- coding: utf-8 -*-
"""破位诊断（P1 从 account_decision_service 抽出的独立策略单元）。

规则触发"该考虑卖"之后，判断"多急、卖多少"——深度/持续性/均线/量能 → 分级。
这是一组未经回测验证的启发式（见 docs/decision-layer-changelog.md，P0.9 回放
显示分档在真实数据上几乎不改判决、价位锚 window 才是开关）。阈值提为
BreachThresholds 可注入扰动。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


URGENCY_IMMEDIATE = "immediate"   # 立即执行
URGENCY_HALF = "half"             # 减半观察
URGENCY_WAIT = "wait"             # 暂缓一日观察

@dataclass(frozen=True)
class BreachThresholds:
    """破位分档阈值（P0.9：从硬编码提为可注入参数，供回放/敏感性扰动）。

    默认值 = 提取前的硬编码值，`diagnose_breach(thresholds=None)` 行为逐字节不变。
    这些是**未经回测验证的启发式结构值**（见 docs/account-decision-rules.md 第四节、
    decision-layer-changelog.md）——改动须走 P0.9 回放对照，不得静默调参。
    """
    deep_pct: float = 5.0            # 深破位（低于最深止损档的百分比）
    shallow_pct: float = 2.0         # 浅破位（暂缓档上限）
    persist_days: int = 5            # 持续破位天数（达此值判立即）
    volume_spike: float = 1.3        # 放量倍数（近5日/近20日均量）
    volume_shrink: float = 0.7       # 缩量倍数
    fresh_days: int = 1              # 首日降档：连续≤此值时立即降为减半
    wait_max_consecutive: int = 2    # 暂缓档允许的最大连续破位天数

_DEFAULT_BREACH_THRESHOLDS = BreachThresholds()

@dataclass(frozen=True)
class BreachDiagnosis:
    """破位诊断：规则触发"该考虑卖"之后，判断"多急、卖多少"。

    基于本地日线（无需网络）计算，作为规则执行的**辅助判断**，不覆盖规则。
    注意：这是一组未经验证的启发式（见 docs/account-decision-rules.md 第四节），
    用于把"跌破=全卖"的二元输出细化为可解释的分级建议，不作为独立策略。
    """
    depth_pct: float          # 破位深度：现价低于最深止损档的百分比
    consecutive_days: int     # 连续收盘低于最深止损档的天数
    above_ma_count: int       # 现价站上的均线条数（0-3，MA5/10/20）
    ma_structure: str         # 多头排列 / 空头排列 / 均线纠缠
    volume_note: str          # 放量破位 / 缩量破位 / 量能平稳
    urgency: str              # immediate / half / wait
    suggestion: str           # 面向用户的一行执行建议

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

def _load_daily_bars(symbol: str, days: int = 90) -> List[Tuple[str, float, float]]:
    """本地日线（日期, 收盘, 成交量）。读不到返回空列表——不联网。"""
    try:
        from datetime import timedelta

        from src.repositories.stock_repo import StockRepository

        end = date.today()
        start = end - timedelta(days=days)
        rows = StockRepository().get_range(symbol, start, end)
        bars: List[Tuple[str, float, float]] = []
        for row in rows or []:
            try:
                close = float(row.close)
                if close <= 0:
                    continue
                bars.append((str(row.date), close,
                             float(getattr(row, "volume", 0) or 0)))
            except (TypeError, ValueError):
                continue
        return bars
    except Exception as exc:  # noqa: BLE001 - 诊断失败不阻塞决策
        logger.warning("[account-decision] 日线读取失败 %s: %r", symbol, exc)
        return []

def _moving_average(closes: List[float], window: int) -> Optional[float]:
    if len(closes) < window or window <= 0:
        return None
    return sum(closes[-window:]) / window

def diagnose_breach(
    *,
    symbol: str,
    price: float,
    stop_levels: List[float],
    bars: Optional[List[Tuple[str, float, float]]] = None,
    thresholds: Optional[BreachThresholds] = None,
) -> Optional[BreachDiagnosis]:
    """破位诊断：深度 / 持续性 / 均线结构 / 量能 → 分级紧急度。

    Args:
        bars: 可选，测试/回放可注入；缺省从本地日线读取。
        thresholds: 可选，回放/敏感性扰动可注入；缺省用默认结构值（行为不变）。
    Returns:
        无有效数据或未破位时返回 None（调用方按原始规则处理）。
    """
    th = thresholds or _DEFAULT_BREACH_THRESHOLDS
    if not stop_levels or price <= 0:
        return None
    deepest = min(stop_levels)

    bars = _load_daily_bars(symbol) if bars is None else bars
    if not bars:
        return None

    closes = [c for _, c, _ in bars]
    volumes = [v for _, _, v in bars]

    # 1. 破位深度
    depth_pct = (deepest - price) / deepest * 100 if deepest > 0 else 0.0

    # 2. 连续性：从最近一日往前数，连续收盘低于最深档的天数
    consecutive = 0
    for _, close, _ in reversed(bars):
        if close < deepest:
            consecutive += 1
        else:
            break

    # 3. 均线结构（MA5/MA10/MA20）
    ma5 = _moving_average(closes, 5)
    ma10 = _moving_average(closes, 10)
    ma20 = _moving_average(closes, 20)
    mas = [m for m in (ma5, ma10, ma20) if m is not None]
    above_ma_count = sum(1 for m in mas if price >= m)
    if len(mas) == 3:
        if ma5 > ma10 > ma20:
            structure = "多头排列"
        elif ma5 < ma10 < ma20:
            structure = "空头排列"
        else:
            structure = "均线纠缠"
    else:
        structure = "均线数据不足"

    # 4. 量能：最近一日 vs 近 20 日均量
    if len(volumes) >= 6:
        recent_avg = sum(volumes[-5:]) / 5
        base_window = volumes[-20:] if len(volumes) >= 20 else volumes
        base_avg = sum(base_window) / len(base_window)
        if base_avg > 0:
            ratio = recent_avg / base_avg
            if ratio >= th.volume_spike:
                volume_note = f"放量破位（近5日均量为近期 {ratio:.1f} 倍）"
            elif ratio <= th.volume_shrink:
                volume_note = f"缩量破位（近5日均量仅为近期 {ratio:.1f} 倍）"
            else:
                volume_note = "量能平稳"
        else:
            volume_note = "量能数据异常"
    else:
        volume_note = "量能数据不足"

    # 5. 分级：按明确的优先级树定档（而非加法计分——计分下"暂缓观察"
    # 几乎不可达，因为价格破位后均线通常已转弱，弱势证据必然累积）。
    # 优先级：暂缓（浅破位+缩量+刚破位）> 立即（深/持续/放量）> 减半（其余）
    weak_evidence = []
    if depth_pct >= th.deep_pct:
        weak_evidence.append(f"破位深（-{depth_pct:.1f}%）")
    if structure == "空头排列":
        weak_evidence.append("均线空头排列")
    elif structure == "均线纠缠":
        weak_evidence.append("均线纠缠")
    if above_ma_count == 0:
        weak_evidence.append("价格位于全部均线下方")
    if "放量" in volume_note:
        weak_evidence.append("放量")
    evidence_text = "、".join(weak_evidence) if weak_evidence else "无显著弱势证据"

    shrinking = "缩量" in volume_note

    if depth_pct < th.shallow_pct and consecutive <= th.wait_max_consecutive and shrinking:
        # 典型浅插针：刚破位、幅度小、且缩量——不确认，给它一天
        urgency = URGENCY_WAIT
        suggestion = (f"缩量浅破位（{evidence_text or '破位幅度小'}）——"
                      "可暂缓一日观察，若明日仍收于止损位下方再执行，"
                      "避免被单日插针打出")
    elif depth_pct >= th.deep_pct or consecutive >= th.persist_days or "放量" in volume_note:
        urgency = URGENCY_IMMEDIATE
        suggestion = (f"技术面弱势证据充分（{evidence_text}）——"
                      "建议按规则立即执行全部卖出")
    else:
        urgency = URGENCY_HALF
        suggestion = (f"已破位但弱势证据不全面（{evidence_text}）——"
                      "可先减半观察，剩余半仓以更低一档或前低作为二次触发")

    # 首日破位降档：技术面再弱，单日也可能只是插针。当日刚跌破（连续 1 日）
    # 不给"立即全卖"，降为减半观察——这正是用户要的"按变化判断"而非机械执行。
    if urgency == URGENCY_IMMEDIATE and consecutive <= th.fresh_days:
        urgency = URGENCY_HALF
        suggestion = (f"当日刚破位（弱势证据：{evidence_text}）——"
                      "首日不急于全清，建议先减半观察，"
                      "若明日仍收于止损位下方再清剩余")

    return BreachDiagnosis(
        depth_pct=round(depth_pct, 2),
        consecutive_days=consecutive,
        above_ma_count=above_ma_count,
        ma_structure=structure,
        volume_note=volume_note,
        urgency=urgency,
        suggestion=suggestion,
    )

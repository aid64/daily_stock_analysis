# -*- coding: utf-8 -*-
"""账户中心决策引擎（FINAL-PLAN-ACCOUNT-CENTRIC R1）

决策对象是用户真实账户（持仓 + 现金），不是策略模拟组合。
回答的唯一问题："我今天该买什么/卖什么/拿着不动"。

决策优先级（确定性在前，LLM 永远只解释不决策）：
  1. 用户规则（AGENT_EVENT_ALERT_RULES_JSON 的 price_cross）
     - direction=below → 止损位：现价低于 → 卖出全部
     - direction=above → 止盈位：现价高于 → 卖出全部
     用户配置规则本身就是执行授权——此前系统每天告警"跌破止损"
     却从不转化成卖出决策，正是"账户中心缺失"的实证。
  2. 组合风险预算：单票集中度超阈值 → 减仓到阈值内（分批可执行）
  3. E2 择时：估值高分位 → 现金防守（不新增买入）
  4. LLM 分析（analysis_history 最近记录）：每条决策附一句话解释
  5. CB 策略目标：降级为研究观察，不进入决策主体（见操作单附注）

执行机制完全复用 order_sheet_service 状态机：生成的卖出单与
/成交回填、/撤单、成本门、影子期指标天然兼容。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 决策三档
VERDICT_SELL = "sell"        # 规则触发（止损/止盈）→ 清仓
VERDICT_REDUCE = "reduce"    # 集中度超限 → 减仓到阈值内
VERDICT_HOLD = "hold"        # 无触发 → 持有（含理由）

# E2 择时防守阈值（与 /健康、操作单 timing_note 同一口径）
E2_DEFENSIVE_THRESHOLD = 0.80

VERDICT_LABELS = {
    VERDICT_SELL: "卖出",
    VERDICT_REDUCE: "减仓",
    VERDICT_HOLD: "持有",
}

# 破位分级紧急度（辅助判断的离散档位）
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


# ── 用户规则解析 ──────────────────────────────────────────────


def load_price_rules() -> Dict[str, Dict[str, List[float]]]:
    """从 AGENT_EVENT_ALERT_RULES_JSON 提取决策语义规则。

    Returns:
        {symbol: {"stop_loss": [10.87, 10.65], "take_profit": [11.77]}}
        price_cross/below = 止损位（降序），price_cross/above = 止盈位（升序）。
        其余告警类型（涨跌幅/量比）是事件提示，不构成交易决策，忽略。
    """
    try:
        from src.config import Config
        from src.agent.events import parse_event_alert_rules
    except Exception as exc:  # noqa: BLE001 - 规则不可读 = 无规则决策
        logger.warning("[account-decision] 规则解析依赖不可用: %r", exc)
        return {}

    try:
        # Config() 直构只给 dataclass 默认值——必须走 get_instance() 单例
        # （内部 setup_env 加载 .env），server 与 bot 命令共用同一实例。
        config = Config.get_instance()
        entries = parse_event_alert_rules(
            getattr(config, "agent_event_alert_rules_json", "") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[account-decision] 规则解析失败: %r", exc)
        return {}

    rules: Dict[str, Dict[str, List[float]]] = {}
    for entry in entries or []:
        try:
            if str(entry.get("alert_type") or "").lower() != "price_cross":
                continue
            symbol = _normalize_symbol(str(entry.get("stock_code") or ""))
            price = float(entry.get("price") or 0)
            if not symbol or price <= 0:
                continue
            direction = str(entry.get("direction") or "").lower()
            bucket = rules.setdefault(symbol, {"stop_loss": [], "take_profit": []})
            if direction == "below":
                bucket["stop_loss"].append(price)
            elif direction == "above":
                bucket["take_profit"].append(price)
        except (TypeError, ValueError):
            continue
    for bucket in rules.values():
        bucket["stop_loss"].sort(reverse=True)   # 一档（高）在前
        bucket["take_profit"].sort()             # 一档（低）在前
    return rules


def _normalize_symbol(symbol: str) -> str:
    """规则键统一到持仓快照的裸码形态（000899）——快照是决策真源。

    PortfolioService 实际存裸码（实测 000899/002475）；canonical_symbol
    的前缀形态（SZ000899）是 CB 契约侧语义，这里不用。
    """
    digits = "".join(ch for ch in symbol if ch.isdigit())
    return digits if len(digits) == 6 else symbol.strip().upper()


# ── LLM 解释层（只读最近分析，不参与决策事实）─────────────────


def _latest_ai_notes(symbols: List[str], days: int = 3) -> Tuple[Dict[str, str], Dict[str, str]]:
    """每只持仓最近一次分析的结论一句话 + 股票名称（解释层，缺失不阻塞）。

    Returns:
        (notes, names)：notes = symbol -> 解释句；names = symbol -> 中文名。
    """
    notes: Dict[str, str] = {}
    names: Dict[str, str] = {}
    try:
        from src.repositories.analysis_repo import AnalysisRepository
        repo = AnalysisRepository()
        for symbol in symbols:
            digits = "".join(ch for ch in symbol if ch.isdigit())
            records = repo.get_list(code=digits, days=days, limit=1)
            if not records:
                continue
            rec = records[0]
            if getattr(rec, "name", None):
                names[symbol] = str(rec.name)
            advice = str(rec.operation_advice or "").strip()
            summary = str(rec.analysis_summary or "").strip()
            as_of = ""
            created = getattr(rec, "created_at", None)
            if created:
                as_of = f"（{str(created)[:10]}）"
            note = advice and f"{advice}——"
            snippet = summary.split("。")[0][:60]
            notes[symbol] = f"{note}{snippet}{as_of}".strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[account-decision] AI 解释读取失败: %r", exc)
    return notes, names


# ── E2 择时（与 /健康、旧 timing_note 同一算法同一 PIT）────────


def compute_e2_percentile() -> Tuple[Optional[float], str]:
    """转债估值 expanding 分位。Returns: (percentile, as_of 日期)。"""
    try:
        from pathlib import Path

        import pandas as pd

        pit_path = (Path(__file__).resolve().parents[3]
                    / "quant_research" / "data" / "pit"
                    / "convertible_pit.parquet")
        if not pit_path.exists():
            return None, ""
        pit = pd.read_parquet(pit_path, columns=["as_of", "price"])
        median = pit.groupby("as_of")["price"].median().sort_index()
        if median.empty:
            return None, ""
        pct = float((median <= median.iloc[-1]).mean())
        return pct, str(pd.Timestamp(median.index[-1]).date())
    except Exception as exc:  # noqa: BLE001 - 择时失败不影响主决策
        logger.warning("[account-decision] E2 分位计算失败: %r", exc)
        return None, ""


# ── 决策核心（纯函数，不做网络 IO）────────────────────────────


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


# ── 破位诊断（规则触发后的辅助判断）───────────────────────────


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


def build_account_decision(
    *,
    snapshot: Dict[str, Any],
    rules: Optional[Dict[str, Dict[str, List[float]]]] = None,
    concentration_threshold_pct: float = 35.0,
    include_e2: bool = True,
) -> AccountDecision:
    """从持仓快照构建账户决策包。

    Args:
        snapshot: PortfolioService.get_portfolio_snapshot() 的返回值
            （调用方决定 include_realtime——管线 09:00 前用 T-1，盘中用实时）
        rules: 已解析规则（缺省时现场解析）
        concentration_threshold_pct: 单票集中度告警阈值（%）
    """
    trade_date = date.today().isoformat()
    rules = rules if rules is not None else load_price_rules()

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
    ai_notes, names = _latest_ai_notes(symbols)

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
    # 2. 减仓数量修正：目标 = 阈值内可持有量（按当前总市值近似，整手取整）
    for i, decision in enumerate(decisions):
        if decision.verdict != VERDICT_REDUCE or decision.last_price <= 0:
            continue
        from src.services.order_sheet_service import lot_size_for
        lot = lot_size_for(decision.symbol)
        budget_mv = total_value * concentration_threshold_pct / 100
        target = int(budget_mv / decision.last_price / lot) * lot
        target = max(0, min(target, decision.quantity))
        # 目标不足一手或不低于现持仓 → 退化为持有（卖不出有效手数）
        if target >= decision.quantity or (decision.quantity - target) < lot:
            decisions[i] = PositionDecision(
                **{**decision.to_dict(),
                   "verdict": VERDICT_HOLD,
                   "target_quantity": decision.quantity,
                   "reason": (f"集中度 {decision.weight_pct:.1f}% 超阈值但减仓量"
                              f"不足一手——持有并提示，无指令")})
        else:
            decisions[i] = PositionDecision(
                **{**decision.to_dict(), "target_quantity": target})

    # 3. 现金决策（E2 择时层）
    e2_pct, e2_as_of = compute_e2_percentile() if include_e2 else (None, "")
    cash_weight = (cash / total_value * 100) if total_value > 0 else 0.0
    if e2_pct is not None and e2_pct > E2_DEFENSIVE_THRESHOLD:
        cash_verdict = "持有"
        cash_reason = (f"E2 择时：估值分位 {e2_pct:.1%}（截至 {e2_as_of}，"
                       f">{E2_DEFENSIVE_THRESHOLD:.0%} 高分位防守区）——不新增买入")
    elif e2_pct is not None:
        cash_verdict = "可配置"
        cash_reason = (f"E2 择时：估值分位 {e2_pct:.1%}（截至 {e2_as_of}）——"
                       "分位正常，现金可按规则配置")
    else:
        cash_verdict = "持有"
        cash_reason = "E2 择时不可用——默认持有现金，不新增买入"

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


# ── 决策包 → 操作单（复用现有状态机）─────────────────────────


def decision_to_sheet(decision: AccountDecision,
                      available_cash: float) -> Any:
    """决策包 → OrderSheet：卖出/减仓进 orders，持有进 holds。

    决策语义映射为 target diff：sell→目标0，reduce→目标计算值，hold→当前量。
    执行机制（限价/放弃条件/撤单时刻/成本门/单笔上限）全部由
    generate_order_sheet 承担——与回测成交假设和回填状态机一致。
    """
    from src.services.order_sheet_service import generate_order_sheet

    current: Dict[str, float] = {}
    target: Dict[str, float] = {}
    prices: Dict[str, float] = {}
    reasons: Dict[str, str] = {}
    hold_reasons: Dict[str, str] = {}

    for p in decision.positions:
        if p.last_price <= 0:
            current[p.symbol] = float(p.quantity)
            hold_reasons[p.symbol] = f"{p.reason}（无报价）"
            continue
        current[p.symbol] = float(p.quantity)
        prices[p.symbol] = p.last_price
        target[p.symbol] = float(p.target_quantity)
        if p.verdict == VERDICT_SELL:
            reasons[p.symbol] = (f"{p.rule_state}——{p.reason}")
        elif p.verdict == VERDICT_REDUCE:
            reasons[p.symbol] = (f"{p.reason}（{p.quantity}→{p.target_quantity}）")

    from datetime import date as _date
    return generate_order_sheet(
        trade_date=_date.today(),
        current_positions=current,
        target_positions=target,
        t1_close_prices=prices,
        available_cash=available_cash,
        reasons=reasons,
        hold_reasons=hold_reasons,
        keep_reason="规则未触发，集中度在阈值内",
    )


# ── 渲染（飞书推送格式）───────────────────────────────────────


def _reference_execution_line(candidate: Dict[str, Any], *, cash: float) -> str:
    """买入观察池的"参考执行"行：限价 / 可买手数 / 往返成本与回本涨幅。

    费率与申报单位一律复用 order_sheet_service 的纯函数（不新写费率），
    金额同时受既有单笔上限约束——与本仓 /买入 命令、操作单生成器同一
    口径，避免"决策包建议的量"与"命令实际能落的量"互相打架。
    """
    from src.services.order_sheet_service import (
        MAX_SINGLE_ORDER_VALUE,
        _cost_fields,
        _unit_label,
        lot_size_for,
    )

    symbol = str(candidate.get("symbol") or "")
    close = float(candidate.get("close") or 0)
    lot = lot_size_for(symbol)
    if not symbol or close <= 0 or lot <= 0:
        return ""
    affordable = int(cash / close / lot) * lot
    if affordable <= 0:
        return (f"    参考执行：限价 {close:g} · 现金不足一手（本手约需 "
                f"¥{close * lot:,.0f}）")
    capped = int(MAX_SINGLE_ORDER_VALUE / close / lot) * lot
    if capped <= 0:
        return (f"    参考执行：限价 {close:g} · 单手 ¥{close * lot:,.0f} 已超"
                f"单笔上限 ¥{MAX_SINGLE_ORDER_VALUE:,.0f}——不在可落单范围")
    quantity = min(affordable, capped)
    cost = _cost_fields(symbol, quantity, close, "buy")
    capped_note = (f"，按单笔上限 ¥{MAX_SINGLE_ORDER_VALUE:,.0f} 截断"
                   if quantity < affordable else "")
    return (f"    参考执行：限价 {close:g} · 可买 {quantity}"
            f"{_unit_label(symbol)}（约 ¥{quantity * close:,.0f}{capped_note}）"
            f" · 往返成本 ¥{cost['round_trip_cost']:,.2f}"
            f"（回本 {cost['break_even_pct'] * 100:.2f}%）")


def render_decision_package(decision: AccountDecision,
                            sheet: Any = None) -> str:
    """决策包渲染：待办动作卡片式（2026-09-19 重写——用户反馈"老一套文字墙"）。

    结构（动作优先）：
      ① 编号动作区——每条含一句话依据 + 边界规则 + 可直接复制的回填命令
      ② 持有观察区——一票一行
      ③ 现金/策略区——策略建议 + 现金结论
      ④ 观察池——仅在当前现金买得起至少一手时展开，否则一行说明
      ⑤ 依据溯源——压缩为一行（详情见 docs/strategy-catalog.md）

    Args:
        sheet: decision_to_sheet 产出的操作单（提供限价/放弃条件；
            None 时只渲染结论，动作不带回填命令）
    """
    from src.services.order_sheet_service import _unit_label

    position_map = {p.symbol: p for p in decision.positions}
    order_list = list(sheet.orders) if sheet else []
    orders_by_symbol = {o.symbol: o for o in order_list}

    # ── ① 编号动作区（交易单 + 策略现金操作）─────────────────
    actions: List[str] = []          # (标题, 依据, 边界, 回填命令)
    for order in order_list:
        p = position_map.get(order.symbol)
        qty_text = f"{order.quantity}{_unit_label(order.symbol)}"
        direction = "买入" if order.direction == "buy" else "卖出"
        title = (f"{direction} {order.symbol} "
                 f"{(p.name if p else '')[:6]}".rstrip())
        actions.append((
            f"{title} {qty_text} @{order.limit_price:g}"
            f"（约 ¥{order.est_value:,.0f}）",
            (p.reason if p else "")[:52],
            f"{order.abandon_rule} · {order.cancel_rule}",
            f"/成交回填 {order.symbol} {direction} "
            f"{order.quantity} {order.limit_price:g}"))
    for s in decision.strategy_signals:
        actions.append((s["action"], s["reason"][:52], "", ""))

    lines: List[str] = []
    n = len(actions)
    if n:
        lines.append(f"【今日待办 {decision.trade_date}】{n} 个动作")
        for i, (title, reason, boundary, fill_cmd) in enumerate(actions, 1):
            lines.append("")
            lines.append(f"{'①②③④⑤⑥⑦⑧⑨⑩'[i-1] if i <= 10 else i}. {title}")
            if reason:
                lines.append(f"   依据：{reason}")
            if boundary:
                lines.append(f"   边界：{boundary}")
            if fill_cmd:
                lines.append(f"   成交后发送：{fill_cmd}")
    else:
        lines.append(f"【今日待办 {decision.trade_date}】无动作")

    # ── ② 持有观察区（不在动作区的持仓，一票一行）────────────
    _urgency_labels = {
        URGENCY_IMMEDIATE: "建议立即执行",
        URGENCY_HALF: "建议减半观察",
        URGENCY_WAIT: "建议暂缓一日",
    }
    hold_lines: List[str] = []
    for p in decision.positions:
        b = p.breach or {}
        if p.symbol in orders_by_symbol:
            # 已进动作区：这里只补一行破位诊断上下文
            if b:
                hold_lines.append(
                    f"· {p.symbol} 破位：连续 {b.get('consecutive_days')} 日"
                    f"收于止损下方 · {b.get('ma_structure')} · "
                    f"{b.get('volume_note')}"
                    + (f" → {_urgency_labels.get(b.get('urgency'), '')}"
                       if b.get("urgency") else ""))
            continue
        price_text = f"{p.last_price:g}" if p.last_price > 0 else "无报价"
        sign = "+" if p.pnl >= 0 else ""
        pnl_text = (f" · 浮亏 {p.pnl:,.0f}元" if p.pnl < 0
                    else (f" · 浮盈 +{p.pnl:,.0f}元" if p.pnl > 0 else ""))
        hold_lines.append(
            f"· {p.symbol} {p.name} {p.quantity}{_unit_label(p.symbol)}"
            f" 现价 {price_text}{pnl_text}"
            f" —— {p.rule_state}")
        if b:
            # WAIT 档等：破位但暂缓，诊断与建议必须在观察区可见
            hold_lines.append(
                f"  破位：连续 {b.get('consecutive_days')} 日收于止损下方 · "
                f"{b.get('ma_structure')} · {b.get('volume_note')}"
                + (f" → {_urgency_labels.get(b.get('urgency'), '')}"
                   if b.get("urgency") else ""))
        if p.ai_note:
            hold_lines.append(f"  AI（仅参考）：{p.ai_note[:48]}")

    if hold_lines:
        lines.append("")
        lines.append("—— 持有观察（无需操作）——")
        lines.extend(hold_lines)

    # ── ③ 现金与策略区 ────────────────────────────────────────
    lines.append("")
    lines.append(f"—— 现金 ¥{decision.cash:,.0f}"
                 f"（{decision.cash_weight_pct:.1f}%）——")
    e2_text = (f"E2 分位 {decision.e2_percentile:.1%}"
               f"（截至 {decision.e2_as_of}）"
               if decision.e2_percentile is not None else "E2 不可用")
    lines.append(f"{decision.cash_verdict} · {e2_text}")
    if (decision.positions
            and any(p.verdict in (VERDICT_SELL, VERDICT_REDUCE)
                    for p in decision.positions)):
        lines.append("（卖出/减仓执行后现金将增加——回填后自动更新）")

    # ── ④ 买入观察池（仅在现金买得起一手时展开）────────────
    if decision.buy_candidates:
        # 计算最便宜一手所需（A 股一手 100 股）
        min_lot_cost = min(
            (c.get("close") or 0) * 100 for c in decision.buy_candidates
        ) if decision.buy_candidates else None
        can_afford = (decision.cash > 0 and min_lot_cost is not None
                     and decision.cash >= min_lot_cost)
        lines.append("")
        if can_afford:
            lines.append("—— 观察池（观点非信号，回复 /买入 <代码> 落单）——")
            for c in decision.buy_candidates[:3]:
                lines.append(f"· {c['symbol']} {c['name']} 收盘 {c['close']:g}"
                             f"（{c['change_pct']:+.1f}%）"
                             f"——{'，'.join(c.get('reasons') or [])[:40]}")
        else:
            cheapest = (f"（最便宜一手约 ¥{min_lot_cost:,.0f}）"
                        if min_lot_cost else "")
            lines.append(f"—— 观察池 {len(decision.buy_candidates)} 只："
                         f"现金不足以买入任何标的{cheapest}，暂不展开"
                         "（卖出现金回笼后自动恢复）")
    if decision.buy_source_note:
        lines.append(f"⚠ 观察池：{decision.buy_source_note}")

    # ── ⑤ 依据溯源（一行，详情按需）──────────────────────────
    lines.append("")
    lines.append("依据：止损/止盈=结构锚生成（20日高低±1.5×波幅，09-19 重生）·"
                 "破位分级=启发式（未验证）·"
                 f"集中度≤{decision.concentration_threshold_pct:.0f}%（共识）·"
                 "E2=转债分位（实验性）· AI 只解释不决策 ·"
                 "详情 docs/strategy-catalog.md")
    return "\n".join(lines)


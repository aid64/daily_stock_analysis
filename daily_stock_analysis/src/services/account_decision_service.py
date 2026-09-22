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
# P1：共享类型/常量下沉 types.py、规则决策抽到 user_rules.py；re-export 保持兼容
from src.services.strategies.types import (  # noqa: E402,F401
    VERDICT_SELL, VERDICT_REDUCE, VERDICT_HOLD, VERDICT_LABELS, PositionDecision)
from src.services.strategies.user_rules import (  # noqa: E402,F401
    decide_position, levels_text)

# E2 择时防守阈值（与 /健康、操作单 timing_note 同一口径）
E2_DEFENSIVE_THRESHOLD = 0.80


# 破位分级紧急度（辅助判断的离散档位）
# 破位分级紧急度与诊断已抽到 strategies/breach.py（P1）；re-export 保持兼容
from src.services.strategies.breach import (  # noqa: E402,F401
    URGENCY_IMMEDIATE,
    URGENCY_HALF,
    URGENCY_WAIT,
    BreachThresholds,
    BreachDiagnosis,
    diagnose_breach,
)










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






# ── 破位诊断（规则触发后的辅助判断）───────────────────────────








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


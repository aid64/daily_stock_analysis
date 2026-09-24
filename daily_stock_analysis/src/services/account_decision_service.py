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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 决策三档
# P1：共享类型/常量下沉 types.py、规则决策抽到 user_rules.py；re-export 保持兼容
from src.services.strategies.types import (  # noqa: E402,F401
    VERDICT_SELL, VERDICT_REDUCE, VERDICT_HOLD, VERDICT_LABELS, PositionDecision)
from src.services.strategies.user_rules import (  # noqa: E402,F401
    decide_position, levels_text)
from src.services.strategies.concentration import (  # noqa: E402,F401
    apply_concentration_targets)
from src.services.strategies.e2_timing import (  # noqa: E402,F401
    E2_DEFENSIVE_THRESHOLD, compute_e2_percentile, decide_cash_verdict)

# E2 择时防守阈值（与 /健康、操作单 timing_note 同一口径）
# P1 step4：E2 择时（compute_e2_percentile / 阈值 / 现金决策映射）抽到
# strategies/e2_timing.py；re-export 保持 ads.* 命名空间兼容（测试 monkeypatch
# 目标不变）


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










# AccountDecision 定义已下沉 strategies/types.py（P1 收尾）；re-export 保持兼容
from src.services.strategies.types import AccountDecision  # noqa: E402,F401


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
            # P4-5：ai_note 携带新鲜度——超过 days 窗口的分析明确标"陈旧"，
            # 不再冒充当前观点（治"AI 层旧报告一句话、与当日决策矛盾"）。
            stale_flag = ""
            if created:
                try:
                    from datetime import date as _date
                    age = (_date.today()
                           - _date.fromisoformat(str(created)[:10])).days
                    if age > days:
                        stale_flag = f"[陈旧·{age}天前] "
                except (ValueError, TypeError):
                    pass
            notes[symbol] = f"{stale_flag}{note}{snippet}{as_of}".strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[account-decision] AI 解释读取失败: %r", exc)
    return notes, names


# ── E2 择时（实现已抽到 strategies/e2_timing.py，P1 step4）────────


# ── 决策核心（纯函数，不做网络 IO）────────────────────────────






# ── 破位诊断（规则触发后的辅助判断）───────────────────────────








def build_account_decision(
    *,
    snapshot: Dict[str, Any],
    rules: Optional[Dict[str, Dict[str, List[float]]]] = None,
    concentration_threshold_pct: float = 35.0,
    include_e2: bool = True,
) -> AccountDecision:
    """从持仓快照构建账户决策包（门面）。

    编排实现已抽到 portfolio_compositor.compose_account_decision（P1 收尾）；
    本门面保留签名与命名空间，供 bot/命令与测试 monkeypatch 引用。

    Args:
        snapshot: PortfolioService.get_portfolio_snapshot() 的返回值
            （调用方决定 include_realtime——管线 09:00 前用 T-1，盘中用实时）
        rules: 已解析规则（缺省时现场解析）
        concentration_threshold_pct: 单票集中度告警阈值（%）
    """
    from src.services.portfolio_compositor import compose_account_decision
    return compose_account_decision(
        snapshot=snapshot,
        rules=rules,
        concentration_threshold_pct=concentration_threshold_pct,
        include_e2=include_e2,
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


def _driver_label(p: Any) -> str:
    """动作卡片的驱动规则标签（v4.2 发现#6）。

    实测 002475 卖出量 200→100→200 的跳变实为"止损减半"与"集中度清仓"
    两机制在不同交易日交替主导——用户看到形似指令、背后理由已换。
    每条动作显式标注由哪条规则驱动，机制切换可见。
    """
    if p is None:
        return ""
    if p.verdict == VERDICT_REDUCE:
        return "集中度规则"
    if "止盈" in (p.rule_state or ""):
        return "止盈规则"
    if "止损" in (p.rule_state or ""):
        return "止损规则"
    return ""


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
        reason_text = (p.reason if p else "")[:110]
        driver = _driver_label(p)
        actions.append((
            f"{title} {qty_text} @{order.limit_price:g}"
            f"（约 ¥{order.est_value:,.0f}）",
            (f"[{driver}] {reason_text}" if driver else reason_text),
            f"{order.abandon_rule} · {order.cancel_rule}",
            f"/成交回填 {order.symbol} {direction} "
            f"{order.quantity} {order.limit_price:g}"))
    for s in decision.strategy_signals:
        actions.append((s["action"], s["reason"][:52], "", ""))

    lines: List[str] = []
    # 非交易日门（P4-4）：挂单边界（开盘放弃/14:50 撤单）只在交易日有意义。
    # 非交易日渲染时在顶部加一行说明，并抑制每条动作的时效边界展示。
    from src.services.notification_events import time_sensitive_suppressed_note
    _nontrading_note = time_sensitive_suppressed_note("cn")
    if _nontrading_note:
        lines.append(_nontrading_note)
        lines.append("")

    n = len(actions)
    if n:
        lines.append(f"【今日待办 {decision.trade_date}】{n} 个动作")
        for i, (title, reason, boundary, fill_cmd) in enumerate(actions, 1):
            lines.append("")
            lines.append(f"{'①②③④⑤⑥⑦⑧⑨⑩'[i-1] if i <= 10 else i}. {title}")
            if reason:
                lines.append(f"   依据：{reason}")
            if boundary and not _nontrading_note:
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


# -*- coding: utf-8 -*-
"""
===================================
买入命令（买入侧最小闭环）
===================================

/买入 <代码> [数量] [价格]（别名：加仓、买）

用户反馈"光卖不买"的最小闭环：系统给参考量与成本，**用户确认才落单**。

铁律（与 /操作单、/成交回填 同一条链路）：
- 人是执行引擎：本命令只把"用户确认过的买单"追加进**当日已存在**的操作单，
  绝不自动生成买单，绝不重建/覆盖当日单据（保留已回填的 orders/fills 状态）；
- 限价：命令传入优先，否则用该标的最近收盘价（复用快照/本地日线既有取价路径）；
- 数量：传入必须是整手；缺省按可用现金整手计算，受既有单笔上限约束；
- 费率与申报单位一律复用 order_sheet_service / simulation_ledger 的纯函数；
- 任一闸门命中都返回可读文案，不抛栈：交易暂停 / 无当日操作单 / 现金 ≤ 0 /
  非整手 / 超单笔上限。
"""

import logging
from datetime import date
from typing import List, Optional, Tuple

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)

_SNAPSHOT_LOOKBACK_DAYS = 7   # 与买入观察池同一容错窗口（管线未跑/节假日）


def _account_snapshot() -> dict:
    """账户快照（现金 + 持仓），PortfolioService 单一真源；离线口径。"""
    from src.services.portfolio_service import PortfolioService

    return PortfolioService().get_portfolio_snapshot(
        cost_method="fifo", include_realtime=False)


def _available_cash(snapshot: Optional[dict] = None) -> float:
    data = snapshot if snapshot is not None else _account_snapshot()
    return float(data.get("total_cash") or 0)


def _held_symbols(snapshot: Optional[dict] = None) -> List[str]:
    """在手持仓代码（裸码 6 位），用于观察池溯源与候选池同一口径。"""
    data = snapshot if snapshot is not None else _account_snapshot()
    symbols: List[str] = []
    for account in data.get("accounts") or []:
        for position in account.get("positions") or []:
            if float(position.get("quantity") or 0) <= 0:
                continue
            digits = "".join(
                ch for ch in str(position.get("symbol") or "") if ch.isdigit())
            if digits:
                symbols.append(digits)
    return symbols


def _to_positive_float(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:   # NaN 检查（NaN != NaN）
        return None
    return number


def _snapshot_close(snapshot, digits: str) -> Optional[float]:
    """全市场快照里该标的的收盘价（代码前缀/后缀两种形态都兼容）。"""
    if "close" not in getattr(snapshot, "columns", []) or "code" not in snapshot.columns:
        return None
    from src.services.buy_watchlist_service import _normalize_scan_code

    for _, row in snapshot.iterrows():
        if _normalize_scan_code(row.get("code"))[0] == digits:
            return _to_positive_float(row.get("close"))
    return None


def _resolve_last_close(symbol: str) -> Tuple[Optional[float], str]:
    """该标的最近收盘价：全市场快照（观察池同源）→ 本地日线（分析落库）。

    只读既有数据源，不新增取价通道、不联网。
    Returns:
        (close, 来源说明)；两处都取不到时返回 (None, "")。
    """
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if not digits:
        return None, ""

    try:
        from src.services.market_scan_service import load_snapshot

        for back in range(0, _SNAPSHOT_LOOKBACK_DAYS + 1):
            day = date.fromordinal(date.today().toordinal() - back)
            snapshot = load_snapshot(day)
            if snapshot is None or getattr(snapshot, "empty", True):
                continue
            close = _snapshot_close(snapshot, digits)
            if close is not None:
                return close, f"全市场快照 {day.isoformat()} 收盘"
    except Exception as exc:  # noqa: BLE001 - 快照不可用则转下一路径
        logger.warning("快照取价失败: %r", exc)

    try:
        from src.services.cb_signal_bridge import canonical_symbol
        from src.repositories.stock_repo import StockRepository

        repo = StockRepository()
        for code in (digits, canonical_symbol(symbol), str(symbol).strip().upper()):
            if not code:
                continue
            for row in repo.get_latest(code, days=5) or []:
                close = _to_positive_float(getattr(row, "close", None))
                if close is not None:
                    day = str(getattr(row, "date", ""))[:10]
                    return close, f"本地日线 {day} 收盘"
    except Exception as exc:  # noqa: BLE001
        logger.warning("本地日线取价失败: %r", exc)

    return None, ""


def _provenance(symbol: str, held: List[str]) -> str:
    """溯源标注：今日观察池候选 vs 你直接指定（非观察池）。

    观察池口径与决策包一致（剔除在手持仓）；观察池构建失败只降级为
    "你直接指定"，不影响落单。
    """
    digits = "".join(ch for ch in symbol if ch.isdigit())
    try:
        from src.services.buy_watchlist_service import build_buy_watchlist

        watchlist = build_buy_watchlist(exclude_symbols=held)
        for candidate in watchlist.get("candidates") or []:
            if str(candidate.get("symbol")) == digits:
                return "来自今日观察池候选"
    except Exception as exc:  # noqa: BLE001
        logger.warning("观察池溯源失败: %r", exc)
    return "你直接指定（非观察池）"


class BuyCommand(BotCommand):
    """追加买入腿到当日操作单（用户确认才落单）"""

    @property
    def name(self) -> str:
        return "buy"

    @property
    def aliases(self) -> List[str]:
        return ["买入", "加仓", "买"]

    @property
    def description(self) -> str:
        return "把一条买单追加进当日操作单（你确认才落单，系统不下委托）"

    @property
    def usage(self) -> str:
        return "/买入 <代码> [数量] [价格]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        if not args:
            return BotResponse.text_response(
                "参数不足。格式：/买入 代码 [数量] [价格]\n"
                "示例：/买入 002475\n"
                "      /买入 002475 100 56.50\n"
                "数量缺省按可用现金整手计算，价格缺省用最近收盘价。")
        symbol_raw = args[0]
        quantity: Optional[float] = None
        price: Optional[float] = None
        for index, token in enumerate(args[1:3], start=1):
            try:
                value = float(token)
            except ValueError:
                return BotResponse.text_response(
                    f"数量/价格必须是数字，收到：{token}\n"
                    "格式：/买入 代码 [数量] [价格]")
            if index == 1:
                quantity = value
            else:
                price = value
        try:
            return BotResponse.text_response(
                _do_buy(symbol_raw, quantity=quantity, price=price))
        except Exception as exc:  # noqa: BLE001 - 面向用户命令不抛栈
            logger.warning("买入命令失败: %r", exc)
            return BotResponse.text_response(
                f"买入指令生成失败：{exc}\n"
                "若反复出现请发 /健康 查看系统状态；本命令不会产生任何委托。")


def _risk_context_note(symbol: str, order_quantity: int, price: float,
                       snapshot: Optional[dict]) -> str:
    """U6：手工买入纳入与组合器同口径的风险提示（不阻断——用户是执行引擎）。

    两项：①本单成交后单票占比（对照 mandate.yaml 单票参考上限 25%）；
    ②E2 转债估值分位防守区状态。任何数据缺失/失败都静默跳过——
    提示是增强而非闸门，绝不因提示失败影响下单。
    """
    lines: List[str] = []
    try:
        positions = [
            p for account in (snapshot or {}).get("accounts") or []
            for p in account.get("positions") or []
            if float(p.get("quantity") or 0) > 0]
        total_cash = float((snapshot or {}).get("total_cash") or 0)
        held_value = sum(
            float(p.get("last_price") or 0) * float(p.get("quantity") or 0)
            for p in positions)
        total_value = held_value + total_cash
        if total_value > 0:
            sym_value = sum(
                float(p.get("last_price") or 0) * float(p.get("quantity") or 0)
                for p in positions if str(p.get("symbol")) == symbol)
            post_pct = (sym_value + order_quantity * price) / total_value * 100
            tag = "（超 mandate 单票参考上限 25%）" if post_pct > 25.0 else ""
            lines.append(
                f"  风险提示：本单成交后 {symbol} 单票占比约 {post_pct:.1f}%{tag}")
    except Exception as exc:  # noqa: BLE001 - 提示失败静默跳过
        logger.warning("买入风险提示（单票占比）计算失败（跳过）: %r", exc)
    try:
        from src.services.strategies.e2_timing import compute_e2_percentile

        e2_pct, e2_as_of = compute_e2_percentile()
        if e2_pct is not None and float(e2_pct) > 0.80:
            lines.append(
                f"  风险提示：E2 转债估值分位 {float(e2_pct) * 100:.1f}%"
                f"（{e2_as_of}）处于防守区（>80%）——组合现金结论为防守，"
                "本单请格外谨慎")
    except Exception as exc:  # noqa: BLE001 - 提示失败静默跳过
        logger.warning("买入风险提示（E2）获取失败（跳过）: %r", exc)
    return "\n".join(lines)


def _do_buy(symbol_raw: str, quantity: Optional[float] = None,
            price: Optional[float] = None) -> str:
    """买入核心：闸门校验 → 组装一条 buy OrderLine → 追加进当日操作单。

    订单追加而非重建（保留既有 orders 与 fills），并按当前 orders 重算
    合计后落盘；模拟账本幂等记录，失败不影响回复。
    """
    from src.services.cb_signal_bridge import canonical_symbol
    from src.services.order_sheet_service import (
        DEFAULT_ABANDON_PCT,
        DEFAULT_CANCEL_TIME,
        MAX_SINGLE_ORDER_VALUE,
        OrderLine,
        _cost_fields,
        _unit_label,
        load_latest_sheet,
        lot_size_for,
        recalculate_totals,
        save_order_sheet,
    )
    from src.services.trading_halt_service import halt_status

    # ① 交易暂停（业务级 kill switch）：最高优先级闸门
    try:
        halt = halt_status() or {}
    except Exception as exc:  # noqa: BLE001 - 状态不可读 = unknown，阻断而非放行
        logger.warning("交易暂停状态读取失败，按阻断处理: %r", exc)
        return ("⛔ 交易暂停状态不可读（unknown）——为安全计不生成买单。"
                "请稍后重试或发 /健康 排查。")
    if halt.get("halted"):
        return (f"⛔ 交易已暂停（{halt.get('halted_at', '?')}，"
                f"原因：{halt.get('reason', '?')}）——不生成买单。\n"
                "恢复请发 /恢复交易。")

    # ② 当日操作单必须已存在：不擅自重建（重建会覆盖已回填状态）
    sheet = load_latest_sheet(date.today().isoformat())
    if sheet is None:
        return ("今日还没有操作单——请先发 /操作单 查看今日决策包再来 /买入。\n"
                "（本命令不重建当日单据：重建会覆盖已回填的成交状态）")

    symbol = canonical_symbol(symbol_raw)
    lot = lot_size_for(symbol)
    unit = _unit_label(symbol)

    # ③ 限价：命令传入优先，否则最近收盘价（既有取价路径）
    if price is None:
        price, price_source = _resolve_last_close(symbol)
        if price is None:
            return (f"取不到 {symbol} 的最近收盘价（快照/本地日线均无记录）——"
                    f"请显式给出价格：/买入 {symbol_raw} [数量] 价格")
        price_source = f"{price_source}（最近收盘价）"
    else:
        price_source = "命令传入"
        if price <= 0:
            return "限价必须大于 0。"

    snapshot = _account_snapshot()
    cash = _available_cash(snapshot)

    # ④ 现金闸门：现金 ≤ 0（账户缺资金入账）不给买单
    if cash <= 0:
        return (f"可用现金 ¥{cash:,.2f} ≤ 0——账户没有可用资金记录，不生成买单。\n"
                "请先 /入金 登记转入资金，或用 /成交回填 补记已发生的成交后重试。")

    cap_quantity = int(MAX_SINGLE_ORDER_VALUE / price / lot) * lot

    # ⑤ 数量：传入必须是整手；缺省按可用现金整手计算（受单笔上限约束）
    cap_note = ""
    if quantity is None:
        affordable = int(cash / price / lot) * lot
        if affordable <= 0:
            return (f"现金不足一手：可用 ¥{cash:,.2f}，一手（{lot}{unit}）约需 "
                    f"¥{price * lot:,.2f}——请先 /入金，或显式给出更小数量/价格。")
        if cap_quantity <= 0:
            return (f"一手（{lot}{unit}）约 ¥{price * lot:,.2f} 已超单笔上限 "
                    f"¥{MAX_SINGLE_ORDER_VALUE:,.0f}——本仓不生成这种单，"
                    "请人工核实价格或改用手工登记。")
        order_quantity = min(affordable, cap_quantity)
        if order_quantity < affordable:
            cap_note = f"，受单笔上限 ¥{MAX_SINGLE_ORDER_VALUE:,.0f} 截断"
    else:
        if quantity <= 0 or quantity != int(quantity):
            return f"数量必须是正整数（整手），收到：{quantity:g}"
        order_quantity = int(quantity)
        if order_quantity % lot != 0:
            return (f"{symbol} 的申报单位是 {lot}{unit}（整手）——收到 "
                    f"{order_quantity}，请改为 {lot} 的整数倍。\n"
                    "（转债 10 张/手，股票 100 股/手）")
        estimated = order_quantity * price
        if estimated > MAX_SINGLE_ORDER_VALUE:
            return (f"超单笔上限：本单约 ¥{estimated:,.2f} > "
                    f"¥{MAX_SINGLE_ORDER_VALUE:,.0f}——请拆单（分多次 /买入）"
                    "或减小数量。")
        if estimated > cash:
            return (f"现金不足：本单约 ¥{estimated:,.2f}，可用 ¥{cash:,.2f}"
                    "——请先 /入金 或减小数量。")

    # ⑥ 组装买入腿：限价/放弃条件/撤单时刻/成本门全部复用既有实现
    estimated_value = order_quantity * price
    provenance = _provenance(symbol, _held_symbols(snapshot))
    line = OrderLine(
        direction="buy",
        symbol=symbol,
        quantity=order_quantity,
        limit_price=round(price, 3),
        abandon_rule=f"开盘>{price * (1 + DEFAULT_ABANDON_PCT):.3f} 放弃",
        cancel_rule=f"{DEFAULT_CANCEL_TIME} 未成交撤单",
        est_value=round(estimated_value, 2),
        reason=f"用户确认买入（{provenance}）——限价来源：{price_source}",
        **_cost_fields(symbol, order_quantity, price, "buy"))

    # ⑦ 追加进当日操作单（保留既有 orders/fills）→ 重算合计 → 落盘
    sheet.orders.append(line)
    recalculate_totals(sheet)
    save_order_sheet(sheet)

    # ⑧ 模拟账本（幂等；失败不影响本次回复）
    ledger_note = ""
    try:
        from src.services.simulation_ledger import SimulationLedger

        recorded = SimulationLedger().record_sheet(sheet)
        ledger_note = "" if recorded else "（模拟账本已有该腿，幂等跳过）"
    except Exception as exc:  # noqa: BLE001
        logger.warning("模拟账本记录失败: %r", exc)
        ledger_note = "（模拟账本记录失败，不影响本单；可用 /对比 复核）"

    # ⑨ 回复：方向/代码/数量/限价/金额/往返成本/回本涨幅 + 回填指引
    # U6：风险提示与动作同行（不阻断）；插在回填指引之前
    risk_note = _risk_context_note(symbol, order_quantity, price, snapshot)
    break_even_pct = line.break_even_pct * 100
    lines = [
        "✅ 买单已加入今日操作单（系统未发送任何委托——请你在券商 App "
        "按限价手动挂单）",
        f"· 买入 {symbol} {order_quantity}{unit} @{line.limit_price:g} 限价"
        f"（约 ¥{estimated_value:,.2f}{cap_note}）",
        f"  {line.abandon_rule} · {line.cancel_rule}",
        f"  成本门：往返成本约 ¥{line.round_trip_cost:,.2f}"
        f"（{break_even_pct:.2f}%）→ 需涨 {break_even_pct:.2f}% 才回本"
        + ("——小单成本占比偏高，确认值得再做" if break_even_pct >= 1.0 else ""),
        f"  溯源：{provenance} · 限价来源：{price_source}",
        f"  现金：可用 ¥{cash:,.2f} → 本单成交后约剩 "
        f"¥{cash - estimated_value:,.2f}{ledger_note}",
    ]
    if risk_note:
        lines.append(risk_note)
    lines.append(f"成交后发 /成交回填 {symbol} 买入 {order_quantity} {price:g}")
    return "\n".join(lines)

# -*- coding: utf-8 -*-
"""
===================================
成交回填命令
===================================

/成交回填 代码 方向 数量 价格 [确认]（CLOSURE-FIX-PLAN G5 + 方案v2 批次A）

闭环最后一环：用户在券商 App 手动下单成交后，回填到系统——
① 真实账本（先写，事务性主记录）② 操作单标记（后标，账本成功才标）
③ /对比 立即可见模拟 vs 真实偏差。

批次 A 完整性（OPTIMIZATION-PLAN-V2）：
- 幂等：trade_uid = sheet_id:symbol:side:日期 派生，重复回填被唯一约束拒收；
- 部分成交：fills 累计量，未达计划量为 partially_filled（FIX OrdStatus 1）；
- 量价合理性门：价偏离限价 ±5% 或量超计划剩余 → 需带"确认"参数二次执行；
- 顺序：先账本成功、再标操作单——账本失败时单据不留半状态。
"""

from datetime import date
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

_PRICE_DEVIATION_PCT = 5.0   # 回填价偏离限价 ±5% 触发确认门


class FillCommand(BotCommand):
    """成交回填"""

    @property
    def name(self) -> str:
        return "fill"

    @property
    def aliases(self) -> List[str]:
        return ["成交回填", "回填", "fill"]

    @property
    def description(self) -> str:
        return "回填真实成交（幂等 + 部分成交 + 量价确认门）"

    @property
    def usage(self) -> str:
        return "/成交回填 002475 买入 100 56.50 [确认]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        if len(args) < 4:
            return BotResponse.text_response(
                "参数不足。格式：/成交回填 代码 方向(买入/卖出) 数量 价格 [费用N] [确认]\n"
                "示例：/成交回填 002475 买入 100 56.50\n"
                "      /成交回填 002475 买入 100 56.50 费用12.5 确认")
        symbol_raw, side_raw, qty_raw, price_raw = args[0], args[1], args[2], args[3]
        confirmed = "确认" in args[4:]
        fee: float | None = None
        for token in args[4:]:
            if token.lower().startswith("费用"):
                try:
                    fee = float(token[2:])
                except ValueError:
                    return BotResponse.text_response(f"费用必须是数字，收到：{token}")
        side_map = {"买入": "buy", "卖出": "sell",
                    "buy": "buy", "sell": "sell", "b": "buy", "s": "sell"}
        side = side_map.get(side_raw.strip().lower())
        if side is None:
            return BotResponse.text_response(f"方向必须是 买入/卖出，收到：{side_raw}")
        try:
            quantity = float(qty_raw)
            price = float(price_raw)
        except ValueError:
            return BotResponse.text_response("数量/价格必须是数字")

        try:
            result = _do_fill(symbol_raw, side, quantity, price,
                              confirmed=confirmed, fee=fee)
        except Exception as exc:  # noqa: BLE001 - 面向用户命令不抛栈
            return BotResponse.text_response(f"回填失败：{exc}")
        return BotResponse.text_response(result)


def _do_fill(symbol_raw: str, side: str, quantity: float, price: float,
             *, confirmed: bool = False, fee: float | None = None) -> str:
    """回填核心逻辑（bot 命令与 API 共用）。

    顺序（批次 A2）：① 真实账本（主记录，失败即中止）② 操作单标记
    （账本成功后才标）。幂等键 A1 保证同一笔回填只入账一次。
    费用（E1.3，Ghostfolio fee 一等字段语义）：显式传入用实值；未传按
    费率模型估算并在备注标注 estimated——费用不缺席。
    """
    from src.services.cb_signal_bridge import canonical_symbol
    from src.services.order_sheet_service import (
        load_latest_sheet,
        mark_order_filled,
    )
    from src.services.portfolio_analysis_context import (
        get_or_create_default_account,
    )
    from src.services.portfolio_service import PortfolioService

    symbol = canonical_symbol(symbol_raw)
    today = date.today()

    sheet = load_latest_sheet(today.isoformat())
    matched = None
    if sheet is not None:
        matched = next(
            (o for o in sheet.orders
             if o.symbol == symbol and o.direction == side), None)

    # 幂等键（批次 A1）：粒度 = 腿+日+量+价。同一笔重复发送（同量同价）
    # 被拒收；不同量/价的多次部分成交各自入账。极端场景"同腿同日两次
    # 完全同量同价的合法成交"用 /持仓登记 手工登记（概率远低于重复发送）。
    if matched is not None:
        trade_uid = (f"{sheet.sheet_id}:{symbol}:{side}:"
                     f"{today.isoformat()}:{quantity:g}:{price:g}")
    else:
        import hashlib
        raw = f"no-sheet:{symbol}:{side}:{quantity:g}:{price:g}:{today.isoformat()}"
        trade_uid = "manual:" + hashlib.sha256(raw.encode()).hexdigest()[:16]

    # 量价合理性门（批次 A5）：偏离限价 ±5% 或量超计划剩余 → 需"确认"
    if matched is not None and not confirmed:
        planned = float(matched.quantity)
        prev_filled = float((sheet.fills.get(symbol) or {}).get(
            "actual_quantity") or 0)
        remaining = planned - prev_filled
        deviation = abs(price - matched.limit_price) / matched.limit_price * 100
        warnings = []
        if deviation > _PRICE_DEVIATION_PCT:
            warnings.append(
                f"回填价 {price:g} 偏离计划限价 {matched.limit_price:g} "
                f"（{deviation:.1f}%）")
        if quantity > remaining + 1e-9:
            warnings.append(
                f"回填量 {quantity:g} 超过计划剩余 {remaining:g}")
        if warnings:
            side_text = "买入" if side == "buy" else "卖出"
            return (
                "⚠ 量价合理性确认：\n· " + "\n· ".join(warnings) +
                "\n\n如确属真实成交，请重发并追加参数：确认\n"
                f"例：/成交回填 {symbol_raw} {side_text} {quantity:g} {price:g} 确认")

    # ① 真实账本（主记录——失败抛异常，操作单不留半状态）
    service = PortfolioService()
    account = get_or_create_default_account(service)
    if account is None:
        raise RuntimeError("持仓账户初始化失败")
    # 费用（E1.3）：显式传入用实值；未传按费率模型估算（转债免印花税）
    if fee is None:
        from src.services.simulation_ledger import calculate_costs, load_fees
        fees = dict(load_fees())
        digits = "".join(ch for ch in symbol if ch.isdigit())
        if len(digits) == 6 and digits.startswith(("11", "12")):
            fees["stamp_duty"] = 0.0
        fee = calculate_costs(int(quantity), price, side, fees)["total"]
        fee_note = f"（费用按模型估算 ¥{fee:.2f}，如有实值可下次带 费用N 更正）"
    else:
        fee_note = f"（费用实值 ¥{fee:.2f}）"
    try:
        service.record_trade(
            account_id=int(account["account_id"]),
            symbol=symbol,
            trade_date=today,
            side=side,
            quantity=quantity,
            price=price,
            fee=float(fee),
            trade_uid=trade_uid,
        )
    except Exception as exc:
        from src.services.portfolio_service import PortfolioConflictError
        if isinstance(exc, PortfolioConflictError) or "Duplicate" in str(exc):
            return (f"⚠ 该笔成交已回填过（幂等拦截）。\n"
                    f"标识：{trade_uid}\n"
                    "如需补记另一笔真实成交，请核对数量/价格后用 /持仓登记 手工登记。")
        raise

    # ② 操作单标记（账本成功后；部分成交累计量）
    if matched is not None:
        mark_order_filled(sheet, symbol, price, int(quantity))
        fill = sheet.fills.get(symbol) or {}
        done = int(fill.get("actual_quantity") or 0)
        planned = int(matched.quantity)
        if done < planned:
            sheet_note = (f"操作单 {sheet.sheet_id[:10]}… 该腿部分成交 "
                          f"{done}/{planned}（剩余 {planned - done}）。\n")
        else:
            sheet_note = f"操作单 {sheet.sheet_id[:10]}… 该腿已足额成交 {done}/{planned}。\n"
    elif sheet is not None:
        sheet_note = "（当日操作单无该腿——账本已记，单据状态不变）\n"
    else:
        sheet_note = "（今日无操作单——仅记账）\n"

    return (
        f"已回填：{symbol} {'买入' if side == 'buy' else '卖出'} "
        f"{quantity:g} @ {price:g}\n"
        f"{fee_note}\n"
        f"{sheet_note}"
        "真实账本已记录。用 /对比 查看模拟 vs 真实偏差。"
    )

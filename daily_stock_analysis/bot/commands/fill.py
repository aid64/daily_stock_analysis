# -*- coding: utf-8 -*-
"""
===================================
成交回填命令
===================================

/成交回填 代码 方向 数量 价格（CLOSURE-FIX-PLAN G5）

闭环最后一环：用户在券商 App 手动下单成交后，回填到系统——
① 操作单中匹配腿标记 filled ② PortfolioService 写真实账本
③ /对比 立即可见模拟 vs 真实偏差。
"""

from datetime import date
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


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
        return "回填真实成交（操作单标记 + 真实账本记录）"

    @property
    def usage(self) -> str:
        return "/成交回填 002475 买入 100 56.50"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        if len(args) < 4:
            return BotResponse.text_response(
                "参数不足。格式：/成交回填 代码 方向(买入/卖出) 数量 价格\n"
                "示例：/成交回填 002475 买入 100 56.50")
        symbol_raw, side_raw, qty_raw, price_raw = args[0], args[1], args[2], args[3]
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
            result = _do_fill(symbol_raw, side, quantity, price)
        except Exception as exc:  # noqa: BLE001 - 面向用户命令不抛栈
            return BotResponse.text_response(f"回填失败：{exc}")
        return BotResponse.text_response(result)


def _do_fill(symbol_raw: str, side: str, quantity: float, price: float) -> str:
    """回填核心逻辑（bot 命令与 API 共用）。"""
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
    today = date.today().isoformat()

    # ① 操作单标记（当日有单才标；没单也继续写账本——用户可能直接交易）
    sheet_note = ""
    sheet = load_latest_sheet(today)
    if sheet is not None:
        matched = next(
            (o for o in sheet.orders
             if o.symbol == symbol and o.direction == side), None)
        if matched is not None:
            mark_order_filled(sheet, symbol, price, int(quantity))
            sheet_note = f"操作单 {sheet.sheet_id[:10]}… 该腿已标 filled。\n"
        else:
            sheet_note = f"（当日操作单无 {symbol} {side} 腿——仍记账，但不影响单据状态）\n"
    else:
        sheet_note = "（今日无操作单——仅记账）\n"

    # ② 真实账本
    service = PortfolioService()
    account = get_or_create_default_account(service)
    if account is None:
        raise RuntimeError("持仓账户初始化失败")
    service.record_trade(
        account_id=int(account["account_id"]),
        symbol=symbol,
        trade_date=date.today(),
        side=side,
        quantity=quantity,
        price=price,
    )

    # ③ 卖出校验（PortfolioService 内部有 oversell 拦截）
    return (
        f"已回填：{symbol} {'买入' if side == 'buy' else '卖出'} "
        f"{quantity:g} @ {price:g}\n"
        f"{sheet_note}"
        "真实账本已记录。用 /对比 查看模拟 vs 真实偏差。"
    )

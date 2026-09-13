# -*- coding: utf-8 -*-
"""
===================================
撤单命令
===================================

/撤单 代码 [原因]（OPTIMIZATION-PLAN-V2 批次 A4）

FIX OrdStatus "Pending Cancel"/"Canceled" 语义：用户在券商 App 撤单后
回填撤单事实——未成交腿终止，已成交量保留（不回滚账本）。
"""

from datetime import date
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


class CancelOrderCommand(BotCommand):
    """撤单"""

    @property
    def name(self) -> str:
        return "cancel_order"

    @property
    def aliases(self) -> List[str]:
        return ["撤单", "cancel"]

    @property
    def description(self) -> str:
        return "登记撤单（未成交腿终止，已成交量保留）"

    @property
    def usage(self) -> str:
        return "/撤单 代码 [原因]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        if not args:
            return BotResponse.text_response(
                "参数不足。格式：/撤单 代码 [原因]\n示例：/撤单 SH113062 到价太快没跟上")
        symbol_raw = args[0]
        reason = " ".join(args[1:]).strip() or "用户撤单"
        try:
            return BotResponse.text_response(
                _do_cancel(symbol_raw, reason))
        except Exception as exc:  # noqa: BLE001
            return BotResponse.text_response(f"撤单登记失败：{exc}")


def _do_cancel(symbol_raw: str, reason: str) -> str:
    from src.services.cb_signal_bridge import canonical_symbol
    from src.services.order_sheet_service import (
        SHEET_STATUS_CANCELLED,
        SHEET_STATUS_PARTIALLY_FILLED,
        load_latest_sheet,
        save_order_sheet,
    )

    symbol = canonical_symbol(symbol_raw)
    sheet = load_latest_sheet(date.today().isoformat())
    if sheet is None:
        return f"今日无操作单，无需撤单登记。（原因留档：{reason}）"

    matched = next((o for o in sheet.orders
                    if o.symbol == symbol), None)
    if matched is None:
        return f"今日操作单无 {symbol} 腿，无需撤单。（原因留档：{reason}）"

    fill = sheet.fills.get(symbol) or {}
    done = int(fill.get("actual_quantity") or 0)
    planned = int(matched.quantity)

    # 单腿撤单：标记 fill.cancelled；若全部腿都无剩余量（已足额或已撤）
    # 则推进单据状态。
    sheet.fills.setdefault(symbol, {})
    sheet.fills[symbol]["cancelled"] = True
    sheet.fills[symbol]["cancel_reason"] = reason
    sheet.fills[symbol]["cancelled_at"] = (
        __import__("datetime").datetime.now().isoformat(timespec="seconds"))

    remaining_legs = []
    for o in sheet.orders:
        f = sheet.fills.get(o.symbol) or {}
        done_o = int(f.get("actual_quantity") or 0)
        if not f.get("cancelled") and done_o < int(o.quantity):
            remaining_legs.append(o.symbol)

    if done >= planned:
        note = f"{symbol} 已足额成交 {done}/{planned}，撤单无实际作用（留档）。"
    elif done > 0:
        sheet.status = SHEET_STATUS_CANCELLED  # 部分成交后撤剩余 → 终态
        note = (f"{symbol} 部分成交 {done}/{planned}，剩余 {planned - done} "
                f"已撤。已成交量保留在账本。")
    else:
        sheet.status = SHEET_STATUS_CANCELLED
        note = f"{symbol} 未成交即撤（{planned} 张/股全部取消）。"

    if not remaining_legs:
        sheet.status = SHEET_STATUS_CANCELLED
    save_order_sheet(sheet)
    return (f"✓ 撤单已登记：{symbol}（原因：{reason}）\n{note}\n"
            "决策快照将在下次 /操作单 或管线时刷新。")

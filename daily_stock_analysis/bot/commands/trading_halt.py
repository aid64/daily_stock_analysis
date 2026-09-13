# -*- coding: utf-8 -*-
"""
===================================
交易暂停 / 恢复命令
===================================

/暂停交易 [原因]   —— 业务级 kill switch：数据继续更新、健康继续可查，
                      但不再生成任何买卖指令（FINAL-MODIFICATION-PLAN P0.7）
/恢复交易          —— 清除暂停状态并刷新决策快照
"""

from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


class HaltTradingCommand(BotCommand):
    """暂停交易"""

    @property
    def name(self) -> str:
        return "halt_trading"

    @property
    def aliases(self) -> List[str]:
        return ["暂停交易", "暂停", "halt"]

    @property
    def description(self) -> str:
        return "暂停生成一切买卖指令（数据照常更新）"

    @property
    def usage(self) -> str:
        return "/暂停交易 [原因]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        try:
            from src.services.trading_halt_service import halt_trading

            reason = " ".join(args).strip() or "未提供原因"
            state = halt_trading(reason=reason)
            return BotResponse.text_response(
                "⛔ 交易已暂停。\n"
                f"原因：{state['reason']}\n"
                f"时间：{state['halted_at']}\n\n"
                "暂停期间：数据与监控继续运行，但 /操作单 不再生成买卖指令。\n"
                "恢复请发 /恢复交易")
        except Exception as exc:  # noqa: BLE001 - 面向用户命令不能抛栈
            return BotResponse.text_response(f"暂停失败：{exc}")


class ResumeTradingCommand(BotCommand):
    """恢复交易"""

    @property
    def name(self) -> str:
        return "resume_trading"

    @property
    def aliases(self) -> List[str]:
        return ["恢复交易", "恢复"]

    @property
    def description(self) -> str:
        return "恢复生成买卖指令（解除暂停）"

    @property
    def usage(self) -> str:
        return "/恢复交易"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        try:
            from src.services.trading_halt_service import resume_trading

            result = resume_trading()
            if result.get("note"):
                return BotResponse.text_response("✓ 当前本就未暂停，无需恢复。")
            prev = result.get("previous_halt") or {}
            return BotResponse.text_response(
                "✅ 交易已恢复。\n"
                f"此前暂停原因：{prev.get('reason', '?')}"
                f"（{prev.get('halted_at', '?')}）\n"
                "决策快照已刷新；下一张 /操作单 将正常生成。")
        except Exception as exc:  # noqa: BLE001
            return BotResponse.text_response(f"恢复失败：{exc}")

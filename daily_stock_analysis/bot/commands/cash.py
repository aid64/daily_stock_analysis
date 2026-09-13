# -*- coding: utf-8 -*-
"""
===================================
入金 / 出金命令
===================================

/入金 金额 [备注]   —— 登记转入账户的资金（修复 P0.4 的闭环缺口：
                       此前 /持仓登记 只支持买卖，入金无命令可走）
/出金 金额 [备注]   —— 登记转出

后端复用 PortfolioService.record_cash_ledger（单一现金真源）；
登记成功后自动刷新决策快照——现金闸门状态即时更新。
"""

from datetime import date
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


class CashDepositCommand(BotCommand):
    """入金登记"""

    @property
    def name(self) -> str:
        return "cash_in"

    @property
    def aliases(self) -> List[str]:
        return ["入金", "转入", "deposit"]

    @property
    def description(self) -> str:
        return "登记转入账户的资金（解除现金闸门）"

    @property
    def usage(self) -> str:
        return "/入金 金额 [备注]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        return self._run(args, direction="in", verb="入金")

    def _run(self, args: List[str], *, direction: str, verb: str) -> BotResponse:
        if not args:
            return BotResponse.text_response(
                f"参数不足。格式：/{verb} 金额 [备注]\n"
                f"示例：/入金 24000 转入本金")
        try:
            amount = float(args[0])
        except ValueError:
            return BotResponse.text_response(f"金额必须是数字，收到：{args[0]}")
        if amount <= 0:
            return BotResponse.text_response("金额必须大于 0")
        note = " ".join(args[1:]).strip() or None
        try:
            from src.services.portfolio_analysis_context import (
                get_or_create_default_account)
            from src.services.portfolio_service import PortfolioService

            service = PortfolioService()
            account = get_or_create_default_account(service)
            if account is None:
                raise RuntimeError("持仓账户初始化失败")
            service.record_cash_ledger(
                account_id=int(account["account_id"]),
                event_date=date.today(),
                direction=direction,
                amount=amount,
                note=note,
            )
        except Exception as exc:  # noqa: BLE001 - 面向用户命令不抛栈
            return BotResponse.text_response(f"{verb}登记失败：{exc}")

        # 现金变动 → 立即刷新决策快照（现金闸门状态即时更新）
        try:
            from src.services.decision_snapshot_service import refresh
            refresh()
        except Exception:  # noqa: BLE001
            pass

        snapshot = service.get_portfolio_snapshot(cost_method="fifo",
                                                  include_realtime=False)
        cash = float(snapshot.get("total_cash") or 0)
        gate = ""
        if cash < 0:
            gate = "\n⚠ 现金仍为负——请核对是否还有未登记的入金。"
        return BotResponse.text_response(
            f"✅ {verb}已登记：¥{amount:,.2f}"
            + (f"（{note}）" if note else "")
            + f"\n当前账面可用现金：¥{cash:,.2f}{gate}\n"
            "重跑 /操作单 可按新现金生成买入张数。")


class CashWithdrawCommand(CashDepositCommand):
    """出金登记"""

    @property
    def name(self) -> str:
        return "cash_out"

    @property
    def aliases(self) -> List[str]:
        return ["出金", "转出", "withdraw"]

    @property
    def description(self) -> str:
        return "登记从账户转出的资金"

    @property
    def usage(self) -> str:
        return "/出金 金额 [备注]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        return self._run(args, direction="out", verb="出金")

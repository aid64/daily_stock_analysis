# -*- coding: utf-8 -*-
"""
===================================
持仓命令
===================================

查看当前持仓，登记买入/卖出交易。

P0-A：此前用户在对话里说"我持仓某股票"没有落地机制——信息只活在
当次会话里，定时分析/推送完全不感知。本命令把持仓落到
PortfolioService（DB 持久化），主链路注入 pipeline 后报告的
"持仓者建议"基于真实持仓。
"""

from datetime import date
from typing import List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


def _format_error(exc: Exception) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


class PortfolioCommand(BotCommand):
    """持仓查看与登记"""

    @property
    def name(self) -> str:
        return "portfolio"

    @property
    def aliases(self) -> List[str]:
        return ["持仓", "持仓列表", "positions"]

    @property
    def description(self) -> str:
        return "查看当前持仓"

    @property
    def usage(self) -> str:
        return "/持仓 | /持仓登记 600519 买入 100 1820.00"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        from src.services.portfolio_analysis_context import list_positions_text

        return BotResponse.text_response(list_positions_text())


class PortfolioRecordCommand(BotCommand):
    """登记一笔买卖交易：/持仓登记 代码 方向 数量 价格 [费用]"""

    @property
    def name(self) -> str:
        return "portfolio_record"

    @property
    def aliases(self) -> List[str]:
        return ["持仓登记", "记持仓", "记账"]

    @property
    def description(self) -> str:
        return "登记买入/卖出交易（写入持仓数据库，影响后续报告的持仓建议）"

    @property
    def usage(self) -> str:
        return "/持仓登记 600519 买入 100 1820.00 [手续费]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        from src.services.portfolio_analysis_context import get_or_create_default_account
        from src.services.portfolio_service import PortfolioService

        if len(args) < 4:
            return BotResponse.text_response(
                "参数不足。格式：/持仓登记 代码 方向(买入/卖出) 数量 价格 [手续费]\n"
                "示例：/持仓登记 600519 买入 100 1820.00 5"
            )
        symbol, side_raw, qty_raw, price_raw = args[0], args[1], args[2], args[3]
        side_map = {"买入": "buy", "卖出": "sell", "buy": "buy", "sell": "sell", "b": "buy", "s": "sell"}
        side = side_map.get(side_raw.strip().lower())
        if side is None:
            return BotResponse.text_response(f"方向必须是 买入/卖出，收到：{side_raw}")
        try:
            quantity = float(qty_raw)
            price = float(price_raw)
            fee = float(args[4]) if len(args) > 4 else 0.0
        except ValueError:
            return BotResponse.text_response("数量/价格/费用必须是数字")

        service = PortfolioService()
        try:
            account = get_or_create_default_account(service)
            if not account:
                return BotResponse.text_response("持仓账户初始化失败，请稍后重试")
            result = service.record_trade(
                account_id=int(account["account_id"]),
                symbol=symbol,
                trade_date=date.today(),
                side=side,
                quantity=quantity,
                price=price,
                fee=fee,
            )
        except ValueError as exc:
            return BotResponse.text_response(f"登记失败：{_format_error(exc)}")
        except Exception as exc:  # noqa: BLE001 - 面向用户的命令不能抛栈
            return BotResponse.text_response(f"登记失败：{_format_error(exc)}")

        symbol_out = result.get("symbol") or symbol
        return BotResponse.text_response(
            f"已登记：{side_raw} {symbol_out} {quantity:g}股 @ {price:g}"
            + (f"（费用 {fee:g}）" if fee else "")
            + f"\n账户：{account.get('account_name')}\n"
            "用 /持仓 查看全部；下次分析将按真实持仓给出建议。"
        )


class PortfolioClearCommand(BotCommand):
    """清空默认账户的持仓（重新开始记录用）"""

    @property
    def name(self) -> str:
        return "portfolio_clear"

    @property
    def aliases(self) -> List[str]:
        return ["清空持仓"]

    @property
    def description(self) -> str:
        return "清空默认账户的登记持仓（按持仓逐只反向卖出，恢复空仓）"

    @property
    def usage(self) -> str:
        return "/清空持仓"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        from src.services.portfolio_analysis_context import get_or_create_default_account
        from src.services.portfolio_service import (
            PortfolioOversellError,
            PortfolioService,
        )

        service = PortfolioService()
        try:
            account = get_or_create_default_account(service)
            if not account:
                return BotResponse.text_response("持仓账户初始化失败，请稍后重试")
            account_id = int(account["account_id"])
            snapshot = service.get_portfolio_snapshot(account_id=account_id, cost_method="fifo")
            positions = [
                position
                for account_row in snapshot.get("accounts") or []
                for position in account_row.get("positions") or []
            ]
            cleared, errors = [], []
            for position in positions:
                try:
                    quantity = float(position.get("quantity") or 0)
                except (TypeError, ValueError):
                    quantity = 0.0
                if quantity <= 0:
                    continue
                price = position.get("avg_cost") or position.get("last_price") or 1.0
                try:
                    service.record_trade(
                        account_id=account_id,
                        symbol=str(position.get("symbol") or ""),
                        trade_date=date.today(),
                        side="sell",
                        quantity=quantity,
                        price=float(price),
                        note="portfolio_clear 反向平仓",
                    )
                    cleared.append(str(position.get("symbol")))
                except (ValueError, PortfolioOversellError) as exc:
                    errors.append(f"{position.get('symbol')}: {exc}")
            if errors:
                return BotResponse.text_response(
                    "部分清仓失败：" + "；".join(errors)
                )
            return BotResponse.text_response(
                f"已清空 {len(cleared)} 只持仓记录。" if cleared else "当前无持仓记录。"
            )
        except Exception as exc:  # noqa: BLE001
            return BotResponse.text_response(f"清空失败：{_format_error(exc)}")


def _unused_optional() -> Optional[int]:  # pragma: no cover - 占位保持类型导入
    return None

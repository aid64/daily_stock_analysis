# -*- coding: utf-8 -*-
"""
===================================
对比命令
===================================

模拟账本 vs 真实账本逐笔对比（蓝图 ③-B）：
"要不要信它"的量化依据——每张操作单的假设成交与用户回填的真实成交
偏差（价格、费用），累计可见。
"""

from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


class CompareCommand(BotCommand):
    """模拟 vs 真实对比"""

    @property
    def name(self) -> str:
        return "compare"

    @property
    def aliases(self) -> List[str]:
        return ["对比", "模拟对比", "信不信"]

    @property
    def description(self) -> str:
        return "模拟账本 vs 真实成交逐笔对比；/对比 周报 看执行率/滑点/放弃率"

    @property
    def usage(self) -> str:
        return "/对比 [周报]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        from src.services.simulation_ledger import SimulationLedger

        try:
            if args and args[0].strip() in ("周报", "weekly", "影子"):
                # 方案v2 第三批：影子期执行度量（近 4 周聚合）
                from src.services.order_sheet_service import SHEETS_DIR
                from src.services.shadow_metrics_service import (
                    collect_shadow_metrics, render_weekly)

                proxy = _RealLedgerProxy(ledger_path=None)
                trades = []
                page = 1
                while page <= 10:  # 分页取全量（上限 2000 条）
                    batch = proxy.list_trade_events(page=page, page_size=200)
                    items = batch.get("items") or batch.get("events") or []
                    trades.extend(items)
                    if len(items) < 200:
                        break
                    page += 1
                report = collect_shadow_metrics(
                    sheets_dir=SHEETS_DIR, real_trades=trades)
                text = ("【影子期执行周报（近 4 周）】\n"
                        + render_weekly(report))
            else:
                ledger = SimulationLedger()
                comparison = ledger.compare_with_real(
                    _RealLedgerProxy(ledger_path=None))
                text = ledger.render_comparison_text(comparison)
        except Exception as exc:  # noqa: BLE001 - 面向用户命令不抛栈
            text = f"对比失败：{exc}"
        return BotResponse.text_response(text)


class _RealLedgerProxy:
    """真实账本代理：复用 P0-A 的默认账户自举，隔离环境由调用方保证"""

    def __init__(self, ledger_path=None):
        pass

    def list_trade_events(self, **kw):
        from src.services.portfolio_analysis_context import (
            get_or_create_default_account)
        from src.services.portfolio_service import PortfolioService

        service = PortfolioService()
        account = get_or_create_default_account(service)
        if account is not None and "account_id" not in kw:
            kw["account_id"] = int(account["account_id"])
        return service.list_trade_events(**kw)

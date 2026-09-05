# -*- coding: utf-8 -*-
"""
===================================
操作单命令
===================================

生成 / 查看当日操作单（蓝图 ①-C.3）。

数据链：quant_research 信号契约（current_plan.json）→ 仓位目标 →
当前持仓 diff（order_sheet_service）→ 文本渲染。
信号契约缺失时诚实降级为"信号源不可用"，不虚构操作。
"""

from datetime import date
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse


def _build_sheet_text() -> str:
    from src.services.cb_signal_bridge import (
        canonical_symbol,
        cb_plan_to_targets,
        load_cb_plan,
    )
    from src.services.order_sheet_service import (
        generate_order_sheet,
        load_latest_sheet,
        render_text,
    )
    from src.services.portfolio_analysis_context import _service
    from src.services.portfolio_service import PortfolioService

    # 已有当日单 → 直接展示（幂等，避免每次重算产生新 sheet_id）
    existing = load_latest_sheet(date.today().isoformat())
    if existing is not None and existing.status == "pending":
        return render_text(existing)

    plan = load_cb_plan()
    if plan is None:
        return (
            "信号源不可用：未找到 quant_research 信号契约"
            "（current_plan.json）。\n"
            "请在 quant_research/ 下运行：\n"
            "  py scripts/run_paper_tracking.py --export-plan"
        )

    service = PortfolioService()
    snapshot = service.get_portfolio_snapshot(cost_method="fifo",
                                              include_realtime=False)
    current_positions: dict = {}
    for account in snapshot.get("accounts") or []:
        for position in account.get("positions") or []:
            try:
                qty = float(position.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty > 0:
                current_positions[str(position.get("symbol"))] = qty

    # 信号源无报价的条目 = 可能已强赎/退市——必须显式示警，不许静默跳过
    stale_symbols = [
        symbol for symbol, payload in (plan.get("targets") or {}).items()
        if not payload or not payload.get("t1_close")
    ]

    targets = cb_plan_to_targets(plan)
    prices = {
        canonical_symbol(symbol): float(payload.get("t1_close") or 0)
        for symbol, payload in (plan.get("targets") or {}).items()
    }
    cash = float(snapshot.get("total_cash") or 0)

    sheet = generate_order_sheet(
        trade_date=date.today(),
        current_positions=current_positions,
        target_positions=targets,
        t1_close_prices=prices,
        available_cash=cash,
    )
    from src.services.order_sheet_service import save_order_sheet

    save_order_sheet(sheet)
    text = render_text(sheet)
    if stale_symbols:
        text += (
            "\n⚠ 以下信号标的无报价（可能已强赎/退市），若仍持有请人工核实："
            + "、".join(sorted(stale_symbols))
        )
    return text


class OrderSheetCommand(BotCommand):
    """当日操作单"""

    @property
    def name(self) -> str:
        return "ordersheet"

    @property
    def aliases(self) -> List[str]:
        return ["操作单", "今日操作", "单子"]

    @property
    def description(self) -> str:
        return "生成/查看今日操作单（具体限价 + 放弃条件）"

    @property
    def usage(self) -> str:
        return "/操作单"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        try:
            return BotResponse.text_response(_build_sheet_text())
        except Exception as exc:  # noqa: BLE001 - 面向用户的命令不能抛栈
            return BotResponse.text_response(f"操作单生成失败：{exc}")

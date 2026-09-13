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

import pandas as pd
from datetime import date
from typing import List

import logging

from pathlib import Path

from bot.commands.base import BotCommand

logger = logging.getLogger(__name__)
from bot.models import BotMessage, BotResponse

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _is_convertible(symbol: str) -> bool:
    """11/12 开头的 6 位代码 = 转债；其余（股票/未知）一律按非转债处理。"""
    digits = "".join(ch for ch in str(symbol) if ch.isdigit())
    return len(digits) == 6 and digits.startswith(("11", "12"))


def _personal_stock_note(stocks: dict) -> str:
    """个人股票持仓的参考价行（只读展示，不产生任何指令）。

    价格走完整快照（include_realtime=True）：盘中取实时报价，盘后
    自动退回最近收盘价——与转债侧 T-1 收盘的"参考价"语义一致。
    同时展示持仓成本与浮动盈亏（用户反馈：只显示现价会让人误读，
    "当时的买价不是现价"）。
    """
    if not stocks:
        return ""
    from src.services.portfolio_service import PortfolioService

    lines = ["\n—— 个人持仓（策略未覆盖，仅展示不下单）——"]
    try:
        snapshot = PortfolioService().get_portfolio_snapshot(
            cost_method="fifo", include_realtime=True)
    except Exception as exc:  # noqa: BLE001 - 报价失败只降级展示
        logger.warning("个人持仓参考价获取失败: %r", exc)
        snapshot = None
    info: dict = {}
    if snapshot:
        for account in snapshot.get("accounts") or []:
            for position in account.get("positions") or []:
                if position.get("price_available"):
                    info[str(position.get("symbol"))] = {
                        "price": float(position.get("last_price") or 0),
                        "price_date": str(position.get("price_date") or ""),
                        "avg_cost": float(position.get("avg_cost") or 0),
                    }
    for symbol in sorted(stocks):
        qty = int(stocks[symbol])
        row = info.get(symbol, {})
        price = row.get("price", 0.0)
        price_date = row.get("price_date", "")
        cost = row.get("avg_cost", 0.0)
        if price > 0:
            line = (f"· {symbol} {qty}股 · 现价 {price:g}（{price_date or '实时'}）"
                    f" · 市值 ¥{qty * price:,.0f}")
            if cost > 0:
                pnl = (price - cost) * qty
                pnl_pct = (price - cost) / cost * 100
                sign = "+" if pnl >= 0 else ""
                line += (f"\n  成本 {cost:g} · 浮动盈亏 {sign}{pnl:,.0f} 元"
                         f"（{sign}{pnl_pct:.1f}%）")
            lines.append(line)
        else:
            lines.append(f"· {symbol} {qty}股 · 无报价，请人工核实")
    return "\n".join(lines)


def _plan_freshness_message(plan: dict) -> str:
    """信号新鲜度门槛（FINAL-SOLUTION 0.3）：过期时拒绝生成指令而非渲染旧计划。

    评估逻辑唯一实现在 decision_snapshot_service（快照/健康共用同一口径）。
    """
    from src.services.decision_snapshot_service import evaluate_plan_freshness

    _fresh, message = evaluate_plan_freshness(plan)
    return message


def _current_gate_notes(snapshot: dict) -> str:
    """当前闸门状态（暂停/现金），供幂等旧单与新单共用。"""
    notes = []
    try:
        from src.services.trading_halt_service import halt_status
        if halt_status().get("halted"):
            s = halt_status()
            notes.append(
                f"⛔ 交易已暂停（{s.get('halted_at', '?')}，原因：{s.get('reason', '?')}）"
                "——今日不生成买卖指令。恢复请发 /恢复交易")
    except Exception as exc:  # noqa: BLE001
        logger.warning("交易暂停状态读取失败: %r", exc)
    cash = float(snapshot.get("total_cash") or 0)
    if cash < 0:
        notes.append(
            f"⛔ 现金闸门：可用现金 ¥{cash:,.2f} 为负——账户缺资金入账记录，"
            "买入目标降级为人工核验。请通过 /持仓登记 补记入金。")
    elif cash <= 0:
        notes.append(
            "⛔ 现金闸门：可用现金未登记（¥0）——买入目标无法给出张数，"
            "请先 /持仓登记 记账。")
    return "\n".join(notes)


def _refresh_snapshot_quietly() -> None:
    """刷新决策快照（异常安全：快照失败不影响操作单主流程）。"""
    try:
        from src.services.decision_snapshot_service import refresh
        refresh()
    except Exception as exc:  # noqa: BLE001
        logger.warning("决策快照刷新失败: %r", exc)


def _build_sheet_text() -> str:
    from src.services.cb_signal_bridge import (
        canonical_symbol,
        cb_plan_to_targets,
        load_cb_plan,
    )
    from src.services.order_sheet_service import (
        _unit_label,
        generate_order_sheet,
        load_latest_sheet,
        render_text,
    )
    from src.services.portfolio_analysis_context import _service
    from src.services.portfolio_service import PortfolioService

    # 已有当日单：若信号契约没有更新则只展示同一版本，避免重复生成；
    # 契约刷新后允许重建并由通知总线按 logical_id 替换旧摘要。
    existing = load_latest_sheet(date.today().isoformat())
    plan = load_cb_plan()

    # 新鲜度门槛：过期契约宁可"不生成指令"，绝不渲染陈旧计划；
    # 拒绝路径也刷新快照（把"系统今天说了不能执行"一并留档）。
    if plan is not None:
        gate = _plan_freshness_message(plan)
        if gate:
            _refresh_snapshot_quietly()
            return gate

    if existing is not None and existing.status == "pending":
        plan_generated_at = str((plan or {}).get("generated_at") or "")
        if not plan_generated_at or plan_generated_at <= str(existing.created_at or ""):
            service = PortfolioService()
            snapshot = service.get_portfolio_snapshot(cost_method="fifo",
                                                      include_realtime=False)
            stocks = {}
            for account in snapshot.get("accounts") or []:
                for position in account.get("positions") or []:
                    symbol = str(position.get("symbol") or "")
                    quantity = float(position.get("quantity") or 0)
                    if quantity > 0 and not _is_convertible(symbol):
                        stocks[symbol] = quantity
            # R0：旧 pending 单在闸门命中时同样不可执行——提示存在但旧单
            # 上下文改为"不可执行（闸门命中）"，orders 不再以可执行形态展示。
            gates = _current_gate_notes(snapshot)
            if gates:
                note = (gates + "\n⛔ 以下旧单当前不可执行（闸门命中），"
                        "仅为当日早前版本留档：")
                return note + "\n" + render_text(existing) + \
                    _personal_stock_note(stocks)
            return render_text(existing) + _personal_stock_note(stocks)

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

    # 股票持仓不在信号契约管辖内（契约只有转债目标）：若混入 target diff，
    # "目标 0 vs 当前 N" 会被误判成整仓卖单。拆出来单独展示参考价，
    # 绝不下达指令——等股票策略上线后再纳入 diff。
    personal_stocks = {
        symbol: qty for symbol, qty in current_positions.items()
        if not _is_convertible(symbol)
    }
    cb_positions = {
        symbol: qty for symbol, qty in current_positions.items()
        if _is_convertible(symbol)
    }

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

    # P0.4 现金硬闸门 + P0.7 暂停 + R0 语义修正：
    # 闸门命中 = 只读观察模式。此前实现把 targets 清空后仍调用生成器，
    # 生成器会把"目标缺席"解释为目标 0 → 已有转债持仓被生成清仓卖单
    # （暂停反而触发卖出，与文案矛盾）。正确语义：闸门命中时不进入
    # target diff，直接渲染"观察视图"——展示当前持仓与原因，零指令。
    cash_gate_note = ""
    gate_hit = False
    if cash < 0:
        cash_gate_note = (
            f"⛔ 现金闸门：可用现金 ¥{cash:,.2f} 为负——账户缺资金入账记录，"
            "今日进入只读观察（不生成任何买卖指令）。\n"
            "请补记入金（现金事件）后重跑 /操作单。")
        gate_hit = True
    elif cash <= 0:
        cash_gate_note = (
            "⛔ 现金闸门：可用现金未登记（¥0）——买入目标无法给出张数。\n"
            "请先登记入金，再重跑 /操作单。")
        gate_hit = True

    # P0.7 交易暂停（业务级 kill switch）：数据照常，但不出任何指令
    halt_note = ""
    try:
        from src.services.trading_halt_service import halt_status
        halt_state = halt_status()
        if halt_state.get("halted"):
            halt_note = (
                f"⛔ 交易已暂停（{halt_state.get('halted_at', '?')}，"
                f"原因：{halt_state.get('reason', '?')}）——今日不生成买卖指令。\n"
                "恢复请发 /恢复交易")
            gate_hit = True
    except Exception as exc:  # noqa: BLE001 - 状态不可读 = unknown，阻断而非放行
        logger.warning("交易暂停状态读取失败，按阻断处理: %r", exc)
        halt_note = "⛔ 交易暂停状态不可读（unknown）——今日不生成买卖指令。"
        gate_hit = True

    if gate_hit:
        # 观察视图：不进 target diff，零指令；持有标的与参考价仍展示。
        observe_lines = [
            f"【观察模式 {date.today().isoformat()}——闸门命中，不生成买卖指令】"]
        for symbol in sorted(cb_positions):
            price = prices.get(symbol)
            qty = int(cb_positions[symbol])
            if price and price > 0:
                observe_lines.append(
                    f"· 持有 {symbol} {qty}{_unit_label(symbol)}"
                    f" · 参考价 {price:g} · 市值约 ¥{qty * price:,.0f}")
            else:
                observe_lines.append(
                    f"· 持有 {symbol} {qty}{_unit_label(symbol)} · 无有效参考价")
        observe_text = "\n".join(observe_lines)
        observe_text += _personal_stock_note(personal_stocks)
        try:
            from src.services.decision_snapshot_service import refresh
            refresh()
        except Exception:  # noqa: BLE001
            pass
        notes = [n for n in (halt_note, cash_gate_note) if n]
        return "\n".join(notes) + "\n" + observe_text

    sheet = generate_order_sheet(
        trade_date=date.today(),
        current_positions=cb_positions,
        target_positions=targets,
        t1_close_prices=prices,
        available_cash=cash,
    )
    from src.services.order_sheet_service import save_order_sheet
    from src.services.simulation_ledger import SimulationLedger

    # E2 择时信号：估值分位高位时提示减仓。
    # C2（OPTIMIZATION-PLAN-V2）：内联计算 expanding 分位——与 /健康 同一
    # 算法同一 PIT。此前 import quant_research.* 因外层/内层包名遮蔽
    # （scripts 容器 vs 真包）在 server 进程内必炸且被 except 吞掉，择时行
    # 曾静默消失。
    timing_note = ""
    try:
        _pit_path = (_PROJECT_ROOT.parent / "quant_research" / "data" / "pit"
                     / "convertible_pit.parquet")
        if _pit_path.exists():
            _pit = pd.read_parquet(_pit_path, columns=["as_of", "price"])
            # G6: 动态截断——跟随 current_plan 的 pit_end（PIT 增量后自动更新）
            _pit_end = plan.get("pit_end") if plan else None
            if _pit_end:
                _pit = _pit[_pit["as_of"] <= pd.Timestamp(_pit_end)]
            _median = _pit.groupby("as_of")["price"].median().sort_index()
            if not _median.empty:
                _cur = float((_median <= _median.iloc[-1]).mean())
                if _cur > 0.80:
                    timing_note = (
                        f"\n⚠ E2 择时信号：估值分位 {_cur:.1%}（>80%，截至 "
                        f"{str(_median.index[-1])[:10]}）"
                        "——建议减半仓，新增买入仅限防守标的"
                    )
    except Exception as exc:  # noqa: BLE001 - 择时失败记日志不阻塞
        logger.warning("E2 择时行计算失败: %r", exc)

    save_order_sheet(sheet)
    # ③-A：操作单生成即记模拟成交（幂等），真实侧等用户回填后 /对比 查看
    try:
        SimulationLedger().record_sheet(sheet)
    except Exception as exc:  # noqa: BLE001 - 账本失败不影响操作单展示
        logger.warning("模拟账本记录失败: %r", exc)
    text = render_text(sheet)
    if halt_note:
        text = halt_note + "\n" + text
    if cash_gate_note:
        text += "\n" + cash_gate_note
    if timing_note:
        text += timing_note
    blocked = plan.get("blocked") or {}
    if blocked:
        lines = [f"· {sym}：{reason}" for sym, reason in sorted(blocked.items())]
        text += ("\n⛔ 生命周期阻断（已从目标剔除，不产生指令）：\n" + "\n".join(lines))
    # E3 信号状态汇总：让"单子上有什么/为什么"自解释
    n_exec = len(sheet.orders) + len(sheet.holds)
    states = [f"executable×{n_exec}"]
    if blocked:
        states.append(f"blocked×{len(blocked)}")
    manual_review = plan.get("manual_review") or {}
    if manual_review:
        states.append(f"manual_review×{len(manual_review)}")
        mr_lines = [f"· {sym}：{reason}" for sym, reason in sorted(manual_review.items())]
        text += ("\n⚠ 人工核实（无有效价格，不产生指令）：\n" + "\n".join(mr_lines))
    text += f"\n—— 信号状态：{' · '.join(states)} ——"
    if stale_symbols:
        text += (
            "\n⚠ 以下信号标的无报价（可能已强赎/退市），若仍持有请人工核实："
            + "、".join(sorted(stale_symbols))
        )
    text += _personal_stock_note(personal_stocks)
    _refresh_snapshot_quietly()
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

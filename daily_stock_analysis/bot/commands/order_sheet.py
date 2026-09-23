# -*- coding: utf-8 -*-
"""
===================================
操作单命令
===================================

生成 / 查看当日账户决策包（FINAL-PLAN-ACCOUNT-CENTRIC R1）。

决策对象 = 用户真实持仓 + 现金（非 CB 策略目标）。
数据链：持仓快照（PortfolioService）→ 用户价格规则 + 集中度 + E2 择时
（account_decision_service）→ 决策包 → 操作单状态机（order_sheet_service）。
CB 契约只作为研究观察附注，不指导操作。
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _render_history(day: str | None = None) -> str:
    """/操作单 历史 [日期]：时间点查询（E2 终局）——读归档决策快照，
    回放"系统那天说了什么"。快照不可变（content_hash 防篡改）。"""
    from src.services.decision_snapshot_service import SNAPSHOT_DIR
    from src.services.order_sheet_service import _unit_label

    archive = SNAPSHOT_DIR / "archive"
    if not archive.exists():
        return "暂无历史归档快照。"
    if day:
        f = archive / f"{day}.json"
        if not f.exists():
            return f"{day} 无归档快照（仅保留 90 天）。"
        days_to_show = [day]
    else:
        days_to_show = sorted(
            (f.stem for f in archive.glob("????-??-??.json")), reverse=True)[:7]
        if not days_to_show:
            return "暂无历史归档快照。"
    lines: List[str] = ["—— 决策快照历史（content_hash 防篡改）——"]
    for d in days_to_show:
        try:
            snap = json.loads((archive / f"{d}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            lines.append(f"· {d}: 归档损坏")
            continue
        plan = snap.get("plan") or {}
        sheet = snap.get("order_sheet") or {}
        quality = snap.get("quality") or {}
        orders = sheet.get("orders") or []
        blocked = plan.get("blocked") or {}
        verdict = ("交易" if orders else
                   "不交易/仅观察")
        reasons = [r for r in (quality.get("warnings") or [])][:1]
        line = (f"· {d} [{snap.get('snapshot_id', '?')[:15]}] "
                f"结论:{verdict} · 订单 {len(orders)} 条 · "
                f"PIT {plan.get('pit_end', '?')}")
        if blocked:
            line += f" · 阻断 {len(blocked)}"
        if reasons:
            line += f" · {reasons[0][:40]}"
        lines.append(line)
        for o in orders:
            arrow = "买入" if o.get("direction") == "buy" else "卖出"
            lines.append(
                f"    {arrow} {o.get('symbol')} {o.get('quantity')}"
                f"{_unit_label(o.get('symbol', ''))} @{o.get('limit_price')}")
        if not orders and sheet:
            lines.append("    （当日无订单）")
    lines.append("\n校验：任意一天可用 content_hash 重算检测是否被改。")
    return "\n".join(lines)


def _refresh_snapshot_quietly() -> None:
    """刷新决策快照（异常安全：快照失败不影响操作单主流程）。"""
    try:
        from src.services.decision_snapshot_service import refresh
        refresh()
    except Exception as exc:  # noqa: BLE001
        logger.warning("决策快照刷新失败: %r", exc)


def _cb_research_appendix() -> str:
    """CB 策略研究观察附注（FINAL-PLAN-ACCOUNT-CENTRIC R1.3）。

    策略目标不再是用户操作信号——账户决策包才是。此处只保留
    paper tracking 的状态摘要，让"研究在跑、与本单无关"可见。
    """
    from src.services.cb_signal_bridge import load_cb_plan

    try:
        plan = load_cb_plan()
    except Exception as exc:  # noqa: BLE001
        plan = None
        logger.warning("CB 信号契约读取失败: %r", exc)
    if not plan:
        return ("\n—— 研究观察：CB 策略信号源不可用（paper tracking 契约缺失）"
                "——不影响上方账户决策 ——")

    targets = plan.get("targets") or {}
    pit_end = str(plan.get("pit_end") or "?")
    reb = str(plan.get("latest_rebalance") or "?")
    age_text = ""
    try:
        if reb and len(reb) >= 10:
            age_days = (date.today() - date.fromisoformat(reb[:10])).days
            age_text = f"，已 {age_days} 天无新调仓信号"
    except ValueError:
        pass
    blocked = plan.get("blocked") or {}
    lines = [
        f"\n—— 研究观察（CB 策略参考，不构成操作指令）——",
        f"· 模拟组合目标 {len(targets)} 只 · PIT 截至 {pit_end}"
        f" · 最近调仓 {reb[:10]}{age_text}",
    ]
    if blocked:
        lines.append(f"· 生命周期阻断 {len(blocked)} 只（强赎/退市/停牌，已从模拟组合剔除）")
    lines.append("· 该策略仍在影子验证期（未通过实盘门槛），目标仅作研究记录")
    return "\n".join(lines)


def _first_fill_hint(service) -> str:
    """P1.5 首笔引导（v4.2 发现#4）：零执行期间明确"如何完成第一笔回填"。

    实测操作单连续 9 天 pending/fills=0——闭环代码可用但用户从未完成一次
    回填。账户没有任何真实成交记录时，决策包尾部给出一次性操作指引；
    查询失败静默降级（不阻塞决策包）。
    """
    try:
        if service.list_trade_events(page=1, page_size=1).get("total", 0) == 0:
            return ("\n💡 首笔回填指引：本账户还没有任何真实成交记录。"
                    "任一待办动作在券商 App 成交后，把卡片里的"
                    "『成交后发送』命令原样发给我即可记账；"
                    "首次回填后 /对比 将开始量化模拟与真实的偏差。")
    except Exception as exc:  # noqa: BLE001 - 查询失败不阻塞决策包
        logger.warning("首笔回填状态查询失败: %r", exc)
    return ""


def _build_sheet_text() -> str:
    """账户决策包（FINAL-PLAN-ACCOUNT-CENTRIC R1）：真实持仓+现金 → 决策。

    决策主体 = 用户规则（止损/止盈）+ 集中度 + E2 择时 + AI 解释；
    CB 策略目标降级为研究观察附注。复用 order_sheet 状态机承载
    卖出/减仓指令（/成交回填、/撤单、成本门、影子期指标全部兼容）。
    """
    from src.services.account_decision_service import (
        build_account_decision,
        decision_to_sheet,
        load_price_rules,
        render_decision_package,
    )
    from src.services.order_sheet_service import (
        load_latest_sheet,
        render_text,
        save_order_sheet,
    )
    from src.services.portfolio_service import PortfolioService
    from src.services.simulation_ledger import SimulationLedger

    service = PortfolioService()
    snapshot = service.get_portfolio_snapshot(cost_method="fifo",
                                              include_realtime=True)

    # P1.5 首笔引导（v4.2 发现#4）：零执行期间明确"如何完成第一笔回填"
    first_fill_note = _first_fill_hint(service)

    # 交易暂停（业务级 kill switch）：数据照常，零指令
    halt_note = ""
    try:
        from src.services.trading_halt_service import halt_status
        halt_state = halt_status()
        if halt_state.get("halted"):
            halt_note = (
                f"⛔ 交易已暂停（{halt_state.get('halted_at', '?')}，"
                f"原因：{halt_state.get('reason', '?')}）——今日不生成买卖指令。\n"
                "恢复请发 /恢复交易")
    except Exception as exc:  # noqa: BLE001 - 状态不可读 = unknown，阻断而非放行
        logger.warning("交易暂停状态读取失败，按阻断处理: %r", exc)
        halt_note = "⛔ 交易暂停状态不可读（unknown）——今日不生成买卖指令。"

    # 当日已有 pending / partially_filled 单：沿用（当日早晨决策治理全天；
    # 跨日自动过期）。partially_filled 必须同样沿用——"卖100留100"的半仓
    # 流程使部分成交成为预期路径，重跑 /操作单 不能另存新单叠加（审视第 8 条）
    from src.services.order_sheet_service import (
        SHEET_STATUS_PARTIALLY_FILLED,
        SHEET_STATUS_PENDING,
    )
    existing = load_latest_sheet(date.today().isoformat())
    if (existing is not None
            and existing.status in (SHEET_STATUS_PENDING,
                                    SHEET_STATUS_PARTIALLY_FILLED)
            and not halt_note):
        decision = build_account_decision(snapshot=snapshot,
                                          rules=load_price_rules())
        text = render_decision_package(decision, sheet=existing)
        text += _cb_research_appendix()
        if first_fill_note:
            text += first_fill_note
        if halt_note:
            text = halt_note + "\n" + text
        _refresh_snapshot_quietly()
        return text

    if halt_note:
        # 暂停：只读观察——决策包照常计算但只渲染结论，不落单
        decision = build_account_decision(snapshot=snapshot,
                                          rules=load_price_rules())
        text = render_decision_package(decision, sheet=None)
        text = (halt_note + "\n（以下为只读决策视图，今日不产生可执行指令）\n"
                + text + _cb_research_appendix())
        _refresh_snapshot_quietly()
        return text

    # 现金闸门（R0 语义修正）：卖出/减仓不依赖现金，负现金只提示记账问题
    cash = float(snapshot.get("total_cash") or 0)
    cash_note = ""
    if cash < 0:
        cash_note = ("\n⚠ 现金闸门：可用现金 ¥{:,.2f} 为负——账户缺资金入账记录。"
                     "卖出/减仓不受影响；请通过 /入金 或 /持仓登记 补记。".format(cash))

    # 账户决策：规则 > 集中度 > E2 > AI 解释
    decision = build_account_decision(snapshot=snapshot,
                                      rules=load_price_rules())
    sheet = decision_to_sheet(decision, available_cash=cash)
    save_order_sheet(sheet)
    # ③-A：操作单生成即记模拟成交（幂等），真实侧等用户回填后 /对比 查看
    try:
        SimulationLedger().record_sheet(sheet)
    except Exception as exc:  # noqa: BLE001 - 账本失败不影响决策包展示
        logger.warning("模拟账本记录失败: %r", exc)

    text = render_decision_package(decision, sheet=sheet)
    if cash_note:
        text += cash_note
    text += _cb_research_appendix()
    if first_fill_note:
        text += first_fill_note
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
        return "/操作单 | /操作单 历史 [日期]"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        try:
            if args and args[0].strip() in ("历史", "归档"):
                day = args[1].strip() if len(args) > 1 else None
                return BotResponse.text_response(_render_history(day))
            return BotResponse.text_response(_build_sheet_text())
        except Exception as exc:  # noqa: BLE001 - 面向用户的命令不能抛栈
            return BotResponse.text_response(f"操作单生成失败：{exc}")

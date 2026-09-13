# -*- coding: utf-8 -*-
"""
===================================
运行健康命令
===================================

/健康（蓝图 ①-E）：30 秒讲清系统运行状况的五要素——
1. 信号与数据新鲜度（CB 信号契约 PIT 末端）
2. 今日推送（总线计数 / 摘要是否已发）
3. 今日操作单（状态）
4. 活跃决策信号数
5. 异常探测（信号契约过期/缺失等，一票红字）

与 /status（系统配置状态）互补：本命令看"运行时发生了什么"。
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import List

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PLAN_PATH = (_PROJECT_ROOT.parent / "quant_research" / "output"
              / "paper_tracking" / "current_plan.json")
_SHEETS_DIR = _PROJECT_ROOT / "output" / "order_sheets"


def _plan_line(today: date) -> str:
    from src.services.cb_signal_bridge import load_cb_plan

    plan = load_cb_plan()
    if plan is None:
        return "⚠ CB 信号契约缺失——quant_research 侧未运行 --export-plan"
    pit_end = plan.get("pit_end", "?")
    try:
        age = (today - date.fromisoformat(pit_end)).days
        age_text = f"（{age} 天前）" if age else ""
        stale = " ⚠ 数据超 7 天" if age > 7 else ""
    except ValueError:
        age_text, stale = "", ""
    n_targets = len(plan.get("targets") or {})
    return f"✓ CB 信号: PIT 末端 {pit_end}{age_text}{stale}，{n_targets} 只目标"


def _push_line() -> str:
    from src.services.notification_digest_service import (
        digest_already_sent, read_day_entries)

    entries = read_day_entries()
    digest = "已发" if digest_already_sent() else "未发"
    return f"✓ 今日推送: 总线 {len(entries)} 条，摘要{digest}"


def _sheet_line() -> str:
    from src.services.order_sheet_service import load_latest_sheet

    sheet = load_latest_sheet(date.today().isoformat())
    if sheet is None:
        return "· 今日操作单: 尚未生成（/操作单 生成）"
    n_orders = len(sheet.orders)
    n_fills = len(sheet.fills)
    return (f"✓ 今日操作单: {sheet.status}，{n_orders} 条操作"
            f"（回填 {n_fills}）")


def _snapshot_line() -> str:
    """决策快照状态（FINAL-SOLUTION W3）：单一事实源的版本与新鲜度。"""
    from src.services.decision_snapshot_service import load_latest_snapshot

    snap = load_latest_snapshot()
    if snap is None:
        return "· 决策快照: 尚未生成（/操作单 或每日管线触发）"
    generated = str(snap.get("generated_at") or "")[:16].replace("T", " ")
    data = snap.get("data_versions") or {}
    quality = snap.get("quality") or {}
    flag = "✓" if quality.get("fresh") else "⚠"
    return (f"{flag} 决策快照: {snap.get('snapshot_id')}（{generated}）"
            f" · PIT 末端 {data.get('pit_end', '?')}"
            f"（{data.get('cross_section_bonds', '?')} 券）")


def _cash_line() -> str:
    """现金登记提醒闭环（FINAL-SOLUTION W4）：现金=0 时买单无法给出张数。"""
    try:
        from src.services.portfolio_service import PortfolioService
        from src.services.cb_signal_bridge import load_cb_plan

        snapshot = PortfolioService().get_portfolio_snapshot(
            cost_method="fifo", include_realtime=False)
        cash = float(snapshot.get("total_cash") or 0)
        plan = load_cb_plan() or {}
        has_buy_targets = any(
            float((v or {}).get("weight") or 0) > 0
            for v in (plan.get("targets") or {}).values())
        if cash < 0:
            return (f"⚠ 可用现金: ¥{cash:,.2f}（为负——账户只有交易记录、"
                    "没有资金入账记录）请通过 /持仓登记 补记入金")
        if cash <= 0 and has_buy_targets:
            return ("⚠ 可用现金: 未登记（¥0）——操作单买单无法给出具体张数，"
                    "转入资金后请 /持仓登记 记账")
        return f"✓ 可用现金: ¥{cash:,.2f}"
    except Exception as exc:  # noqa: BLE001 - 现金查询失败不阻塞健康视图
        return f"⚠ 可用现金查询失败: {exc}"


def _coverage_line() -> str:
    """Show analysis/alert coverage for current holdings and watchlist."""
    try:
        from src.config import get_config
        from src.services.portfolio_service import PortfolioService
        from src.agent.events import parse_event_alert_rules

        cfg = get_config()
        watched = {str(s).strip().upper() for s in (cfg.stock_list or []) if str(s).strip()}
        snapshot = PortfolioService().get_portfolio_snapshot(
            cost_method="fifo", include_realtime=False)
        held = set()
        for account in snapshot.get("accounts") or []:
            for position in account.get("positions") or []:
                if float(position.get("quantity") or 0) > 0:
                    raw = str(position.get("symbol") or "").strip().upper()
                    held.add("".join(ch for ch in raw if ch.isdigit()) or raw)
        rules = parse_event_alert_rules(getattr(cfg, "agent_event_alert_rules_json", ""))
        alerted = {str(r.get("stock_code") or "").strip().upper() for r in rules}
        covered = sorted((held | watched) & alerted)
        uncovered = sorted((held | watched) - alerted)
        detail = f"已配置告警 {','.join(covered) or '无'}"
        if uncovered:
            detail += f"；未配置个性化阈值 {','.join(uncovered)}"
        return "✓ 组合覆盖: " + detail
    except Exception as exc:  # noqa: BLE001 - coverage is diagnostic only
        return f"⚠ 组合覆盖查询失败: {exc}"


def _risk_line() -> str:
    """批次 D（方案 v2）：组合集中度风险——复用 PortfolioRiskService。"""
    try:
        from src.services.portfolio_risk_service import PortfolioRiskService

        report = PortfolioRiskService().get_risk_report(include_realtime=False)
        con = report.get("concentration") or {}
        top_positions = con.get("top_positions") or []
        alerts = [p for p in top_positions if p.get("is_alert")]
        equity = float(con.get("total_market_value") or 0)
        if alerts:
            worst = alerts[0]
            names = "、".join(
                f"{p['symbol']} 占 {p['weight_pct']:.1f}%" for p in alerts[:2])
            line = (f"⚠ 集中度: {names}（阈值 "
                    f"{report['thresholds']['concentration_alert_pct']:.0f}%）"
                    f"——单标的风险敞口过大")
        else:
            top = top_positions[0] if top_positions else None
            line = (f"✓ 集中度: 最高 {top['symbol']} 占 {top['weight_pct']:.1f}%"
                    if top else "✓ 集中度: 无持仓")
        return f"{line} · 持仓市值 ¥{equity:,.0f}（不含现金）"
    except Exception as exc:  # noqa: BLE001 - 风险行失败不影响健康视图
        return f"⚠ 集中度查询失败: {exc}"


def _signal_line() -> str:
    try:
        from src.services.decision_signal_service import DecisionSignalService

        service = DecisionSignalService()
        result = service.list_signals(status="active", page=1, page_size=1)
        total = result.get("total")
        if total is None:
            items = result.get("signals") or result.get("items") or []
            total = len(items)
        return f"✓ 活跃决策信号: {total} 条"
    except Exception as exc:  # noqa: BLE001 - 信号库异常不阻塞健康视图
        return f"⚠ 活跃信号查询失败: {exc}"


def _anomaly_lines(today: date) -> List[str]:
    lines = []
    if _PLAN_PATH.exists():
        try:
            plan = json.loads(_PLAN_PATH.read_text(encoding="utf-8"))
            pit_end = date.fromisoformat(plan.get("pit_end", "1970-01-01"))
            if today - pit_end > timedelta(days=7):
                lines.append("⚠ CB 信号数据超 7 天未更新——请跑 quant 侧 PIT 增量")
        except (ValueError, json.JSONDecodeError):
            lines.append("⚠ CB 信号契约解析失败")
    else:
        lines.append("⚠ 未发现 CB 信号契约——操作单将无法生成")
    return lines


def _timing_line() -> str:
    """E2 估值分位择时信号。

    C2（OPTIMIZATION-PLAN-V2）：弃用静态 e2 JSON（其 current_state 是
    研究窗口末端读数，与最新 PIT 偏差实测 6.3pp），改为按当前 PIT
    动态重算 expanding 分位——与操作单 timing_note 同一算法同源。
    """
    try:
        import pandas as pd
        from src.services.cb_signal_bridge import load_cb_plan

        plan = load_cb_plan()
        if plan is None:
            return ""
        pit_path = (_PROJECT_ROOT.parent / "quant_research" / "data" / "pit"
                    / "convertible_pit.parquet")
        if not pit_path.exists():
            return ""
        pit = pd.read_parquet(pit_path, columns=["as_of", "price"])
        pit_end = plan.get("pit_end")
        if pit_end:
            pit = pit[pit["as_of"] <= pd.Timestamp(pit_end)]
        median = pit.groupby("as_of")["price"].median().sort_index()
        if median.empty:
            return ""
        pct = float((median <= median.iloc[-1]).mean())
        as_of = str(median.index[-1])[:10]
        if pct > 0.80:
            return (f"⚠ E2 择时: 估值分位 {pct:.1%}（截至 {as_of}）"
                    "→ 建议减半仓")
        return f"✓ E2 择时: 估值分位 {pct:.1%}（截至 {as_of}）→ 正常仓位"
    except Exception as exc:  # noqa: BLE001 - 择时行失败不影响健康视图
        return f"⚠ E2 择时计算失败: {exc}"


class HealthCommand(BotCommand):
    """运行健康视图"""

    @property
    def name(self) -> str:
        return "health"

    @property
    def aliases(self) -> List[str]:
        return ["健康", "运行状况", "运行状态"]

    @property
    def description(self) -> str:
        return "系统运行健康视图（信号/推送/操作单/异常）"

    @property
    def usage(self) -> str:
        return "/健康"

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        today = date.today()
        lines = ["【运行健康】", _plan_line(today), _push_line(),
                 _sheet_line(), _cash_line(), _snapshot_line(),
                 _coverage_line(), _risk_line(), _signal_line()]
        timing = _timing_line()
        if timing:
            lines.append(timing)
        lines.extend(_anomaly_lines(today))
        if not any("⚠" in ln for ln in lines):
            lines.append("一切正常。")
        return BotResponse.text_response("\n".join(lines))

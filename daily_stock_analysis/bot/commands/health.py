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
    """E2 估值分位择时信号"""
    try:
        from src.services.cb_signal_bridge import load_cb_plan
        plan = load_cb_plan()
        if plan is None:
            return ""
        # 简化：读 CB-RESEARCH-PLAN 产出的最新分位
        import json as _json
        e2_path = _PROJECT_ROOT.parent / "quant_research" / "output" / "e2_valuation_timing.json"
        if not e2_path.exists():
            return ""
        e2 = _json.loads(e2_path.read_text(encoding="utf-8"))
        state = e2.get("current_state", {})
        pct = state.get("percentile")
        if pct is None:
            return ""
        if pct > 0.80:
            return f"⚠ E2 择时: 估值分位 {pct:.1%} → 建议减半仓"
        return f"✓ E2 择时: 估值分位 {pct:.1%} → 正常仓位"
    except Exception:
        return ""
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
                 _sheet_line(), _signal_line()]
        timing = _timing_line()
        if timing:
            lines.append(timing)
        lines.extend(_anomaly_lines(today))
        if not any("⚠" in ln for ln in lines):
            lines.append("一切正常。")
        return BotResponse.text_response("\n".join(lines))

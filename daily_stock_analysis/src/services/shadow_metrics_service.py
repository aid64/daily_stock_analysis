# -*- coding: utf-8 -*-
"""影子期执行度量（OPTIMIZATION-PLAN-V2 第三批 8/9/10）

回答"用户是否按单执行、执行得怎么样"——把操作单历史（含状态与回填）、
模拟账本、真实账本合成按周聚合的执行指标：

- 计划腿数 / 已成交腿（足额+部分）/ 已撤腿 / 过期单数
- 执行率（数量口径）：已回填量 ÷ 计划量（仅计未被撤单前放弃的腿；
  合理放弃不算失败——分母剔除先撤后统计的腿）
- 平均滑点：已成交腿 (真实价-限价)/限价，按金额加权
- 量偏差：真实量 vs 计划量（足额腿应为 0；部分成交腿显示完成度）
- 放弃率：撤单腿 ÷ 计划腿
- 漏回填：模拟账本有、真实账本无、且未撤单的腿

数据边界：消息送达时间暂无回执，"响应时延"用 单据 created_at →
回填 filled_at 的间隔近似（记为 fill_lag_hours）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _load_sheet(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _iso_week(day: str) -> str:
    try:
        d = date.fromisoformat(day[:10])
        return f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
    except ValueError:
        return "unknown"


def _tag(week: Dict[str, Any], totals: Dict[str, Any], tag: str) -> None:
    week[f"tag_{tag}"] = week.get(f"tag_{tag}", 0) + 1
    totals[f"tag_{tag}"] = totals.get(f"tag_{tag}", 0) + 1


def collect_shadow_metrics(
    *,
    sheets_dir: Path,
    real_trades: List[Dict[str, Any]],
    days: int = 28,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """聚合近 N 天操作单的执行指标，按 ISO 周分组。"""
    now = now or datetime.now()
    cutoff = (now - timedelta(days=days)).date().isoformat()
    real_index: Dict[tuple, Dict[str, Any]] = {}
    for ev in real_trades:
        key = (str(ev.get("symbol", "")).upper(),
               str(ev.get("side", "")).lower(),
               str(ev.get("trade_date", ""))[:10])
        real_index.setdefault(key, []).append(ev)

    weeks: Dict[str, Dict[str, Any]] = {}
    totals = {
        "sheets": 0, "legs": 0, "filled_legs": 0, "partial_legs": 0,
        "cancelled_legs": 0, "expired_sheets": 0, "unmatched_legs": 0,
        "planned_qty": 0.0, "filled_qty": 0.0, "abandoned_qty": 0.0,
        "slippage_num": 0.0, "slippage_den": 0.0, "fill_lag_hours_sum": 0.0,
        "fill_lag_n": 0,
        # E1.3 偏差标签（知乎方法论+performance-coach 先例：违规分类计数）
        "tag_over_executed": 0, "tag_price_deviated": 0, "tag_late_fill": 0,
    }

    sheets_root = Path(sheets_dir)
    if not sheets_root.exists():
        return {"weeks": {}, "totals": {**totals, "execution_rate": None,
                                        "avg_slippage_pct": None,
                                        "abandon_rate": None}}

    for day_dir in sorted(sheets_root.iterdir()):
        if not day_dir.is_dir() or day_dir.name < cutoff:
            continue
        for f in sorted(day_dir.glob("*.json")):
            sheet = _load_sheet(f)
            if sheet is None:
                continue
            trade_date = str(sheet.get("trade_date", ""))[:10]
            if not trade_date or trade_date < cutoff:
                continue
            week = _iso_week(trade_date)
            w = weeks.setdefault(week, {
                "sheets": 0, "legs": 0, "filled_legs": 0, "partial_legs": 0,
                "cancelled_legs": 0, "unmatched_legs": 0,
                "planned_qty": 0.0, "filled_qty": 0.0, "abandoned_qty": 0.0,
                "slippage_num": 0.0, "slippage_den": 0.0,
                "fill_lag_hours_sum": 0.0, "fill_lag_n": 0,
                "tag_over_executed": 0, "tag_price_deviated": 0,
                "tag_late_fill": 0})
            totals["sheets"] += 1
            w["sheets"] += 1
            if sheet.get("status") == "expired":
                totals["expired_sheets"] += 1

            for order in sheet.get("orders") or []:
                sym = str(order.get("symbol") or "")
                side = str(order.get("direction") or "")
                planned = float(order.get("quantity") or 0)
                leg_key = (sym.upper(), side, trade_date)
                fill = (sheet.get("fills") or {}).get(sym) or {}
                cancelled = bool(fill.get("cancelled"))
                done = float(fill.get("actual_quantity") or 0)
                real_price = fill.get("actual_price")

                totals["legs"] += 1
                w["legs"] += 1
                totals["planned_qty"] += planned
                w["planned_qty"] += planned

                if cancelled and done <= 0:
                    # 先撤未成交 = 用户主动放弃（不算执行率分母的失败）
                    totals["cancelled_legs"] += 1
                    w["cancelled_legs"] += 1
                    totals["abandoned_qty"] += planned
                    w["abandoned_qty"] += planned
                    continue

                real_matches = real_index.get(leg_key, [])
                real_qty = sum(float(r.get("quantity") or 0) for r in real_matches)
                # E1.3 偏差标签：超量执行 / 价格偏离 / 延迟回填
                limit_price_ = float(order.get("limit_price") or 0)
                if done > planned + 1e-9:
                    _tag(w, totals, "over_executed")
                if real_price and limit_price_ > 0:
                    dev = abs(real_price - limit_price_) / limit_price_
                    if dev > 0.05:
                        _tag(w, totals, "price_deviated")
                lag_h = _fill_lag_hours(sheet.get("created_at"),
                                        fill.get("filled_at"))
                if lag_h is not None and lag_h > 48:
                    _tag(w, totals, "late_fill")
                if done > 0 or real_qty > 0:
                    totals["filled_qty"] += min(done, planned) if done else min(real_qty, planned)
                    w["filled_qty"] += min(done, planned) if done else min(real_qty, planned)
                    if planned > 0 and done >= planned:
                        totals["filled_legs"] += 1
                        w["filled_legs"] += 1
                    elif done > 0:
                        totals["partial_legs"] += 1
                        w["partial_legs"] += 1
                    # 滑点：真实/回填价 vs 限价，按成交额加权（存小数，渲染层转百分比）
                    limit_price = float(order.get("limit_price") or 0)
                    if real_price and limit_price > 0 and done > 0:
                        slip = (real_price - limit_price) / limit_price
                        sign = 1.0 if side == "buy" else -1.0  # 买贵/卖便宜=不利
                        weighted = done * real_price
                        totals["slippage_num"] += slip * sign * weighted
                        totals["slippage_den"] += weighted
                        w["slippage_num"] += slip * sign * weighted
                        w["slippage_den"] += weighted
                    # 响应时延
                    lag = _fill_lag_hours(sheet.get("created_at"),
                                          fill.get("filled_at"))
                    if lag is not None:
                        totals["fill_lag_hours_sum"] += lag
                        totals["fill_lag_n"] += 1
                        w["fill_lag_hours_sum"] += lag
                        w["fill_lag_n"] += 1
                elif not cancelled:
                    totals["unmatched_legs"] += 1
                    w["unmatched_legs"] += 1

    def _rate(num: float, den: float) -> Optional[float]:
        return round(num / den, 4) if den > 0 else None

    report_totals = {
        **totals,
        "execution_rate": _rate(totals["filled_qty"],
                                max(totals["planned_qty"] - totals["abandoned_qty"], 0.0)),
        "avg_slippage_pct": (round(totals["slippage_num"] / totals["slippage_den"], 5)
                             if totals["slippage_den"] > 0 else None),
        "abandon_rate": _rate(totals["abandoned_qty"], totals["planned_qty"]),
        "avg_fill_lag_hours": (round(totals["fill_lag_hours_sum"] / totals["fill_lag_n"], 1)
                               if totals["fill_lag_n"] else None),
    }
    for week, w in weeks.items():
        w["execution_rate"] = _rate(w["filled_qty"],
                                    max(w["planned_qty"] - w["abandoned_qty"], 0.0))
        w["avg_slippage_pct"] = (round(w["slippage_num"] / w["slippage_den"], 5)
                                 if w["slippage_den"] > 0 else None)
        w["abandon_rate"] = _rate(w["abandoned_qty"], w["planned_qty"])
        w["avg_fill_lag_hours"] = (round(w["fill_lag_hours_sum"] / w["fill_lag_n"], 1)
                                   if w["fill_lag_n"] else None)

    return {"weeks": dict(sorted(weeks.items())), "totals": report_totals}


def _fill_lag_hours(created_at: Any, filled_at: Any) -> Optional[float]:
    try:
        c = datetime.fromisoformat(str(created_at))
        f = datetime.fromisoformat(str(filled_at))
        return round(max((f - c).total_seconds(), 0) / 3600.0, 1)
    except (ValueError, TypeError):
        return None


def render_weekly(report: Dict[str, Any], *, weeks: int = 4) -> str:
    """渲染周报文本（供 /对比 周报 与每日摘要指标行）。"""
    lines: List[str] = []
    totals = report.get("totals") or {}
    lines.append(
        f"执行率 {fmt_pct(totals.get('execution_rate'))}"
        f" · 平均滑点 {fmt_pct(totals.get('avg_slippage_pct'))}"
        f" · 放弃率 {fmt_pct(totals.get('abandon_rate'))}"
        + (f" · 平均回填时延 {totals['avg_fill_lag_hours']}h"
           if totals.get("avg_fill_lag_hours") is not None else ""))
    tag_names = {"tag_over_executed": "超量", "tag_price_deviated": "改价",
                 "tag_late_fill": "延迟"}
    for week, w in list((report.get("weeks") or {}).items())[-weeks:]:
        tags = "、".join(f"{label}×{w.get(k, 0)}" for k, label in tag_names.items()
                         if w.get(k, 0) > 0) or "无"
        lines.append(
            f"· {week}: 单 {w['sheets']}，腿 {w['legs']}"
            f"（成交 {w['filled_legs']} / 部分 {w['partial_legs']}"
            f" / 放弃 {w['cancelled_legs']} / 漏回填 {w['unmatched_legs']}）"
            f" · 执行率 {fmt_pct(w.get('execution_rate'))}"
            f" · 滑点 {fmt_pct(w.get('avg_slippage_pct'))}"
            f" · 偏差 {tags}")
    if not (report.get("weeks") or {}):
        lines.append("（近四周无操作单——影子期指标待第一笔执行）")
    return "\n".join(lines)


def fmt_pct(value: Any) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "—"

# -*- coding: utf-8 -*-
"""半自动交易助手视图端点（蓝图 ③-C / ①-E.2）

- GET /assistant/health-view   运行健康五要素（信号新鲜度/推送/操作单/信号数/异常）
- GET /assistant/comparison    模拟账本 vs 真实账本逐笔对比

与 bot 命令（/健康 /对比）同一数据源（services 层），本路由只是 Web 视图。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PLAN_PATH = (_PROJECT_ROOT.parent / "quant_research" / "output"
              / "paper_tracking" / "current_plan.json")


def _signal_freshness(today: date) -> Dict[str, Any]:
    from src.services.cb_signal_bridge import load_cb_plan

    plan = load_cb_plan()
    if plan is None:
        return {"available": False, "reason": "signal contract missing"}
    pit_end = str(plan.get("pit_end", ""))
    try:
        age_days = (today - date.fromisoformat(pit_end)).days
    except ValueError:
        age_days = None
    return {
        "available": True,
        "pit_end": pit_end,
        "age_days": age_days,
        "stale": age_days is None or age_days > 7,
        "n_targets": len(plan.get("targets") or {}),
        "latest_rebalance": plan.get("latest_rebalance"),
    }


def _push_stats(today: date) -> Dict[str, Any]:
    from src.services.notification_digest_service import (
        digest_already_sent, read_day_entries)

    entries = read_day_entries()
    holding_related = sum(1 for e in entries if e.get("holding_related"))
    return {
        "bus_count": len(entries),
        "holding_related_count": holding_related,
        "digest_sent": digest_already_sent(),
    }


def _today_sheet(today: date) -> Dict[str, Any]:
    from src.services.order_sheet_service import load_latest_sheet

    sheet = load_latest_sheet(today.isoformat())
    if sheet is None:
        return {"exists": False}
    return {
        "exists": True,
        "sheet_id": sheet.sheet_id,
        "status": sheet.status,
        "n_orders": len(sheet.orders),
        "n_holds": len(sheet.holds),
        "n_fills": len(sheet.fills),
        "total_buy_value": sheet.total_buy_value,
        "total_sell_value": sheet.total_sell_value,
    }


def _active_signals() -> Dict[str, Any]:
    try:
        from src.services.decision_signal_service import DecisionSignalService

        result = DecisionSignalService().list_signals(
            status="active", page=1, page_size=1)
        total = result.get("total")
        if total is None:
            total = len(result.get("items") or [])
        return {"count": int(total)}
    except Exception as exc:  # noqa: BLE001 - 视图不因信号库异常崩溃
        return {"count": None, "error": str(exc)}


@router.get("/health-view")
def assistant_health_view() -> Dict[str, Any]:
    """运行健康五要素（与 bot /健康 同源）"""
    today = date.today()
    anomalies: List[str] = []
    signal = _signal_freshness(today)
    if not signal.get("available"):
        anomalies.append("CB 信号契约缺失")
    elif signal.get("stale"):
        anomalies.append("CB 信号数据超 7 天未更新")
    return {
        "date": today.isoformat(),
        "signal": signal,
        "push": _push_stats(today),
        "order_sheet": _today_sheet(today),
        "active_signals": _active_signals(),
        "anomalies": anomalies,
        "healthy": not anomalies,
    }


@router.get("/comparison")
def assistant_comparison(
    account_id: Optional[int] = None,
) -> Dict[str, Any]:
    """模拟账本 vs 真实账本逐笔对比（与 bot /对比 同源）"""
    try:
        from src.services.portfolio_analysis_context import (
            get_or_create_default_account)
        from src.services.portfolio_service import PortfolioService
        from src.services.simulation_ledger import SimulationLedger

        ledger = SimulationLedger()
        kwargs: Dict[str, Any] = {}
        if account_id is not None:
            kwargs["account_id"] = account_id
        else:
            service = PortfolioService()
            account = get_or_create_default_account(service)
            if account is not None:
                kwargs["account_id"] = int(account["account_id"])
            result = ledger.compare_with_real(service, **kwargs)
            return {**result, "comparison_date": date.today().isoformat()}
        result = ledger.compare_with_real(
            PortfolioService(), **kwargs)
        return {**result, "comparison_date": date.today().isoformat()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error": "comparison_failed", "message": str(exc)},
        ) from exc


@router.get("/order-sheets")
def assistant_order_sheets(limit: int = 10) -> Dict[str, Any]:
    """近期操作单列表（状态机概览）"""
    from src.services.order_sheet_service import SHEETS_DIR

    root = SHEETS_DIR
    if not root.exists():
        return {"sheets": []}
    sheets: List[Dict[str, Any]] = []
    day_dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      reverse=True)[: max(1, limit)]
    for day_dir in day_dirs:
        for f in sorted(day_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sheets.append({
                    "sheet_id": data.get("sheet_id"),
                    "trade_date": data.get("trade_date"),
                    "status": data.get("status"),
                    "n_orders": len(data.get("orders") or []),
                    "total_buy_value": data.get("total_buy_value"),
                    "total_sell_value": data.get("total_sell_value"),
                })
            except (json.JSONDecodeError, OSError):
                continue
    return {"sheets": sheets[:limit]}

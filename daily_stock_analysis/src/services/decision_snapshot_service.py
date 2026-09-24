# -*- coding: utf-8 -*-
"""Decision Snapshot 单一事实源（FINAL-SOLUTION 0.1 / W3）

一个版本化快照收拢当日全部决策事实：信号契约（targets/blocked）、
持仓与现金、操作单状态、PIT 数据版本。管线、bot 命令、Web 端点
都只读快照，不各自重推——"每个数字都能追溯到数据日期和版本"。

存储布局（output/decision_snapshot/）：
- latest.json          当前快照（原子写：tmp + os.replace）
- archive/<date>.json  每日末版快照（审计"系统那天说了什么"），保留 90 天

异常边界：refresh() 任何失败只记日志返回 None，绝不拖垮操作单/管线。
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import os
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "output" / "decision_snapshot"
_REPO_ROOT = SNAPSHOT_DIR.parent.parent.parent      # 仓库根（daily_stock_analysis 的上级）
PIT_PARQUET = _REPO_ROOT / "quant_research" / "data" / "pit" / "convertible_pit.parquet"
ARCHIVE_RETENTION_DAYS = 90
SNAPSHOT_SCHEMA_VERSION = 2
DECISION_CONTEXT_VERSION = 1
_UNSET = object()
_SNAPSHOT_WRITE_LOCK = RLock()
STALE_PLAN_DAYS = 7  # 与 /健康 信号告警口径一致

_FRESHNESS_STALE = (
    "⚠ CB 信号过期：PIT 末端 {pit_end}（{age} 天前）——今日不生成操作指令。\n"
    "请先运行每日管线（PIT 增量→assemble→导出）后重试。")
_FRESHNESS_UNREGEN = (
    "⚠ 信号契约未随 PIT 更新重生（generated_at {generated} < pit_end {pit_end}）"
    "——今日不生成操作指令，请重跑 --export-plan。")
_FRESHNESS_NO_PIT_END = "⚠ 信号契约缺少 pit_end——拒绝生成操作指令（数据完整性不足）。"
_FRESHNESS_BAD_PIT_END = "⚠ 信号契约 pit_end 无法解析——拒绝生成操作指令。"


def evaluate_plan_freshness(plan: Optional[Dict[str, Any]]) -> tuple[bool, str]:
    """信号新鲜度评估（唯一实现，order_sheet 门控与 /健康 共用）。

    Returns:
        (fresh, message)：fresh=True 时 message 为空串；False 时 message
        是面向用户的拒绝理由。
    """
    if not plan:
        return True, ""  # 契约缺失由调用方按"信号源不可用"处理
    pit_end_raw = str(plan.get("pit_end") or "")
    if not pit_end_raw:
        return False, _FRESHNESS_NO_PIT_END
    try:
        pit_end = date.fromisoformat(pit_end_raw)
    except ValueError:
        return False, _FRESHNESS_BAD_PIT_END
    age = (date.today() - pit_end).days
    if age > STALE_PLAN_DAYS:
        return False, _FRESHNESS_STALE.format(pit_end=pit_end, age=age)
    generated_raw = str(plan.get("generated_at") or "")
    if generated_raw[:10] and generated_raw[:10] < pit_end_raw:
        return False, _FRESHNESS_UNREGEN.format(
            generated=generated_raw[:10], pit_end=pit_end_raw)
    return True, ""


def _pit_data_version() -> Dict[str, Any]:
    """PIT parquet 指纹与覆盖统计（读失败不阻塞快照）。"""
    info: Dict[str, Any] = {"pit_path": str(PIT_PARQUET)}
    try:
        import pandas as pd

        info["pit_sha256"] = hashlib.sha256(PIT_PARQUET.read_bytes()).hexdigest()
        table = pd.read_parquet(PIT_PARQUET, columns=["as_of", "symbol"])
        latest = table["as_of"].max()
        info["pit_end"] = str(pd.Timestamp(latest).date())
        info["cross_section_bonds"] = int(
            table.loc[table["as_of"] == latest, "symbol"].nunique())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[snapshot] PIT 数据版本读取失败: %r", exc)
        info["pit_error"] = str(exc)
    return info


def _resolved_database_path() -> str:
    """当前进程实际使用的数据库绝对路径（诊断字段）。

    DATABASE_PATH 默认是相对路径（./data/stock_analysis.db）——不同工作目录/
    不同入口的进程会读到不同的库（甚至一个空库），这里把真实路径写进快照，
    让"这个快照是哪份数据算出来的"可回溯、不一致时可见。
    """
    raw = ""
    try:
        from src.config import Config

        raw = str(getattr(Config.get_instance(), "database_path", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 - 取不到就走环境变量
        logger.debug("[snapshot] 读取 database_path 失败: %r", exc)
    if not raw:
        import os as _os

        raw = _os.getenv("DATABASE_PATH", "./data/stock_analysis.db")
    try:
        return str(Path(raw).expanduser().resolve())
    except Exception:  # noqa: BLE001
        return raw


def _is_portfolio_regression(
    previous: Optional[Dict[str, Any]],
    snapshot: Dict[str, Any],
) -> bool:
    """本快照是否把当日持仓读成了空、且与既有快照不是同一个库。

    同日 + 既有快照有持仓 + 新快照无持仓 + 数据库路径不同 → 几乎只可能是
    "另一进程用不同 DATABASE_PATH 刷新了同一份快照"（实测：相对路径 +
    不同工作目录会读到空库）。此时拒绝覆盖，保留信息更完整的那一份。
    合法的清仓（同一数据库）不受影响。
    """
    if not previous:
        return False
    if str(previous.get("trade_date") or "") != str(snapshot.get("trade_date") or ""):
        return False
    prev_positions = (previous.get("portfolio") or {}).get("positions") or []
    new_positions = (snapshot.get("portfolio") or {}).get("positions") or []
    if not prev_positions or new_positions:
        return False
    prev_db = str((previous.get("data_versions") or {}).get("database_path") or "")
    new_db = str((snapshot.get("data_versions") or {}).get("database_path") or "")
    return bool(prev_db and new_db and prev_db != new_db)


def build_snapshot(
    *,
    decision: Any = None,
    rendered_text: Optional[str] = None,
    portfolio_snapshot: Optional[Dict[str, Any]] = None,
    order_sheet: Any = _UNSET,
    price_rules: Optional[Dict[str, Any]] = None,
    execution_mode: str = "generated",
) -> Dict[str, Any]:
    """收集状态或捕获调用方已经生成的决策，不重新计算历史判断。"""
    captured_at = datetime.now().isoformat(timespec="seconds")
    trade_date = normalize_archive_day(
        decision.trade_date if decision is not None else date.today().isoformat())
    if decision is not None and (not isinstance(rendered_text, str) or not rendered_text.strip()):
        raise ValueError("完整决策归档必须包含当时生成的原文")

    warnings: List[str] = []
    plan: Dict[str, Any] = {}
    plan_available = False
    try:
        from src.services.cb_signal_bridge import load_cb_plan
        plan = load_cb_plan() or {}
        plan_available = plan.get("targets") is not None or bool(plan)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"信号契约读取失败: {exc}")
    if not plan:
        # R0：契约缺失/空计划不能再伪装成 fresh——快照必须能区分
        # "信号正常"与"根本没有可用信号"。
        warnings.append("信号契约缺失或无目标——决策依据不完整")
    fresh, fresh_msg = evaluate_plan_freshness(plan if plan else None)
    if fresh_msg:
        warnings.append(fresh_msg.splitlines()[0])

    portfolio: Dict[str, Any] = {}
    try:
        if portfolio_snapshot is None:
            from src.services.portfolio_service import PortfolioService
            portfolio_snapshot = PortfolioService().get_portfolio_snapshot(
                cost_method="fifo", include_realtime=False)
        snapshot = portfolio_snapshot
        positions = []
        for account in snapshot.get("accounts") or []:
            for position in account.get("positions") or []:
                if float(position.get("quantity") or 0) > 0:
                    positions.append({
                        "symbol": position.get("symbol"),
                        "quantity": position.get("quantity"),
                        "avg_cost": position.get("avg_cost"),
                        "last_price": position.get("last_price"),
                        "price_date": position.get("price_date"),
                    })
        portfolio = {
            "total_cash": round(float(snapshot.get("total_cash") or 0), 2),
            "positions": sorted(positions, key=lambda p: str(p["symbol"])),
        }
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"持仓快照读取失败: {exc}")

    # 价格新鲜度：持仓估值价不得落后于信号数据日（pit_end）——落后即
    # 操作单按旧价生成（典型：价格源断流回退旧收盘价、新买入尚无 PIT 价）。
    # price_date 缺失（价格解析失败、估值价为 0）视同陈旧，比旧价更不可信。
    pit_end_raw = str(plan.get("pit_end") or "")
    held_positions = portfolio.get("positions") or []
    if held_positions and pit_end_raw:
        try:
            pit_end_d = date.fromisoformat(pit_end_raw)
            stale_syms = [
                str(p.get("symbol")) for p in held_positions
                if not p.get("price_date")
                or date.fromisoformat(str(p.get("price_date"))) < pit_end_d
            ]
            if stale_syms:
                shown = "、".join(stale_syms[:5]) + ("…" if len(stale_syms) > 5 else "")
                warnings.append(
                    f"持仓价格陈旧：{len(stale_syms)} 只持仓价格缺失或日期"
                    f"落后于 PIT 末端 {pit_end_raw}（{shown}）——注意核实估值与操作价")
        except ValueError:
            pass  # pit_end 无法解析已由 evaluate_plan_freshness 单独告警

    sheet_info: Dict[str, Any] = {}
    try:
        if order_sheet is _UNSET:
            from src.services.order_sheet_service import load_latest_sheet
            order_sheet = load_latest_sheet(trade_date)
        if order_sheet is not None:
            sheet_info = {
                "sheet_id": order_sheet.sheet_id,
                "trade_date": getattr(order_sheet, "trade_date", trade_date),
                "created_at": getattr(order_sheet, "created_at", ""),
                "status": order_sheet.status,
                "orders": [asdict(o) for o in order_sheet.orders],
                "holds": [asdict(h) for h in order_sheet.holds],
                "fills": copy.deepcopy(order_sheet.fills),
                "no_action_reason": getattr(order_sheet, "no_action_reason", None),
            }
    except Exception as exc:
        warnings.append(f"操作单读取失败: {exc}")

    data_version = _pit_data_version()
    data_version["database_path"] = _resolved_database_path()
    if data_version.get("pit_error"):
        warnings.append(f"PIT 数据版本读取失败: {data_version['pit_error'][:80]}")

    # P4.5 决策包归档契约：优先归档**调用方当时生成**的决策（decision +
    # rendered_text），使 /操作单 历史 回放的是"系统当天说了什么"的原样，
    # 而不是事后重算——盘中价格/规则/账户变化后重算会与用户收到的指令不一致。
    # 仅在无 decision（现金/恢复交易/管线等无参 refresh()）时才降级重算。
    account_decision: Dict[str, Any] = {}
    try:
        if decision is not None:
            account_decision = _capture_decision(decision)
        else:
            from src.services.account_decision_service import (
                build_account_decision, load_price_rules)
            _rules = price_rules if price_rules is not None else load_price_rules()
            _decision = build_account_decision(
                snapshot=portfolio_snapshot, rules=_rules)
            account_decision = _capture_decision(_decision)
            account_decision["recomputed"] = True
    except Exception as exc:  # noqa: BLE001 - 决策包归档失败不阻塞快照
        warnings.append(f"决策包归档读取失败: {exc}")

    snapshot_out = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": f"{trade_date}_{uuid.uuid4().hex[:8]}",
        "generated_at": captured_at,
        "trade_date": trade_date,
        "execution_mode": execution_mode,   # generated / reused / halted / recompute
        "plan": {
            "pit_end": plan.get("pit_end"),
            "plan_generated_at": plan.get("generated_at"),
            "latest_rebalance": plan.get("latest_rebalance"),
            "targets": plan.get("targets") or {},
            "blocked": plan.get("blocked") or {},
            "no_action_reason": plan.get("no_action_reason"),
        },
        "portfolio": portfolio,
        "order_sheet": sheet_info,
        "account_decision": account_decision,
        "rendered_text": rendered_text if isinstance(rendered_text, str) else "",
        "data_versions": data_version,
        "quality": {
            "fresh": fresh and not warnings,
            "signal_available": plan_available,
            "warnings": warnings,
        },
    }
    return snapshot_out


def _capture_decision(decision: Any) -> Dict[str, Any]:
    """把 AccountDecision 快照为可回放的完整判断（每票判决 + 破位诊断 +
    现金/E2 + 观察池 + 策略信号），不做二次计算。"""
    return {
        "context_version": DECISION_CONTEXT_VERSION,
        "trade_date": getattr(decision, "trade_date", ""),
        "positions": [{
            "symbol": p.symbol,
            "name": getattr(p, "name", ""),
            "quantity": p.quantity,
            "last_price": getattr(p, "last_price", 0.0),
            "verdict": p.verdict,
            "target_quantity": p.target_quantity,
            "reason": p.reason,
            "rule_state": p.rule_state,
            "ai_note": getattr(p, "ai_note", ""),
            "breach": p.breach,       # 破位诊断（深度/持续/均线/量能/紧急度）
        } for p in decision.positions],
        "cash": getattr(decision, "cash", 0.0),
        "cash_verdict": decision.cash_verdict,
        "cash_reason": decision.cash_reason,
        "e2_percentile": (round(decision.e2_percentile, 4)
                          if decision.e2_percentile is not None else None),
        "e2_as_of": decision.e2_as_of,
        "concentration_threshold_pct": decision.concentration_threshold_pct,
        "buy_candidates": list(getattr(decision, "buy_candidates", []) or []),
        "buy_source_note": getattr(decision, "buy_source_note", ""),
        "strategy_signals": list(getattr(decision, "strategy_signals", []) or []),
    }


def normalize_archive_day(value: Any) -> str:
    """把任意日期输入规整为 ISO 归档日（YYYY-MM-DD）；非法输入抛 ValueError。

    历史命令的日期参数直接用于拼归档文件名，必须先过 ISO 校验——否则
    `../` 之类的路径穿越或垃圾串会拼出目录外路径或制造无意义文件名。
    """
    text = str(value or "").strip()[:10]
    parsed = date.fromisoformat(text)   # 非法输入直接抛 ValueError
    return parsed.isoformat()


def _canonical_body(snapshot: Dict[str, Any]) -> str:
    body = {k: v for k, v in snapshot.items() if k != "content_hash"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def _content_hash(snapshot: Dict[str, Any]) -> str:
    """快照内容指纹（canonical JSON 的 sha256 全长）——读回后重算即知
    是否被改过。P4.5：从 16 位截断升级为全长 64 位（截断指纹在防篡改
    语义下抗碰撞裕度不足）。"""
    return hashlib.sha256(_canonical_body(snapshot).encode("utf-8")).hexdigest()


def verify_snapshot(snapshot: Dict[str, Any]) -> bool:
    """校验归档快照未被篡改：重算 canonical body 的 sha256 与存档一致。

    旧档兼容（P4.5）：
    - 缺 content_hash → 无法校验，返回 True（不因历史缺字段误报篡改）；
    - 旧的 16 位截断 hash → 与全长 hash 的前 16 位比对（兼容 schema<2）。
    hmac.compare_digest 常量时间比较，避免比较时序侧信道。
    """
    stored = str(snapshot.get("content_hash") or "")
    if not stored:
        return True
    recomputed = _content_hash(snapshot)
    if len(stored) == 16:   # 旧截断指纹
        return hmac.compare_digest(stored, recomputed[:16])
    return hmac.compare_digest(stored, recomputed)


def save_snapshot(snapshot: Dict[str, Any]) -> Path:
    """原子写 latest.json + 每日 archive（保留 90 天），写锁串行化。"""
    snapshot = dict(snapshot)
    snapshot["content_hash"] = _content_hash(snapshot)
    body = json.dumps(snapshot, ensure_ascii=False, indent=1)
    with _SNAPSHOT_WRITE_LOCK:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        latest = SNAPSHOT_DIR / "latest.json"
        tmp = SNAPSHOT_DIR / f"latest.json.{uuid.uuid4().hex}.tmp"
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, latest)

        archive_day = SNAPSHOT_DIR / "archive"
        archive_day.mkdir(exist_ok=True)
        day = normalize_archive_day(
            snapshot.get("trade_date") or date.today().isoformat())
        arc_final = archive_day / f"{day}.json"
        arc_tmp = archive_day / f"{day}.{uuid.uuid4().hex}.tmp"
        arc_tmp.write_text(body, encoding="utf-8")
        os.replace(arc_tmp, arc_final)   # 归档也走原子替换
        _prune_archive(archive_day)
    return latest


def _prune_archive(archive_day: Path) -> None:
    cutoff = date.today() - timedelta(days=ARCHIVE_RETENTION_DAYS)
    for f in archive_day.glob("????-??-??.json"):
        try:
            if date.fromisoformat(f.stem) < cutoff:
                f.unlink()
        except ValueError:
            continue
    # 清理可能残留的中间态 tmp（原子替换失败/进程中断遗留）
    for f in archive_day.glob("*.tmp"):
        try:
            f.unlink()
        except OSError:
            continue


def refresh(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """构建并落盘快照；异常安全（操作单/管线的失败不受影响）。

    kwargs 透传给 build_snapshot（decision/rendered_text/portfolio_snapshot/
    order_sheet/price_rules/execution_mode）——生成入口传入当时的决策与原文，
    使归档回放的是"系统当时说了什么"，无参调用（现金/恢复交易等）走重算降级。
    """
    with _SNAPSHOT_WRITE_LOCK:
        try:
            snapshot = build_snapshot(**kwargs)
            previous = load_latest_snapshot()
            if _is_portfolio_regression(previous, snapshot):
                logger.warning(
                    "[snapshot] 拒绝降级覆盖：新快照当日持仓为空、数据库路径与既有快照不同"
                    "（既有 %s ← 本次 %s）——疑似另一进程 DATABASE_PATH 不一致，保留原快照",
                    ((previous or {}).get("data_versions") or {}).get("database_path"),
                    (snapshot.get("data_versions") or {}).get("database_path"),
                )
                return previous
            save_snapshot(snapshot)
            logger.info("[snapshot] 决策快照已更新: %s", snapshot["snapshot_id"])
            return snapshot
        except Exception as exc:  # noqa: BLE001
            logger.warning("[snapshot] 快照刷新失败（不影响主流程）: %r", exc)
            return None


def load_latest_snapshot() -> Optional[Dict[str, Any]]:
    latest = SNAPSHOT_DIR / "latest.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[snapshot] latest.json 读取失败: %r", exc)
        return None

# -*- coding: utf-8 -*-
"""每日摘要消息总线（蓝图 ①-D）：治"八路轰炸、没有重点"

架构：8 个推送源各自调 NotificationService.send_with_results（现状不变）；
开关 NOTIFICATION_DAILY_DIGEST_ENABLED=true 时，静态渠道推送在
notification.py 挂点处被拦截进**当日总线**（JSONL 落库，重启不丢、
跨进程一致），每日固定时刻聚合为**一条**分级摘要发送：

  ◆ 与你持仓相关（置顶）——条目文本/结构化载荷命中持仓代码
  ◆ 重要（alert 路由 / critical 级别）
  ◆ 其他

设计取舍：
- 总线选 JSONL 而非 DB：单进程顺序 append、零迁移、git 可追溯
  （盘点 D4：轻文件方案在本仓库已被证明优于重诊断体系）。
- 当日 (route_type, dedup_key/内容hash) 去重 = 持久化降噪，
  替代 notification_noise 的进程内存态。
- system_error 与 bot 会话回复不入总线（即时性优先）。
- 摘要自身的发送走 _digest_bypass 实例标志，避免递归拦截。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BUS_ROOT = Path(__file__).resolve().parents[2] / "data" / "cache" / "notification_bus"
DIGEST_BYPASS_ATTR = "_digest_bypass"


def _today() -> str:
    return date.today().isoformat()


def _bus_path(day: Optional[str] = None) -> Path:
    return BUS_ROOT / f"{day or _today()}.jsonl"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def _symbol_variants(value: Any) -> set:
    """Return canonical text/digit variants for suffix/prefix market codes."""
    raw = str(value or "").strip().upper()
    if not raw:
        return set()
    digits = "".join(ch for ch in raw if ch.isdigit())
    variants = {raw}
    if digits:
        variants.add(digits)
    if len(digits) == 6 and digits.startswith(("11", "12")):
        variants.update({"SH" + digits if digits.startswith("11") else "SZ" + digits,
                         digits + (".SH" if digits.startswith("11") else ".SZ")})
    return variants


def _held_symbols() -> set:
    """当前持仓代码集合（前缀/后缀形态都收）；失败返回空集。"""
    try:
        from src.services.portfolio_analysis_context import _service
        from src.services.portfolio_service import PortfolioService

        service = _service()
        snapshot = service.get_portfolio_snapshot(cost_method="fifo",
                                                  include_realtime=False)
        symbols = set()
        for account in snapshot.get("accounts") or []:
            for position in account.get("positions") or []:
                symbols.update(_symbol_variants(position.get("symbol")))
        return symbols
    except Exception as exc:  # noqa: BLE001 - 持仓匹配失败不阻塞总线
        logger.warning("digest held-symbol lookup failed: %r", exc)
        return set()


def _is_holding_related(content: str, structured_payload: Optional[Dict[str, Any]],
                        held: set) -> bool:
    if not held:
        return False
    haystack_parts = [content or ""]
    if structured_payload:
        try:
            haystack_parts.append(json.dumps(structured_payload,
                                             ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            pass
    haystack = "\n".join(haystack_parts).upper()
    return any(s in haystack for s in held)


def _logical_event_fields(
    *,
    content: str,
    route_type: Optional[str],
    dedup_key: Optional[str],
    structured_payload: Optional[Dict[str, Any]],
) -> Tuple[str, str, List[str]]:
    """Resolve the stable business identity carried by a bus entry.

    Callers may provide the identity in ``structured_payload``.  The legacy
    dedup key remains the fallback so old producers and old JSONL files keep
    working while new producers stop inventing one-off verification keys.
    """
    payload = structured_payload or {}
    artifact_type = str(payload.get("artifact_type") or "").strip().lower()
    logical_id = str(payload.get("logical_id") or "").strip()
    symbols_raw = payload.get("symbols") or []
    if isinstance(symbols_raw, str):
        symbols_raw = symbols_raw.split(",")
    symbols_set = set()
    for item in symbols_raw:
        symbols_set.update(_symbol_variants(item))
    symbols = sorted(symbols_set)
    if not artifact_type:
        if str(route_type or "").lower() == "alert":
            artifact_type = "event_alert"
        elif str(route_type or "").lower() == "system_error":
            artifact_type = "system_error"
        else:
            artifact_type = "report"
    if not logical_id:
        logical_id = f"{artifact_type}:{dedup_key or _content_hash(content)}"
    return artifact_type, logical_id, symbols


def append_to_bus(
    *,
    content: str,
    route_type: Optional[str],
    severity: Optional[str],
    dedup_key: Optional[str] = None,
    structured_payload: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """写入当日总线；同一 logical_id 替换旧版本而非追加。"""
    now = now or datetime.now()
    day = now.date().isoformat()
    artifact_type, logical_id, symbols = _logical_event_fields(
        content=content, route_type=route_type, dedup_key=dedup_key,
        structured_payload=structured_payload,
    )
    payload = structured_payload or {}
    held = _held_symbols()
    entry = {
        "at": now.isoformat(timespec="seconds"),
        "route_type": route_type or "report",
        "severity": severity or "info",
        "dedup_key": dedup_key or logical_id,
        "artifact_type": artifact_type,
        "logical_id": logical_id,
        "symbols": symbols,
        "source": str(payload.get("source") or "unknown"),
        "priority": str(payload.get("priority") or "normal"),
        "status": str(payload.get("status") or "active"),
        "holding_related": bool(set(symbols) & held) or _is_holding_related(
            content, structured_payload, held),
        "content": content[:2000],
        "pit_end": payload.get("pit_end"),
        "plan_generated_at": payload.get("plan_generated_at"),
        "stale_symbols": payload.get("stale_symbols") or [],
    }
    path = _bus_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = read_day_entries(day)
    replaced = False
    retained = []
    for old in entries:
        old_logical_id = str(old.get("logical_id") or "").strip()
        old_key = (old.get("route_type"), old.get("dedup_key"))
        same_event = old_logical_id == logical_id or old_key == (
            entry["route_type"], entry["dedup_key"])
        if same_event:
            if old.get("content") == entry["content"] and old.get("symbols", []) == symbols:
                return False, old
            replaced = True
            continue
        retained.append(old)
    retained.append(entry)
    with path.open("w", encoding="utf-8") as fh:
        for item in retained:
            fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    return True, entry


def _read_day_keys(path: Path) -> set:
    keys = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            keys.add((row.get("route_type"), row.get("dedup_key")))
        except json.JSONDecodeError:
            continue
    return keys


def read_day_entries(day: Optional[str] = None) -> List[Dict[str, Any]]:
    path = _bus_path(day or _today())
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def render_digest(entries: List[Dict[str, Any]], day: Optional[str] = None) -> str:
    """分级渲染：先按逻辑事件折叠，再按持仓相关性与优先级排序。"""
    latest: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        logical_id = str(entry.get("logical_id") or "").strip()
        if not logical_id:
            logical_id = f"legacy:{entry.get('route_type')}:{entry.get('dedup_key') or _content_hash(str(entry.get('content') or ''))}"
        latest[logical_id] = entry
    entries = list(latest.values())
    related = [e for e in entries if e.get("holding_related")]
    important = [e for e in entries if not e.get("holding_related")
                 and (e.get("route_type") == "alert"
                      or str(e.get("severity")) in ("critical", "error"))]
    rest = [e for e in entries if e not in related and e not in important]

    lines = [f"【每日摘要 {day or _today()}】共 {len(entries)} 条"]
    for title, group in (("◆ 与你持仓相关", related),
                         ("◆ 重要", important),
                         ("◆ 其他", rest)):
        if not group:
            continue
        lines.append(f"{title}（{len(group)}）")
        for e in group:
            first_line = next(
                (ln.strip() for ln in str(e.get("content", "")).splitlines()
                 if ln.strip()), "(空)")
            if len(first_line) > 120:
                first_line = first_line[:117] + "..."
            lines.append(f"· [{e.get('route_type')}] {first_line}")
    if not entries:
        lines.append("今日无待汇总推送。")
    return "\n".join(lines)


def should_send_digest_now(now: Optional[datetime] = None,
                           digest_time: str = "18:00") -> bool:
    now = now or datetime.now()
    try:
        hh, mm = digest_time.split(":")[:2]
        target = int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        target = 18 * 60
    current = now.hour * 60 + now.minute
    return current >= target


def mark_digest_sent(day: Optional[str] = None) -> None:
    marker = BUS_ROOT / f"{day or _today()}.digest_sent"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now().isoformat(timespec="seconds"),
                      encoding="utf-8")


def digest_already_sent(day: Optional[str] = None) -> bool:
    return (BUS_ROOT / f"{day or _today()}.digest_sent").exists()


def run_daily_digest(force: bool = False) -> Optional[str]:
    """聚合发送今日摘要；已发送/无条目时返回 None。"""
    from src.config import get_config

    if not force and digest_already_sent():
        return None
    entries = read_day_entries()
    if not entries:
        logger.info("digest：今日总线为空，跳过")
        mark_digest_sent()
        return None
    text = render_digest(entries)
    # 方案v2 第三批(10)：摘要带影子期指标行（本周执行率/滑点/放弃率）
    try:
        from src.services.order_sheet_service import SHEETS_DIR
        from src.services.portfolio_analysis_context import (
            get_or_create_default_account)
        from src.services.portfolio_service import PortfolioService
        from src.services.shadow_metrics_service import (
            collect_shadow_metrics, render_weekly)

        svc = PortfolioService()
        account = get_or_create_default_account(svc)
        trades = []
        if account is not None:
            batch = svc.list_trade_events(
                account_id=int(account["account_id"]), page=1, page_size=200)
            trades = batch.get("items") or batch.get("events") or []
        report = collect_shadow_metrics(sheets_dir=SHEETS_DIR,
                                        real_trades=trades, days=7)
        text += "\n—— 影子期（本周）——\n" + render_weekly(report, weeks=1)
    except Exception as exc:  # noqa: BLE001 - 指标行失败不影响摘要主体
        logger.warning("影子期指标行生成失败: %r", exc)
    try:
        from src.notification import NotificationService

        notifier = NotificationService()
        setattr(notifier, DIGEST_BYPASS_ATTR, True)
        # B1（OPTIMIZATION-PLAN-V2）：检查真实发送回执——success=False
        #（no_channel/all_failed）不写 sent 标记，次日首轮可重试。
        result = notifier.send_with_results(
            text, route_type="report", severity="info",
            dedup_key=f"daily_digest:{_today()}",
        )
        if not bool(getattr(result, "success", False)):
            logger.error(
                "daily digest 通知未送达（status=%s, results=%s）——不标记已发，"
                "下轮重试", getattr(result, "status", "?"),
                [(c.channel, c.success) for c in (getattr(result, "channel_results", None) or [])])
            return None
    except Exception as exc:  # noqa: BLE001 - 摘要失败不抛给调度器
        logger.exception("daily digest send failed: %r", exc)
        return None
    mark_digest_sent()
    logger.info("daily digest 已发送（%d 条汇总）", len(entries))
    return text


def digest_background_task(config, config_provider=None):
    """供 runtime_scheduler 注册的后台任务：到点发摘要，未到点静默。"""
    def _task() -> None:
        try:
            cfg = config_provider() if config_provider else config
            digest_time = str(getattr(cfg, "notification_digest_time",
                                      "18:00") or "18:00")
            enabled = bool(getattr(cfg, "notification_daily_digest_enabled",
                                   False))
            if not enabled:
                return
            if not should_send_digest_now(digest_time=digest_time):
                return
            run_daily_digest()
        except Exception as exc:  # noqa: BLE001
            logger.exception("digest background task failed: %r", exc)

    return {
        "name": "notification_daily_digest",
        "task": _task,
        "interval_seconds": 300,
        "run_immediately": True,
    }

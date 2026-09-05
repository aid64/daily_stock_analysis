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


def _held_symbols() -> set:
    """当前持仓代码集合（前缀/后缀形态都收，宽松匹配）；失败返回空集。"""
    try:
        from src.services.portfolio_analysis_context import _service
        from src.services.portfolio_service import PortfolioService

        service = _service()
        snapshot = service.get_portfolio_snapshot(cost_method="fifo",
                                                  include_realtime=False)
        symbols = set()
        for account in snapshot.get("accounts") or []:
            for position in account.get("positions") or []:
                raw = str(position.get("symbol") or "").strip().upper()
                if not raw:
                    continue
                digits = "".join(ch for ch in raw if ch.isdigit())
                symbols.add(raw)
                if digits:
                    symbols.add(digits)
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


def append_to_bus(
    *,
    content: str,
    route_type: Optional[str],
    severity: Optional[str],
    dedup_key: Optional[str] = None,
    structured_payload: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """写入当日总线（带持久化去重）。返回 (是否新入总线, 条目)。"""
    now = now or datetime.now()
    day = now.date().isoformat()
    entry = {
        "at": now.isoformat(timespec="seconds"),
        "route_type": route_type or "report",
        "severity": severity or "info",
        "dedup_key": dedup_key or _content_hash(content),
        "holding_related": _is_holding_related(
            content, structured_payload, _held_symbols()),
        "content": content[:2000],  # 总线存摘要；原文在各自报告/历史里
    }
    path = _bus_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = _read_day_keys(path)
    key = (entry["route_type"], entry["dedup_key"])
    if key in seen:
        return False, entry
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
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
    """分级渲染：持仓相关置顶 > 重要 > 其他；每条一行摘要。"""
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
    try:
        from src.notification import NotificationService

        notifier = NotificationService()
        setattr(notifier, DIGEST_BYPASS_ATTR, True)
        notifier.send_with_results(
            text, route_type="report", severity="info",
            dedup_key=f"daily_digest:{_today()}",
        )
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

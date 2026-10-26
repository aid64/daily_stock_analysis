# -*- coding: utf-8 -*-
"""Weekday pre-market check for the DSA server and compare-deck operations.

Runs read-only. Exit code 0 when all critical checks pass, 1 when any
critical check fails. Normal mornings stay silent; failures are sent to
Feishu with one dedup key per day.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
LEDGER_PATH = REPO_ROOT / ".scratch" / "dsa-strategy-validation" / "compare_strategies.json"
BACKUP_ROOT = PROJECT_ROOT / "data" / "backups"

DNS_HOSTS = ("qt.gtimg.cn", "push2.eastmoney.com", "open.feishu.cn")
HEALTH_URL = "http://127.0.0.1:8000/api/health"
STATUS_URL = "http://127.0.0.1:8000/api/v1/assistant/runtime/status"


def _check(name: str, ok: bool, detail: str, *, critical: bool = True) -> Tuple[str, str, str]:
    status = "PASS" if ok else ("FAIL" if critical else "WARN")
    return status, name, detail


def _http_json(url: str, timeout: float = 8.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _dns_ok(host: str, timeout: float = 5.0) -> bool:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(socket.getaddrinfo, host, 443)
        try:
            future.result(timeout=timeout)
            return True
        except Exception:
            return False


def _load_ledger() -> Dict[str, Any]:
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def collect_checks(today: date) -> List[Tuple[str, str, str]]:
    checks: List[Tuple[str, str, str]] = []

    try:
        health = _http_json(HEALTH_URL)
        checks.append(_check("server health", health.get("status") == "ok", str(health.get("status"))))
    except Exception as exc:
        checks.append(_check("server health", False, repr(exc)))

    try:
        status = _http_json(STATUS_URL)
        running = bool(status.get("running"))
        stream = bool(status.get("feishu_stream_alive"))
        checks.append(_check("server running", running, f"uptime={status.get('uptime_seconds')}"))
        checks.append(_check("feishu stream", stream, f"alive={stream}"))
    except Exception as exc:
        checks.append(_check("server status", False, repr(exc)))
        checks.append(_check("feishu stream", False, "status endpoint unavailable"))

    for host in DNS_HOSTS:
        resolved = _dns_ok(host)
        checks.append(_check(f"dns {host}", resolved, "resolve ok" if resolved else "resolve failed"))

    backup_db = BACKUP_ROOT / today.isoformat() / "stock_analysis.db"
    if backup_db.is_file() and backup_db.stat().st_size > 0:
        checks.append(_check("08:30 backup", True, f"{backup_db.stat().st_size / 1e6:.1f} MB"))
    else:
        checks.append(_check("08:30 backup", False, f"missing: {backup_db}"))

    try:
        ledger = _load_ledger()
        control = ledger.get("_control") or {}
        strategies = ledger.get("strategies") or {}
        active_holds = sum(
            1 for name, slot in strategies.items()
            if not name.startswith(("C_", "D_")) and slot.get("holds")
        )
        marks = ledger.get("marks") or []
        last_mark = marks[-1].get("date") if marks else "none"
        close_done = control.get("close_done") or "none"
        iso = today.isocalendar()
        week_key = f"{iso[0]}-W{int(iso[1]):02d}"
        week_state = (control.get("week_rebalance_done") or {}).get(week_key, "absent")
        month_state = (control.get("rebalance_done") or {}).get(today.isoformat()[:7], "absent")
        checks.append(_check(
            "ledger",
            True,
            f"close_done={close_done} last_mark={last_mark} active_holds={active_holds}/13 "
            f"week={week_state} month={month_state}",
            critical=False,
        ))
        if close_done == today.isoformat():
            checks.append(_check("close_done timing", False, "close_done already set before 15:00", critical=False))
    except Exception as exc:
        checks.append(_check("ledger", False, repr(exc)))

    return checks


def notify_failures(checks: List[Tuple[str, str, str]], today: date) -> None:
    failures = [f"{name}: {detail}" for status, name, detail in checks if status == "FAIL"]
    if not failures:
        return
    try:
        from src.notification import NotificationService

        content = f"【DSA 盘前检查异常 {today.isoformat()}】\n" + "\n".join(failures[:12])
        NotificationService().send(
            content,
            route_type="strategy_compare",
            dedup_key=f"deck_morning_{today.isoformat()}",
        )
    except Exception as exc:  # fail-open: the task log remains the source of truth
        print(f"[morning-check] notification failed: {exc!r}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="DSA weekday pre-market check")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    today = date.today()
    checks = collect_checks(today)
    failures = [item for item in checks if item[0] == "FAIL"]

    if args.json:
        print(json.dumps(
            {"date": today.isoformat(), "checks": [
                {"status": status, "name": name, "detail": detail} for status, name, detail in checks
            ]},
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(f"=== DSA 盘前检查 {datetime.now().isoformat(timespec='seconds')} ===")
        for status, name, detail in checks:
            print(f"{status:4} {name}: {detail}")
        print(f"结论: {'FAIL' if failures else 'PASS'} ({len(failures)} critical failures)")

    if failures and not args.no_notify:
        notify_failures(checks, today)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

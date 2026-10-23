# -*- coding: utf-8 -*-
"""Best-effort Feishu alert when DSA_Deck_Audit exits non-zero.

Never raises: a notification failure must not change the audit result.
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    date = datetime.now().strftime("%Y%m%d")
    msg = ("【对比盘审计 FAIL】compare_deck_audit.py 退出码非 0；"
           "请查看 logs/compare_deck_audit.log 并人工介入。")
    if "--dry-run" in sys.argv:
        print(msg)
        return 0
    try:
        from src.notification import NotificationService
        ok = NotificationService().send(
            msg, route_type="strategy_compare", dedup_key=f"deck_audit_fail_{date}")
        print("notify", "ok" if ok else "not-sent")
    except Exception as exc:  # noqa: BLE001 - best effort, never block audit
        print(f"notify failed: {exc!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

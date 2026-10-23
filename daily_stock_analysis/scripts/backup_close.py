# -*- coding: utf-8 -*-
"""Post-close full backup into data/backups_close/<date>/.

The 08:30 daily backup predates the 15:00 close. This wrapper reuses
backup_ledger.run_backup with a separate root, so the morning backup is
never overwritten and retention/pruning stay identical.

Environment override for smoke tests:
  DSA_CLOSE_BACKUP_ROOT=<temp dir>
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_ledger as b  # noqa: E402


def close_root() -> Path:
    raw = os.environ.get("DSA_CLOSE_BACKUP_ROOT")
    return Path(raw) if raw else (b._PROJECT_ROOT / "data" / "backups_close")


def main() -> int:
    ap = argparse.ArgumentParser(description="Post-close full backup (separate root)")
    ap.add_argument("--retention", type=int, default=14,
                    help="保留份数（默认 14，0=不删）")
    args = ap.parse_args()
    b._BACKUP_ROOT = close_root()
    try:
        b.run_backup(retention=args.retention, verify=True)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

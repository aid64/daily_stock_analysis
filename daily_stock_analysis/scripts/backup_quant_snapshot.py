# -*- coding: utf-8 -*-
"""Copy quant signal inputs to the second-storage backup.

The daily DSA backup contains the DB/ledger/order sheets, but not the quant
inputs needed to replay E/H/A/B signals: PIT parquet, current_plan, the A/B
feeds snapshot, and the H target cache. They are small (PIT ~8MB) and are
copied alongside the close backup with a sha256 manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
QUANT_ROOT = PROJECT_DIR.parent / "quant_research"
DECK_ROOT = PROJECT_DIR
DEST_SUBDIR = "quant"
RETENTION_DEFAULT = 7


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _latest_snapshot(quant_root: Path) -> Path | None:
    snaps = sorted((quant_root / "output").glob("feeds_snapshot_v*.pkl"),
                   key=lambda p: p.name)
    return snaps[-1] if snaps else None


def _latest_h_cache(deck_root: Path) -> Path | None:
    caches = sorted((deck_root / "output" / "deck_signal_cache").glob("etf_mwu_cb_*.json"),
                    key=lambda p: p.name)
    return caches[-1] if caches else None


def _sources(quant_root: Path, deck_root: Path, day: str) -> list[tuple[str, Path, bool]]:
    """(target_name, source, required)"""
    snap = _latest_snapshot(quant_root)
    h_cache = _latest_h_cache(deck_root)
    items = [
        ("convertible_pit.parquet", quant_root / "data" / "pit" / "convertible_pit.parquet", True),
        ("current_plan.json", quant_root / "output" / "paper_tracking" / "current_plan.json", True),
        ("feeds_snapshot.pkl", snap, True) if snap else ("feeds_snapshot.pkl", Path(), True),
        ("etf_mwu_cb.json", h_cache, False) if h_cache else ("etf_mwu_cb.json", Path(), False),
        ("etf_mwu_cb_monthly.yaml",
         quant_root / "config" / "strategy" / "etf_mwu_cb_monthly.yaml", False),
    ]
    return items


def _is_date_dir(path: Path) -> bool:
    try:
        date.fromisoformat(path.name)
        return True
    except ValueError:
        return False


def backup(quant_root: Path, deck_root: Path, destination: Path, day: str,
           retention: int = RETENTION_DEFAULT, dry_run: bool = False) -> Path:
    target = destination / DEST_SUBDIR / day
    items = _sources(quant_root, deck_root, day)
    missing_required = [name for name, src, req in items if req and not src.is_file()]
    if missing_required:
        print(f"[ERROR] required quant inputs missing: {missing_required}", file=sys.stderr)
        raise SystemExit(2)
    if dry_run:
        print(f"[dry] {target} ({len([1 for _, s, _ in items if s.is_file()])} files)")
        return target

    target.mkdir(parents=True, exist_ok=True)
    lines = [
        f"quant_backup_at={datetime.now().isoformat(timespec='seconds')}",
        f"date={day}",
        f"target={target}",
    ]
    copied = 0
    for name, src, required in items:
        if not src.is_file():
            lines.append(f"{name} skipped=missing optional")
            continue
        dst = target / name
        shutil.copy2(src, dst)
        src_hash, dst_hash = _sha256(src), _sha256(dst)
        if src_hash != dst_hash:
            print(f"[ERROR] sha mismatch: {name}", file=sys.stderr)
            raise SystemExit(4)
        lines.append(f"{name} sha256={src_hash} bytes={src.stat().st_size} source={src}")
        copied += 1
    (target / "QUANT_MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if retention > 0:
        dirs = sorted((p for p in (destination / DEST_SUBDIR).iterdir() if _is_date_dir(p)),
                      key=lambda p: p.name)
        while len(dirs) > retention:
            shutil.rmtree(dirs.pop(0), ignore_errors=True)
    print(f"quant backup complete: {target} ({copied} files)")
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="备份 quant 信号输入到第二存储")
    ap.add_argument("--quant-root", default=str(QUANT_ROOT))
    ap.add_argument("--deck-root", default=str(DECK_ROOT))
    ap.add_argument("--destination", required=True)
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--retention", type=int, default=RETENTION_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        backup(Path(args.quant_root), Path(args.deck_root), Path(args.destination),
               args.date, args.retention, args.dry_run)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] quant backup failed: {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

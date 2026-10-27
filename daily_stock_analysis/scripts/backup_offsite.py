# -*- coding: utf-8 -*-
"""离机备份同步：把最新每日备份复制到第二存储（外置盘/网盘同步目录）。

每日 ``backup_ledger.py`` 只写本机 ``data/backups/<日期>/``；本机磁盘损坏会同时
丢失源数据与备份。本脚本把指定日期的备份复制到用户给定的目标目录，逐文件做
sha256 校验，并按日期保留最近 N 份。

默认**跳过 `.env`**（含飞书/LLM/数据源凭据）：离机目标若未加密，不应复制明文
凭据。完整还原需要配置时，用 ``--include-env`` 并确保目标是加密存储；也可以
把 `.env` 单独放进密码管理器。

用法（在 daily_stock_analysis/ 下）：
  py scripts/backup_offsite.py --destination E:\\DSA-Backup --dry-run
  py scripts/backup_offsite.py --destination E:\\DSA-Backup
  py scripts/backup_offsite.py --destination E:\\DSA-Backup --date 2026-10-22
  py scripts/backup_offsite.py --destination E:\\DSA-Backup --include-env
  py scripts/backup_offsite.py --destination E:\\DSA-Backup --retention 8

目标布局：``<destination>/daily_stock_analysis/<YYYY-MM-DD>/``，目录内含
``OFFSITE_MANIFEST.txt``（时间/来源/逐文件 sha256）。只清理目标下由本脚本管理的
日期目录，不触碰其它文件。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("backup_offsite")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BACKUP_ROOT = _PROJECT_ROOT / "data" / "backups"
_DEST_SUBDIR = "daily_stock_analysis"
_SECRET_NAMES = frozenset({".env"})
RETENTION_DEFAULT = 14


def _is_date_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        date.fromisoformat(path.name)
        return True
    except ValueError:
        return False


def _date_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted((p for p in root.iterdir() if _is_date_dir(p)), key=lambda p: p.name)


def latest_backup_dir(root: Path | None = None) -> Path:
    dirs = _date_dirs(root or _BACKUP_ROOT)
    if not dirs:
        raise SystemExit(2)
    return dirs[-1]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(src: Path) -> list[Path]:
    return sorted(p for p in src.rglob("*") if p.is_file())


def _ensure_disjoint(source: Path, destination: Path) -> None:
    src = source.resolve()
    dst = destination.resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        logger.error("目标目录不能与备份源目录重叠")
        raise SystemExit(2)


def sync_offsite(destination: str | Path, date_str: str | None = None,
                 retention: int = RETENTION_DEFAULT, include_env: bool = False,
                 dry_run: bool = False,
                 source_root: str | Path | None = None) -> Path:
    source_root = Path(source_root) if source_root else _BACKUP_ROOT
    source = source_root / date_str if date_str else latest_backup_dir(source_root)
    if not source.is_dir():
        logger.error("备份日期目录不存在: %s", source)
        raise SystemExit(2)

    dest_root = Path(destination).expanduser().resolve() / _DEST_SUBDIR
    target = dest_root / source.name
    if source_root.resolve() == dest_root.resolve() or source_root.resolve() in dest_root.parents:
        logger.error("目标目录不能位于本机备份根目录内（仍需离机）")
        raise SystemExit(2)
    _ensure_disjoint(source, target)

    all_files = _files(source)
    skipped = [p for p in all_files if p.name in _SECRET_NAMES and not include_env]
    copy_files = [p for p in all_files if p not in skipped]
    if not copy_files:
        logger.error("备份目录没有可同步文件: %s", source)
        raise SystemExit(2)
    if skipped:
        logger.warning("默认跳过凭据文件 %s；完整还原请用 --include-env 并确保目标加密",
                       ", ".join(p.name for p in skipped))

    if dry_run:
        logger.info("[dry] %s -> %s (%d files, %d skipped)",
                    source, target, len(copy_files), len(skipped))
        for p in copy_files:
            logger.info("[dry] %s", p.relative_to(source))
        return target

    target.mkdir(parents=True, exist_ok=True)
    if not include_env:
        for name in _SECRET_NAMES:
            stale = target / name
            if stale.exists():
                stale.unlink()
                logger.info("已移除目标中的旧凭据文件: %s", stale)
    lines = [
        f"offsite_at={datetime.now().isoformat(timespec='seconds')}",
        f"source={source}",
        f"destination={target}",
        f"files={len(copy_files)}",
        f"skipped={','.join(p.name for p in skipped) or '-'}",
    ]
    for src_file in copy_files:
        rel = src_file.relative_to(source)
        dst_file = target / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        src_hash = _sha256(src_file)
        dst_hash = _sha256(dst_file)
        if src_hash != dst_hash:
            logger.error("校验失败: %s (src %s != dst %s)", rel, src_hash, dst_hash)
            raise SystemExit(4)
        lines.append(f"{rel.as_posix()} sha256={src_hash} bytes={src_file.stat().st_size}")

    (target / "OFFSITE_MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("离机备份完成: %s (%d files)", target, len(copy_files))

    if retention > 0:
        dirs = _date_dirs(dest_root)
        while len(dirs) > retention:
            old = next(d for d in dirs if d != target)
            shutil.rmtree(old, ignore_errors=True)
            logger.info("滚动删除目标旧备份: %s", old.name)
            dirs.remove(old)
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description="把最新每日备份同步到第二存储")
    ap.add_argument("--destination", required=True,
                    help="目标根目录（外置盘/网盘同步目录），脚本会创建 daily_stock_analysis/<日期>/")
    ap.add_argument("--date", help="要同步的备份日期 YYYY-MM-DD（默认最新）")
    ap.add_argument("--retention", type=int, default=RETENTION_DEFAULT,
                    help=f"目标保留份数（默认 {RETENTION_DEFAULT}，0=不删）")
    ap.add_argument("--include-env", action="store_true",
                    help="同时复制 .env（仅在目标为加密存储时使用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写目标")
    ap.add_argument("--source-root", default=None,
                    help="备份源根目录（默认 data/backups；收盘备份用 data/backups_close）")
    args = ap.parse_args()
    try:
        sync_offsite(args.destination, args.date, args.retention,
                     args.include_env, args.dry_run, args.source_root)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("离机备份异常: %r", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

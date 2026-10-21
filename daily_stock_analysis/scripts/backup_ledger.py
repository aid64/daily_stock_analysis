# -*- coding: utf-8 -*-
"""账本与关键状态备份（v4.1 计划 P0.5 耐久性前置）

背景：账户真源是单机单 SQLite（data/stock_analysis.db，9 张 portfolio_* 表 +
append-only 审计），此前零备份——盘坏/误删 = 账本与审计全灭。本脚本建立
每日滚动备份，让单机环境有还原点。

备份内容（一份完整还原所需的最小集）：
  1. data/stock_analysis.db        —— 账户真源（用 SQLite 在线 backup API，
                                       运行中热备份也一致，不锁库）
  2. .env                          —— 配置与凭据（含价格规则/飞书/LLM）
  3. strategies.yaml（仓库根）      —— 策略注册表
  4. output/decision_snapshot/     —— 决策快照归档（审计留痕）
  5. output/order_sheets/          —— 历史操作单（回填/对比依据）
  6. .scratch/dsa-strategy-validation/compare_strategies.json
                                   —— 对比盘台账/cohort 真源（含 prereg/marks/_control），
                                      以及 cohort-manifest.jsonl、ledger-seal-manifest.json

布局：data/backups/<YYYY-MM-DD>/…（data/ 已 gitignore，备份不入库）
滚动：默认保留最近 RETENTION 份，超出按日期删最旧。

用法（在 daily_stock_analysis/ 下）：
  py scripts/backup_ledger.py              # 备份到当日目录
  py scripts/backup_ledger.py --retention 30
  py scripts/backup_ledger.py --verify     # 备份后校验 db 可打开、表齐全，且对比盘台账可解析

恢复见 docs/disaster-recovery.md。
建议由 Windows 计划任务每日调用一次（注册命令见该文档）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("backup_ledger")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]   # daily_stock_analysis/
_REPO_ROOT = _PROJECT_ROOT.parent                      # 仓库根
_DB = _PROJECT_ROOT / "data" / "stock_analysis.db"
_BACKUP_ROOT = _PROJECT_ROOT / "data" / "backups"
_OUTPUT = _PROJECT_ROOT / "output"
_DECK_DIR = _REPO_ROOT / ".scratch" / "dsa-strategy-validation"
_DECK_FILES = ("compare_strategies.json", "cohort-manifest.jsonl", "ledger-seal-manifest.json")

RETENTION_DEFAULT = 14

# portfolio 真源表（--verify 校验齐全性）
_REQUIRED_TABLES = (
    "portfolio_accounts", "portfolio_trades", "portfolio_cash_ledger",
    "portfolio_positions", "portfolio_event_audit",
)


def _backup_sqlite(src: Path, dst: Path) -> None:
    """SQLite 在线 backup：运行中也能得到一致快照，不锁写。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists():
        logger.warning("跳过（不存在）: %s", src)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return True


def _verify(db_path: Path) -> bool:
    """备份的 db 可打开且核心表齐全。"""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.error("校验失败：备份 db 无法打开: %r", exc)
        return False
    missing = [t for t in _REQUIRED_TABLES if t not in names]
    if missing:
        logger.error("校验失败：备份缺少核心表: %s", missing)
        return False
    logger.info("校验通过：db 可打开，核心表齐全")
    return True


def _verify_deck(path: Path) -> bool:
    """对比盘台账可解析且保留三块核心容器（strategies/marks/_control）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("校验失败：对比盘台账无法解析: %r", exc)
        return False
    ok = (isinstance(data, dict)
          and isinstance(data.get("strategies"), dict)
          and isinstance(data.get("marks"), list)
          and isinstance(data.get("_control"), dict))
    if not ok:
        logger.error("校验失败：对比盘台账缺少 strategies/marks/_control 容器")
    return ok


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _prune(retention: int) -> None:
    if not _BACKUP_ROOT.exists():
        return
    days = sorted(
        (d for d in _BACKUP_ROOT.iterdir()
         if d.is_dir() and _is_date_dir(d.name)),
        key=lambda d: d.name)
    for old in days[:-retention] if retention > 0 else []:
        shutil.rmtree(old, ignore_errors=True)
        logger.info("滚动删除旧备份: %s", old.name)


def _is_date_dir(name: str) -> bool:
    try:
        date.fromisoformat(name)
        return True
    except ValueError:
        return False


def run_backup(retention: int = RETENTION_DEFAULT, verify: bool = False) -> Path:
    if not _DB.exists():
        logger.error("账本主库不存在: %s", _DB)
        raise SystemExit(2)

    day_dir = _BACKUP_ROOT / date.today().isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)

    db_dst = day_dir / "stock_analysis.db"
    _backup_sqlite(_DB, db_dst)
    logger.info("已热备份账本: %s (%.1f MB)",
                db_dst, db_dst.stat().st_size / 1e6)

    _copy_if_exists(_PROJECT_ROOT / ".env", day_dir / ".env")
    _copy_if_exists(_REPO_ROOT / "strategies.yaml", day_dir / "strategies.yaml")
    _copy_if_exists(_OUTPUT / "decision_snapshot", day_dir / "decision_snapshot")
    _copy_if_exists(_OUTPUT / "order_sheets", day_dir / "order_sheets")

    deck_dst = day_dir / "dsa-strategy-validation" / "compare_strategies.json"
    deck_copied = _copy_if_exists(_DECK_DIR / "compare_strategies.json", deck_dst)
    for name in _DECK_FILES[1:]:
        _copy_if_exists(_DECK_DIR / name, day_dir / "dsa-strategy-validation" / name)

    manifest = [
        f"backup_at={datetime.now().isoformat(timespec='seconds')}",
        f"db_bytes={db_dst.stat().st_size}",
        f"source_db={_DB}",
    ]
    if deck_copied:
        manifest += [
            f"deck_ledger_bytes={deck_dst.stat().st_size}",
            f"deck_ledger_sha256={_sha256(deck_dst)}",
        ]
    (day_dir / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")

    ok = _verify(db_dst) if verify else True
    if verify and deck_copied and not _verify_deck(deck_dst):
        ok = False
    _prune(retention)
    if not ok:
        raise SystemExit(3)
    logger.info("备份完成: %s", day_dir)
    return day_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="账本与关键状态每日备份（P0.5）")
    ap.add_argument("--retention", type=int, default=RETENTION_DEFAULT,
                    help=f"保留份数（默认 {RETENTION_DEFAULT}，0=不删）")
    ap.add_argument("--verify", action="store_true",
                    help="备份后校验 db 可打开且核心表齐全")
    args = ap.parse_args()
    try:
        run_backup(retention=args.retention, verify=args.verify)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("备份异常: %r", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

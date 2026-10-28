# -*- coding: utf-8 -*-
"""纸面账本独立 SQLite 存储（与真实 Portfolio 数据库零共享）。

**状态（2026-10-14 决定）：未采纳为对比盘正式账本**——正式账本是 JSON 单文件
`compare_strategies.json`（契约见 `docs/strategy-compare-deck.md` §16）；本模块
当前无生产消费者，**勿在对比盘路径接线**。如未来出现多进程共库需求，须先评估
复用 `src/storage.py` 的 `Portfolio*` 表并另开独立任务。

- 独立连接工厂：绝不复用 ``src.storage`` 的进程单例——那个单例在已初始化后
  传不同 db_url 也不会产生隔离库。纸面库路径由调用方显式注入。
- 事务：BEGIN IMMEDIATE + busy_timeout；写事务短小（行情、信号、网络都在事务外）。
- 初始化失败必须阻断（抛 PaperStoreError），不告警继续。
- 事件表是事实源；现金/持仓是可重放投影（重放实现见 paper_trading_service）。
  事件 UNIQUE(slot, order_key) 提供跨进程订单幂等（order_key 为 NULL 的事件不受限）。
- 备份走 SQLite backup API；禁止直接复制活跃 WAL 数据库文件。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    slot TEXT PRIMARY KEY,
    starting_cash REAL NOT NULL,
    config_hash TEXT NOT NULL,
    created_day TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    slot TEXT NOT NULL REFERENCES accounts(slot),
    trading_day TEXT NOT NULL,
    event_type TEXT NOT NULL,
    order_key TEXT,
    payload TEXT NOT NULL,
    cash_after REAL,
    created_at TEXT NOT NULL,
    UNIQUE (slot, order_key)
);
CREATE TABLE IF NOT EXISTS daily_valuation (
    slot TEXT NOT NULL,
    trading_day TEXT NOT NULL,
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    equity REAL NOT NULL,
    unvalued_json TEXT NOT NULL,
    fully_valued INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (slot, trading_day)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    slot TEXT,
    trading_day TEXT,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (topic, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_events_slot_seq ON events(slot, seq);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, id);
"""


class PaperStoreError(RuntimeError):
    """纸面账本存储层错误（初始化失败/约束冲突/结构漂移）。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class PaperStore:
    """单文件 SQLite 纸面账本。线程内复用连接；写事务经 :meth:`transaction`。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PaperStoreError(f"paper db 目录不可创建 {self.db_path}: {exc}") from exc
        self._local = threading.local()
        self._init_schema()

    # ------------------------------------------------------------ 连接与初始化
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        last_exc: Optional[Exception] = None
        for attempt in range(5):
            new_conn: Optional[sqlite3.Connection] = None
            try:
                new_conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
                # busy_timeout 必须先于 journal_mode；Windows 上两进程同时做
                # WAL 转换仍可能立即报 locked——整段初始化带退避重试。
                new_conn.execute("PRAGMA busy_timeout=5000")
                new_conn.execute("PRAGMA foreign_keys=ON")
                if new_conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                    raise PaperStoreError("paper db foreign_keys 未生效——阻断而非降级")
                if new_conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                    new_conn.execute("PRAGMA journal_mode=WAL")
                new_conn.execute("PRAGMA synchronous=FULL")
                self._local.conn = new_conn
                return new_conn
            except PaperStoreError:
                raise
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if new_conn is not None:
                    new_conn.close()
                if "locked" not in str(exc) and "busy" not in str(exc).lower():
                    raise PaperStoreError(f"paper db 初始化失败 {self.db_path}: {exc}") from exc
                time.sleep(0.25 * (attempt + 1))
            except sqlite3.Error as exc:
                if new_conn is not None:
                    new_conn.close()
                raise PaperStoreError(f"paper db 初始化失败 {self.db_path}: {exc}") from exc
        raise PaperStoreError(f"paper db 初始化持续锁冲突 {self.db_path}: {last_exc!r}")

    def _init_schema(self) -> None:
        conn = self._conn()
        last_exc: Optional[Exception] = None
        for attempt in range(4):
            try:
                # CREATE ... IF NOT EXISTS 与 meta 插入均幂等，锁冲突可安全重试。
                # 并发首开时两个进程可能同时看到 meta 为空：用 INSERT OR IGNORE
                # 消除 UNIQUE(schema_version) 竞争，再重读校验版本。
                conn.executescript(_SCHEMA)
                row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if row is None:
                    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                                 (str(SCHEMA_VERSION),))
                    row = conn.execute(
                        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if row is not None and int(row[0]) > SCHEMA_VERSION:
                    raise PaperStoreError(
                        f"paper db schema v{row[0]} 高于当前支持的 v{SCHEMA_VERSION}——拒绝打开")
                return
            except PaperStoreError:
                raise
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc).lower():
                    raise PaperStoreError(f"paper db schema 初始化失败: {exc}") from exc
                time.sleep(0.2 * (attempt + 1))
                last_exc = exc
            except sqlite3.Error as exc:
                raise PaperStoreError(f"paper db schema 初始化失败: {exc}") from exc
        raise PaperStoreError(f"paper db schema 初始化持续锁冲突: {last_exc!r}")

    @contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE 写事务；异常回滚，成功提交。行情/信号/网络不得进入。"""
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise PaperStoreError(f"paper db 写锁获取失败: {exc}") from exc
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # ------------------------------------------------------------ 账户与事件
    def ensure_account(self, slot: str, starting_cash: float, config_hash: str,
                       created_day: str) -> None:
        """建账（幂等）。已存在但起始资金/配置哈希不同视为配置漂移，直接拒绝。"""
        with self.transaction() as conn:
            row = conn.execute("SELECT starting_cash, config_hash FROM accounts WHERE slot=?",
                               (slot,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO accounts(slot, starting_cash, config_hash, created_day)"
                    " VALUES(?, ?, ?, ?)",
                    (slot, float(starting_cash), config_hash, created_day))
                conn.execute(
                    "INSERT INTO events(slot, trading_day, event_type, order_key, payload,"
                    " cash_after, created_at) VALUES(?, ?, 'opening', NULL, ?, ?, ?)",
                    (slot, created_day,
                     json.dumps({"starting_cash": float(starting_cash),
                                 "config_hash": config_hash}, ensure_ascii=False),
                     float(starting_cash), _now()))
            else:
                if abs(row[0] - starting_cash) > 1e-9 or row[1] != config_hash:
                    raise PaperStoreError(
                        f"账户 {slot} 配置漂移: 已有 starting_cash={row[0]}, "
                        f"config_hash={row[1]!r}; 请求 cash={starting_cash}, hash={config_hash!r}")

    def max_seq(self, conn: sqlite3.Connection, slot: str) -> int:
        return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events WHERE slot=?",
                            (slot,)).fetchone()[0]

    def executed_keys(self, conn: sqlite3.Connection, slot: str,
                      keys: Sequence[str]) -> set:
        if not keys:
            return set()
        marks = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT order_key FROM events WHERE slot=? AND order_key IN ({marks})",
            (slot, *keys)).fetchall()
        return {r[0] for r in rows}

    def insert_event(self, conn: sqlite3.Connection, slot: str, trading_day: str,
                     event_type: str, order_key: Optional[str], payload: dict,
                     cash_after: Optional[float]) -> None:
        conn.execute(
            "INSERT INTO events(slot, trading_day, event_type, order_key, payload,"
            " cash_after, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (slot, trading_day, event_type, order_key,
             json.dumps(payload, ensure_ascii=False, sort_keys=True), cash_after, _now()))

    def events(self, slot: str) -> list:
        return self._conn().execute(
            "SELECT seq, trading_day, event_type, order_key, payload, cash_after, created_at"
            " FROM events WHERE slot=? ORDER BY seq", (slot,)).fetchall()

    # ------------------------------------------------------------ 估值与 outbox
    def upsert_valuation(self, slot: str, trading_day: str, cash: float,
                         market_value: float, equity: float,
                         unvalued: dict, fully_valued: bool) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO daily_valuation(slot, trading_day, cash, market_value, equity,"
                " unvalued_json, fully_valued, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(slot, trading_day) DO UPDATE SET cash=excluded.cash,"
                " market_value=excluded.market_value, equity=excluded.equity,"
                " unvalued_json=excluded.unvalued_json, fully_valued=excluded.fully_valued,"
                " updated_at=excluded.updated_at",
                (slot, trading_day, cash, market_value, equity,
                 json.dumps(unvalued, ensure_ascii=False, sort_keys=True),
                 1 if fully_valued else 0, _now()))

    def insert_outbox(self, conn: sqlite3.Connection, topic: str, dedup_key: str,
                      content: str, slot: Optional[str], trading_day: Optional[str]) -> None:
        """与事件同事务写入。UNIQUE(topic, dedup_key) 冲突抛 IntegrityError→整事务回滚。"""
        conn.execute(
            "INSERT INTO outbox(topic, dedup_key, slot, trading_day, content, created_at,"
            " updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (topic, dedup_key, slot, trading_day, content, _now(), _now()))

    def outbox_rows(self, statuses: Sequence[str], limit: int = 50) -> list:
        marks = ",".join("?" * len(statuses))
        return self._conn().execute(
            f"SELECT id, topic, dedup_key, slot, trading_day, content, status, attempts"
            f" FROM outbox WHERE status IN ({marks}) ORDER BY id LIMIT ?",
            (*statuses, int(limit))).fetchall()

    def mark_outbox(self, outbox_id: int, status: str, error: Optional[str] = None) -> None:
        if status not in ("pending", "queued", "acknowledged", "failed", "uncertain"):
            raise PaperStoreError(f"非法 outbox 状态: {status!r}")
        with self.transaction() as conn:
            conn.execute(
                "UPDATE outbox SET status=?, last_error=?,"
                " attempts=attempts+1, updated_at=? WHERE id=?",
                (status, error, _now(), outbox_id))

    # ------------------------------------------------------------ 运维
    def backup(self, target: str | Path) -> Path:
        """SQLite backup API 在线备份（WAL 安全）。"""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        src = self._conn()
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
        return target

    def integrity_check(self) -> bool:
        return self._conn().execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

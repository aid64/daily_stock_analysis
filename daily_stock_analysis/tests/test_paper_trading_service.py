# -*- coding: utf-8 -*-
"""纸面交易服务回归测试：隔离、幂等、原子性、多进程竞争、备份恢复。

对应 spec.md Phase 1 任务 02 与 map.md 验收矩阵第 2/3/4 条。
全部使用 tmp_path 独立数据库；不读生产台账、不读 .env、不联网。
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

from src.core.paper_execution import (
    AssetClass,
    ExecutionContext,
    Intent,
    Quote,
    trade_fee,
)
from src.repositories.paper_repo import PaperStore, PaperStoreError
from src.services.paper_trading_service import (
    PaperTradingService,
    build_quote,
)

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 10, 8)
DAY2 = date(2026, 10, 9)


def _svc(tmp_path) -> PaperTradingService:
    return PaperTradingService(tmp_path / "paper.db")


def _ctx(slot="T", day=DAY) -> ExecutionContext:
    return ExecutionContext(slot=slot, trading_day=day)


def _buy(symbol="510300.SH", qty=100, cls=AssetClass.ETF, **kw) -> Intent:
    return Intent(symbol=symbol, side="BUY", qty=qty, asset_class=cls, **kw)


def _sell(symbol="510300.SH", qty=100, cls=AssetClass.ETF, **kw) -> Intent:
    return Intent(symbol=symbol, side="SELL", qty=qty, asset_class=cls, **kw)


def _q(symbol="510300.SH", price=4.5) -> dict:
    return {symbol: Quote(symbol=symbol, price=price)}


# ---------------------------------------------------------------- 账户与隔离
def test_account_isolation_same_symbol_two_slots(tmp_path):
    svc = _svc(tmp_path)
    svc.ensure_account("A", 12411.0, config_hash="h1", created_day=DAY)
    svc.ensure_account("B", 12411.0, config_hash="h2", created_day=DAY)
    svc.execute("A", DAY, [_buy()], _q(), _ctx("A"))
    a, b = svc.load_state("A"), svc.load_state("B")
    assert sum(l.qty for l in a.lots) == 100
    assert b.lots == () and b.cash == 12411.0        # B 未动
    assert a.cash == pytest.approx(12411.0 - 100 * 4.5 - 5.0)


def test_config_drift_rejected(tmp_path):
    svc = _svc(tmp_path)
    svc.ensure_account("A", 12411.0, config_hash="h1", created_day=DAY)
    with pytest.raises(PaperStoreError):
        svc.ensure_account("A", 9999.0, config_hash="h1", created_day=DAY)
    with pytest.raises(PaperStoreError):
        svc.ensure_account("A", 12411.0, config_hash="h9", created_day=DAY)


# ---------------------------------------------------------------- 幂等
def test_duplicate_command_retry_executes_once(tmp_path):
    """反例: 相同输入重试 100→200→300。服务层重放同键命令只成交一次。"""
    svc = _svc(tmp_path)
    svc.ensure_account("T", 12411.0, config_hash="h", created_day=DAY)
    intents = [_buy()]
    r1 = svc.execute("T", DAY, intents, _q(), _ctx())
    r2 = svc.execute("T", DAY, intents, _q(), _ctx())
    assert len(r1.fills) == 1
    assert r2.fills == () and r2.cash == r1.cash
    assert all(rej.reason.value == "duplicate_order" for rej in r2.rejections)
    assert len(svc.store.events("T")) == 2            # opening + 1 fill
    assert svc.load_state("T").cash == r1.cash


def test_dividend_credited_exactly_once(tmp_path):
    """反例: 同一分红事件跨日重复入账。event_id 幂等，金额按事件而非按天。"""
    svc = _svc(tmp_path)
    svc.ensure_account("D", 12411.0, config_hash="h", created_day=DAY)
    assert svc.record_dividend("D", DAY, "512890.SH:2026-div-1", "512890.SH",
                               10.0, detail="每100份派10元") is True
    assert svc.record_dividend("D", DAY2, "512890.SH:2026-div-1", "512890.SH",
                               10.0) is False            # 同事件次日重放
    assert svc.record_dividend("D", DAY2, "512890.SH:2026-div-2", "512890.SH",
                               20.0) is True             # 新事件正常入账
    assert svc.load_state("D").cash == pytest.approx(12411.0 + 10.0 + 20.0)


# ---------------------------------------------------------------- 原子性
def test_outbox_collision_rolls_back_fill_event(tmp_path):
    """outbox 与成交同事务：outbox 冲突→整笔回滚，重放命令恢复。"""
    svc = _svc(tmp_path)
    svc.ensure_account("T", 12411.0, config_hash="h", created_day=DAY)
    # 预占一个会与 extra_notification 冲突的 dedup 键
    with svc.store.transaction() as conn:
        svc.store.insert_outbox(conn, "alert", "fixed", "pre-existing", None, None)
    before = svc.store.events("T")
    with pytest.raises(sqlite3.IntegrityError):
        svc.execute("T", DAY, [_buy()], _q(), _ctx(),
                    extra_notifications=[("alert", "fixed", "collides")])
    assert svc.store.events("T") == before            # 成交事件已回滚
    assert svc.load_state("T").cash == 12411.0
    r = svc.execute("T", DAY, [_buy()], _q(), _ctx())  # 重放：幂等键未消耗，正常成交
    assert len(r.fills) == 1


def test_foreign_keys_block_orphan_event(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with svc.store.transaction() as conn:
            svc.store.insert_event(conn, "ghost_slot", "2026-10-08", "fill", None,
                                   {"symbol": "X"}, cash_after=1.0)


# ---------------------------------------------------------------- 重放一致性
def test_state_replay_matches_execution(tmp_path):
    svc = _svc(tmp_path)
    svc.ensure_account("T", 12411.0, config_hash="h", created_day=DAY)
    svc.execute("T", DAY, [_buy("600000.SH", 200, AssetClass.STOCK)],
                {"600000.SH": Quote("600000.SH", 10.0)}, _ctx())
    svc.execute("T", DAY2, [_sell("600000.SH", 50, AssetClass.STOCK)],
                {"600000.SH": Quote("600000.SH", 11.0)}, _ctx("T", DAY2))
    svc.record_dividend("T", DAY2, "div-x", "600000.SH", 12.3)

    state = svc.load_state("T")
    expected = (12411.0 - (2000.0 + trade_fee(AssetClass.STOCK, "BUY", 2000.0))
                + (550.0 - trade_fee(AssetClass.STOCK, "SELL", 550.0)) + 12.3)
    assert state.cash == pytest.approx(expected, abs=0.01)
    assert [(l.qty, l.price, l.buy_day) for l in state.lots] == [(150, 10.0, DAY)]

    # 事件表最后一条 cash_after 与重放现金一致
    last_cash_after = svc.store.events("T")[-1][5]
    assert last_cash_after == pytest.approx(state.cash, abs=0.01)


# ---------------------------------------------------------------- 多进程竞争
def test_multiprocess_exactly_one_fill(tmp_path):
    """真实双进程同键竞争：恰好一单成交，另一单 DUPLICATE_ORDER，库完好。"""
    worker = tmp_path / "mp_worker.py"
    worker.write_text(
        "import json, sys\n"
        "from datetime import date\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from src.services.paper_trading_service import PaperTradingService\n"
        "from src.core.paper_execution import AssetClass, ExecutionContext, Intent, Quote\n"
        "svc = PaperTradingService(sys.argv[2])\n"
        "svc.ensure_account('P', 12411.0, config_hash='mp', created_day='2026-10-08')\n"
        "ctx = ExecutionContext(slot='P', trading_day=date(2026, 10, 8))\n"
        "r = svc.execute('P', date(2026, 10, 8),\n"
        "                [Intent(symbol='510300.SH', side='BUY', qty=100,\n"
        "                        asset_class=AssetClass.ETF)],\n"
        "                {'510300.SH': Quote('510300.SH', 4.5)}, ctx)\n"
        "print(json.dumps({'fills': len(r.fills),\n"
        "                  'dup': any(x.reason.value == 'duplicate_order' for x in r.rejections),\n"
        "                  'cash': r.cash}))\n",
        encoding="utf-8")
    db = tmp_path / "mp.db"
    procs = [subprocess.Popen([sys.executable, str(worker), str(ROOT), str(db)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, cwd=str(ROOT), env=dict(os.environ))
             for _ in range(2)]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"worker failed: {err[-500:]}"
        results.append(json.loads(out.strip().splitlines()[-1]))
    assert sum(r["fills"] for r in results) == 1
    assert sum(1 for r in results if r["dup"]) == 1
    final = PaperTradingService(db).load_state("P")
    assert final.cash == pytest.approx(12411.0 - 100 * 4.5 - 5.0)
    assert PaperTradingService(db).store.integrity_check()


def test_concurrent_schema_init_is_idempotent(tmp_path):
    """并发首开同一空库不得因 schema_version UNIQUE 竞争失败。"""
    db = tmp_path / "race.db"
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def open_store() -> None:
        barrier.wait()
        try:
            PaperStore(db)
        except Exception as exc:  # noqa: BLE001 - 收集后统一断言
            errors.append(exc)

    threads = [threading.Thread(target=open_store, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert PaperStore(db).integrity_check()


# ---------------------------------------------------------------- 备份与运维
def test_backup_restore_preserves_state(tmp_path):
    svc = _svc(tmp_path)
    svc.ensure_account("T", 12411.0, config_hash="h", created_day=DAY)
    svc.execute("T", DAY, [_buy()], _q(), _ctx())
    svc.record_dividend("T", DAY, "dv1", "510300.SH", 3.0)
    bak = svc.backup(tmp_path / "backup" / "paper_bak.db")
    restored = PaperTradingService(bak)
    state = restored.load_state("T")
    assert state.cash == pytest.approx(svc.load_state("T").cash)
    assert [(l.qty, l.price) for l in state.lots] == [(l.qty, l.price)
                                                      for l in svc.load_state("T").lots]
    assert restored.store.integrity_check()


def test_valuation_settlement_upsert(tmp_path):
    svc = _svc(tmp_path)
    svc.ensure_account("T", 12411.0, config_hash="h", created_day=DAY)
    svc.execute("T", DAY, [_buy()], _q(), _ctx())
    v1 = svc.settle_valuation("T", DAY, _q(price=4.5),
                              symbol_asset_class={"510300.SH": AssetClass.ETF})
    v2 = svc.settle_valuation("T", DAY, _q(price=4.6),
                              symbol_asset_class={"510300.SH": AssetClass.ETF})
    assert v2.equity > v1.equity
    rows = svc.store._conn().execute(
        "SELECT equity FROM daily_valuation WHERE slot='T'").fetchall()
    assert len(rows) == 1 and rows[0][0] == pytest.approx(v2.equity)


def test_outbox_lifecycle(tmp_path):
    svc = _svc(tmp_path)
    svc.ensure_account("T", 12411.0, config_hash="h", created_day=DAY)
    svc.execute("T", DAY, [_buy()], _q(), _ctx())
    pending = svc.pending_notifications()
    assert len(pending) == 1 and pending[0][6] == "pending"
    svc.mark_notification(pending[0][0], "queued")
    assert svc.pending_notifications() == []
    svc.mark_notification(pending[0][0], "acknowledged")
    with pytest.raises(PaperStoreError):
        svc.mark_notification(pending[0][0], "delivered")   # 非法状态拒绝


# ---------------------------------------------------------------- 行情适配
def test_build_quote_adapter():
    q = build_quote("510300.SH", {"price": 4.5, "pct": 1.2})
    assert q.price == 4.5 and not q.is_stale
    assert build_quote("X", {"price": 0}).price == 0.0            # 无效价由核心拒绝
    assert build_quote("X", {"price": 1.0, "is_stale": True}).is_stale
    parsed = build_quote("X", {"price": 1.0, "ts": "2026-10-08T14:00:00"})
    assert parsed.timestamp is not None and parsed.timestamp.year == 2026
    assert build_quote("X", {"price": 1.0, "ts": "not-a-date"}).timestamp is None

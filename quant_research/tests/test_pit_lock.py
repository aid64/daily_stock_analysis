# -*- coding: utf-8 -*-
"""PIT 跨进程写锁回归（tail / assemble 共用）。"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

from scripts import pit_lock, update_pit_tail


def test_acquire_release_and_exclusion(tmp_path):
    assert pit_lock.acquire(tmp_path) is True
    assert pit_lock.lock_path(tmp_path).exists()
    assert pit_lock.acquire(tmp_path) is False          # 本进程仍持有
    pit_lock.release(tmp_path)
    assert pit_lock.acquire(tmp_path) is True
    pit_lock.release(tmp_path)


def test_dead_owner_lock_is_reclaimed(tmp_path):
    path = pit_lock.lock_path(tmp_path)
    path.write_text(json.dumps({
        "pid": 99999999,
        "started": (datetime.now() - timedelta(days=1)).isoformat(),
    }), encoding="utf-8")
    assert pit_lock.acquire(tmp_path) is True
    pit_lock.release(tmp_path)


def test_unparseable_old_lock_is_reclaimed_but_fresh_lock_is_not(tmp_path):
    path = pit_lock.lock_path(tmp_path)
    path.write_text("garbage", encoding="utf-8")
    assert pit_lock.acquire(tmp_path) is False           # 新鲜但不可解析 → 保守不抢
    old = time.time() - pit_lock.STALE_SECONDS - 10
    os.utime(path, (old, old))
    assert pit_lock.acquire(tmp_path) is True            # 超龄 → 清理重试
    pit_lock.release(tmp_path)


def test_tail_skips_when_lock_is_held(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    assert pit_lock.acquire(tmp_path) is True
    assert update_pit_tail.main(["--raw-dir", str(raw_dir), "--limit", "0"]) == 0
    pit_lock.release(tmp_path)

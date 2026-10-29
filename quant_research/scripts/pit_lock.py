# -*- coding: utf-8 -*-
"""PIT 写入跨进程锁。

`update_pit_tail.py`（Windows 每日任务 / server 09:00 管线）与
`build_pit_snapshot.py --assemble-only` 可能在不同调度器下同时启动；
raw CSV 与 parquet 都是非原子写，必须串行化。锁文件放在 PIT 目录，
进程退出/异常时由调用方 finally 释放；持锁进程已死或锁龄超过 6h
视为残留（全量 PIT 构建上限 2h）。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

LOCK_NAME = ".pit_update.lock.json"
STALE_SECONDS = 6 * 3600


def lock_path(pit_dir: str | Path) -> Path:
    return Path(pit_dir) / LOCK_NAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def acquire(pit_dir: str | Path, wait_seconds: float = 0.0) -> bool:
    """尝试获取 PIT 写锁；返回 False 表示另一进程正在更新（调用方应跳过）。"""
    path = lock_path(pit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({
                "pid": os.getpid(),
                "started": datetime.now().isoformat(),
            }).encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            owner = None
            try:
                owner = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, AttributeError):
                pass
            pid = None
            started = None
            if isinstance(owner, dict):
                try:
                    pid = int(owner.get("pid"))
                except (TypeError, ValueError):
                    pid = None
                try:
                    started = datetime.fromisoformat(str(owner.get("started")))
                except (TypeError, ValueError):
                    started = None
            stale = started is not None and (
                datetime.now() - started).total_seconds() > STALE_SECONDS
            if pid and _pid_alive(pid) and not stale:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.5)
                continue
            # 属主已死/锁龄超限；不可解析时按 mtime 判断，避免永久卡死。
            if not isinstance(owner, dict):
                try:
                    age = time.time() - path.stat().st_mtime
                except OSError:
                    age = 0
                if age <= STALE_SECONDS:
                    return False
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue


def release(pit_dir: str | Path) -> None:
    try:
        lock_path(pit_dir).unlink()
    except FileNotFoundError:
        pass

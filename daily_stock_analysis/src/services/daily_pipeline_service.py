# -*- coding: utf-8 -*-
"""每日自动化管线（CLOSURE-FIX-PLAN G4）：PIT 增量 → 信号导出 → 操作单 → 推送

串行执行（quant 侧脚本走子进程，遵守跨项目契约——不 import）：
  1. update_pit_tail.py --limit 50（东财源增量）
  2. build_pit_snapshot.py --assemble-only --start 2018
  3. run_paper_tracking.py --export-plan
  4. dsa 侧生成操作单（幂等：当日已有 pending 单则跳过重新生成，只推送）
  5. 推送操作单文本（NotificationService, route_type=report）
  6. 刷新 E2 择时 JSON（G6 后由 pit_end 动态计算——本管线只重跑脚本）

每日 09:00 触发（轮询模式，同 digest：5 分钟检查一次到点执行，当日标记防重跑）。
任一步失败 → 推 system_error 告警，不中断其他后台任务。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("daily_pipeline")

_ROOT = Path(__file__).resolve().parents[2]          # daily_stock_analysis/
_REPO_ROOT = _ROOT.parents[0]                        # 仓库根
_QUANT_SCRIPTS = _REPO_ROOT / "quant_research" / "scripts"
_STATUS_FILE = _ROOT / "output" / "pipeline_status.json"
DAILY_PIPELINE_TIME = "09:00"


def _run_step(name: str, cmd: list, timeout: int = 600) -> bool:
    """跑一步子进程命令。返回成功与否。"""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(_QUANT_SCRIPTS), encoding="utf-8", errors="replace")
        if result.returncode != 0:
            logger.warning("[pipeline] %s 失败 rc=%d: %s",
                           name, result.returncode,
                           (result.stderr or result.stdout or "")[:300])
            return False
        logger.info("[pipeline] %s 完成", name)
        return True
    except subprocess.TimeoutExpired:
        logger.warning("[pipeline] %s 超时（%ds）", name, timeout)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("[pipeline] %s 异常: %r", name, exc)
        return False


def _write_status(step: str, ok: bool, detail: str = "") -> None:
    """管线状态写文件（Web 横幅/G2 的数据源）。"""
    try:
        status = {
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "last_step": step,
            "ok": ok,
            "detail": detail[:200],
            "date": date.today().isoformat(),
        }
        _STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATUS_FILE.write_text(
            json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def run_daily_pipeline(force: bool = False) -> bool:
    """执行完整管线。幂等保护：当日已成功则跳过（force 覆盖）。"""
    if not force and _STATUS_FILE.exists():
        try:
            prev = json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
            if prev.get("date") == date.today().isoformat() and prev.get("ok"):
                logger.info("[pipeline] 今日已成功执行，跳过")
                return True
        except (json.JSONDecodeError, OSError):
            pass

    logger.info("[pipeline] 开始执行")
    py = sys.executable

    # 1-3: quant 侧三步（子进程，跨项目契约）
    steps = [
        ("pit_tail", [py, "update_pit_tail.py", "--limit", "50"], 600),
        ("assemble", [py, "build_pit_snapshot.py",
                      "--assemble-only", "--start", "2018"], 300),
        ("export_plan", [py, "run_paper_tracking.py", "--export-plan"], 300),
    ]
    for name, cmd, timeout in steps:
        if not _run_step(name, cmd, timeout):
            _write_status(name, False, f"{name} step failed")
            _alert_failure(name)
            return False

    # 4: 生成操作单（幂等）
    try:
        from bot.commands.order_sheet import _build_sheet_text
        from src.notification import NotificationService

        text = _build_sheet_text()
        # 5: 推送
        notifier = NotificationService()
        notifier.send(text, route_type="report",
                      dedup_key=f"daily_pipeline:{date.today().isoformat()}")
        _write_status("push_order_sheet", True)
        logger.info("[pipeline] 操作单已推送")
        return True
    except Exception as exc:  # noqa: BLE001
        _write_status("push_order_sheet", False, str(exc))
        _alert_failure("push_order_sheet")
        return False


def _alert_failure(step: str) -> None:
    """管线失败 → 推 system_error 告警（不中断其他任务）。"""
    try:
        from src.notification import NotificationService

        NotificationService().send(
            f"每日管线步骤失败：{step}（{datetime.now().strftime('%H:%M')}）",
            route_type="system_error",
            dedup_key=f"pipeline_fail:{date.today().isoformat()}")
    except Exception:  # noqa: BLE001
        pass


def should_run_pipeline_now(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    try:
        hh, mm = DAILY_PIPELINE_TIME.split(":")[:2]
        return now.hour * 60 + now.minute >= int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return False


def daily_pipeline_background_task(config, config_provider=None):
    """供 runtime_scheduler 注册的后台任务（轮询模式）。"""

    def _task() -> None:
        try:
            if not should_run_pipeline_now():
                return
            run_daily_pipeline()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[pipeline] background task failed: %r", exc)

    return {
        "name": "daily_pipeline",
        "task": _task,
        "interval_seconds": 300,
        "run_immediately": False,
    }

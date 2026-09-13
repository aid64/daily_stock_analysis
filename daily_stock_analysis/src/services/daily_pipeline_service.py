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
from typing import Any, Dict, Optional

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


def _write_status(step: str, ok: bool, detail: str = "",
                  notify_ok: Optional[bool] = None) -> None:
    """管线状态写文件（Web 横幅/G2 的数据源）。

    B2：notify_ok 与 ok 分离——决策完成（ok）不等于通知送达。
    """
    try:
        status = {
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "last_step": step,
            "ok": ok,
            "notify_ok": bool(notify_ok) if notify_ok is not None else ok,
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

    # 1-3: quant 侧三步（子进程，跨项目契约）。
    # PIT 刷新节奏（FINAL-SOLUTION 0.1）：周日全量（914 只约 2 小时，
    # 防截面断裂回退到 56 只），工作日增量 50 只 + 优先队列保证目标债新鲜。
    is_sunday = date.today().weekday() == 6
    pit_cmd = ([py, "update_pit_tail.py"] if is_sunday
               else [py, "update_pit_tail.py", "--limit", "50"])
    steps = [
        ("pit_tail", pit_cmd, 10800 if is_sunday else 600),
        ("assemble", [py, "build_pit_snapshot.py",
                      "--assemble-only", "--start", "2018"], 300),
        ("export_plan", [py, "run_paper_tracking.py", "--export-plan"], 300),
    ]
    for name, cmd, timeout in steps:
        if not _run_step(name, cmd, timeout):
            _write_status(name, False, f"{name} step failed")
            _alert_failure(name)
            return False
        if name == "assemble":
            _check_cross_section_coverage()

    # 3.5: 决策快照（W3）——计划导出后立即落一份可追溯快照
    try:
        from src.services.decision_snapshot_service import refresh
        refresh()
    except Exception as exc:  # noqa: BLE001 - 快照失败不阻塞管线
        logger.warning("[pipeline] decision snapshot refresh failed: %r", exc)

    # 4: 生成操作单（幂等）
    try:
        from bot.commands.order_sheet import _build_sheet_text
        from src.notification import NotificationService
        from src.services.cb_signal_bridge import load_cb_plan
        from src.services.notification_digest_service import (
            DIGEST_BYPASS_ATTR, append_to_bus)

        text = _build_sheet_text()
        plan = load_cb_plan() or {}
        target_symbols = sorted(str(s).upper() for s in (plan.get("targets") or {}))
        stale_symbols = sorted(
            str(s).upper() for s, payload in (plan.get("targets") or {}).items()
            if not payload or float(payload.get("t1_close") or 0) <= 0
        )
        logical_id = f"daily_operation_sheet:{date.today().isoformat()}"
        dedup = f"daily_pipeline:{date.today().isoformat()}"
        payload = {
            "artifact_type": "daily_operation_sheet",
            "logical_id": logical_id,
            "source": "daily_pipeline",
            "priority": "high",
            "symbols": target_symbols,
            "status": "degraded" if stale_symbols else "ready",
            "pit_end": plan.get("pit_end"),
            "plan_generated_at": plan.get("generated_at"),
            "stale_symbols": stale_symbols,
        }
        notifier = NotificationService()
        if text.startswith("⚠"):
            # 新鲜度门槛拒绝：即时告警送达，不进摘要总线压到晚间
            result = notifier.send_with_results(
                text, route_type="system_error", dedup_key=dedup)
        else:
            # 盘前操作单契约（蓝图终局形态）：①总线留索引（logical_id
            # 替换语义，晚间摘要只列一行）②立即直发到渠道——report 路由
            # 默认被摘要总线拦截会压到 18:00，违背 09:00 盘前推送。
            append_to_bus(content=text, route_type="report", severity="info",
                          dedup_key=dedup, structured_payload=payload)
            setattr(notifier, DIGEST_BYPASS_ATTR, True)
            result = notifier.send_with_results(
                text, route_type="report", dedup_key=dedup,
                structured_payload=payload)
        # B2（OPTIMIZATION-PLAN-V2）：区分"决策完成"与"通知送达"——
        # 通知失败不埋没决策，但 status 拆字段并推告警，次日可补发。
        notify_ok = bool(getattr(result, "success", False))
        _write_status("push_order_sheet", True,
                      detail="" if notify_ok else "通知未送达（次日重试）",
                      notify_ok=notify_ok)
        if not notify_ok:
            logger.error("[pipeline] 操作单通知未送达（status=%s）",
                         getattr(result, "status", "?"))
            _alert_failure_custom("操作单已生成但通知未送达——请检查飞书渠道；"
                                  "单据内容可用 /操作单 查询。")
        logger.info("[pipeline] 操作单已推送（notify_ok=%s）", notify_ok)
        return True
    except Exception as exc:  # noqa: BLE001
        _write_status("push_order_sheet", False, str(exc))
        _alert_failure("push_order_sheet")
        return False


def _check_cross_section_coverage(min_bonds: int = 200) -> None:
    """assemble 后护栏：PIT 末端截面券数过低说明增量管道断裂（9 月曾跌到 56）。

    只告警不失败——让 export 继续（目标债有优先队列保障），但用户会收到
    system_error 提示去跑全量刷新。
    """
    try:
        import pandas as pd

        parquet = (_REPO_ROOT / "quant_research" / "data" / "pit"
                   / "convertible_pit.parquet")
        table = pd.read_parquet(parquet, columns=["as_of", "symbol"])
        latest = table["as_of"].max()
        n_bonds = int(table.loc[table["as_of"] == latest, "symbol"].nunique())
        if n_bonds < min_bonds:
            _alert_failure_custom(
                f"PIT 覆盖率过低：{pd.Timestamp(latest).date()} 截面仅 {n_bonds} 只"
                f"（阈值 {min_bonds}）——增量管道疑似断裂，请在周日全量刷新前"
                "手动执行 py scripts/update_pit_tail.py（quant_research/）")
            logger.warning("[pipeline] cross-section coverage low: %d bonds", n_bonds)
    except Exception as exc:  # noqa: BLE001 - 护栏失败不影响管线主流程
        logger.warning("[pipeline] coverage check failed: %r", exc)


def _alert_failure_custom(message: str) -> None:
    """带自定义文案的 system_error 告警（护栏用）。"""
    try:
        from src.notification import NotificationService

        NotificationService().send(
            message, route_type="system_error",
            dedup_key=f"pipeline_guard:{date.today().isoformat()}")
    except Exception:  # noqa: BLE001
        pass


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

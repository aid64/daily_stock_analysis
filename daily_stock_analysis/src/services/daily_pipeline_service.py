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
    """执行完整管线。幂等保护：当日已成功则跳过（force 覆盖）。

    E2.2 并发互斥：server 重试与手动补跑可能同时进入——用 O_EXCL 锁文件
    串行化（持锁方写 PID，陈旧锁=持锁进程已死则抢占）。
    """
    if _acquire_pipeline_lock() is None:
        logger.info("[pipeline] 另一实例持有管线锁——本次跳过")
        return True
    try:
        return _run_daily_pipeline_locked(force)
    finally:
        _PIPELINE_LOCK_PATH.unlink(missing_ok=True)  # 释放锁（无句柄需关闭）


def _run_daily_pipeline_locked(force: bool = False) -> bool:
    if not force and _STATUS_FILE.exists():
        try:
            prev = json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
            if prev.get("date") == date.today().isoformat() and prev.get("ok"):
                logger.info("[pipeline] 今日已成功执行，跳过")
                return True
        except (json.JSONDecodeError, OSError):
            pass

    logger.info("[pipeline] 开始执行")

    # 交易日历门：非交易日（周末/法定节假日）不跑决策管线——无新截面无
    # 新价，产出只能是重复单或陈旧单。force=True 手动补跑可越过；
    # 日历不可用/异常时 fail-open 照常执行（维持既有行为）。
    if not force:
        try:
            from src.core.trading_calendar import MarketPhase, infer_market_phase
            if infer_market_phase("cn") == MarketPhase.NON_TRADING:
                logger.info("[pipeline] 交易日历门：今日非交易日——跳过管线")
                _write_status("calendar_gate", True, "非交易日（周末/节假日）——管线跳过")
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pipeline] 交易日历门检查失败（fail-open 继续）: %r", exc)

    py = sys.executable

    # 1-3: quant 侧三步（子进程，跨项目契约）。
    # PIT 刷新节奏（FINAL-SOLUTION 0.1）：周日全量（914 只约 2 小时，
    # 防截面断裂回退到 56 只），工作日增量 50 只 + 优先队列保证目标债新鲜。
    is_sunday = date.today().weekday() == 6
    # --time-budget 540：给 600s 步骤超时留余量——预算用尽优雅退出
    #（增量断点安全），不再因网络劣化把整步判失败
    pit_cmd = ([py, "update_pit_tail.py"] if is_sunday
               else [py, "update_pit_tail.py", "--limit", "50", "--time-budget", "420"])
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

    # 3.4: 全市场扫描（买入侧原料线，蓝图 ②-A/②-B）——快照落库 + 规则漏斗。
    # 失败不阻塞主决策（买入观察池降级为空），但必须留痕到状态文件。
    scan_ok, scan_detail = False, ""
    try:
        from src.services.market_scan_service import (
            fetch_and_store_snapshot, run_funnel)
        snapshot = fetch_and_store_snapshot()
        funnel = run_funnel(snapshot)
        scan_ok = True
        scan_detail = funnel.step_log
        logger.info("[pipeline] market scan: %s", scan_detail)
    except Exception as exc:  # noqa: BLE001 - 扫描失败不阻塞管线
        scan_detail = str(exc)
        logger.warning("[pipeline] market scan failed: %r", exc)
    _write_status("market_scan", scan_ok, scan_detail)

    # 3.5: 决策快照（W3）——计划导出后立即落一份可追溯快照
    try:
        from src.services.decision_snapshot_service import refresh
        refresh()
    except Exception as exc:  # noqa: BLE001 - 快照失败不阻塞管线
        logger.warning("[pipeline] decision snapshot refresh failed: %r", exc)

    # 4: 生成账户决策包（R1：决策对象=真实持仓+现金，非 CB 策略 diff）
    try:
        from bot.commands.order_sheet import _build_sheet_text
        from src.notification import NotificationService
        from src.services.cb_signal_bridge import load_cb_plan
        from src.services.notification_digest_service import (
            DIGEST_BYPASS_ATTR, append_to_bus)

        text = _build_sheet_text()
        plan = load_cb_plan() or {}

        # 持仓符号集（决策包 symbols）从快照取真实账户，而非 CB 契约目标
        held_symbols: list = []
        try:
            from src.services.portfolio_service import PortfolioService
            snapshot = PortfolioService().get_portfolio_snapshot(
                cost_method="fifo", include_realtime=False)
            for account in snapshot.get("accounts") or []:
                for position in account.get("positions") or []:
                    if float(position.get("quantity") or 0) > 0:
                        digits = "".join(
                            ch for ch in str(position.get("symbol")) if ch.isdigit())
                        held_symbols.append(digits)
        except Exception as exc:  # noqa: BLE001 - 符号集缺失不阻塞推送
            logger.warning("[pipeline] 持仓符号集读取失败: %r", exc)

        logical_id = f"daily_operation_sheet:{date.today().isoformat()}"
        dedup = f"daily_pipeline:{date.today().isoformat()}"
        payload = {
            "artifact_type": "daily_operation_sheet",
            "logical_id": logical_id,
            "source": "daily_pipeline",
            "priority": "high",
            "symbols": sorted(set(held_symbols)),
            "status": "ready" if held_symbols else "degraded",
            "pit_end": plan.get("pit_end"),
            "plan_generated_at": plan.get("generated_at"),
        }
        notifier = NotificationService()
        if text.startswith("⚠"):
            # 门槛拒绝：即时告警送达，不进摘要总线压到晚间
            result = notifier.send_with_results(
                text, route_type="system_error", dedup_key=dedup)
        else:
            # 盘前决策包契约：①总线留索引（logical_id 替换语义，晚间摘要
            # 只列一行）②立即直发到渠道——report 路由默认被摘要总线拦截
            # 会压到 18:00，违背 09:00 盘前推送。
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


_PIPELINE_LOCK_PATH = _ROOT / "output" / "pipeline.lock"


def _acquire_pipeline_lock():
    """O_EXCL 抢锁；陈旧锁（持锁 PID 已不存在）抢占。返回文件句柄或 None。"""
    import os as _os

    _PIPELINE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = _os.open(str(_PIPELINE_LOCK_PATH),
                      _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY)
        _os.write(fh, str(_os.getpid()).encode())
        _os.close(fh)
        return _PIPELINE_LOCK_PATH
    except FileExistsError:
        try:
            holder = int(_PIPELINE_LOCK_PATH.read_text(encoding="utf-8").strip())
            import psutil  # 可选依赖；缺失时保守视为活着
            psutil.Process(holder)
        except (ValueError, ImportError):
            logger.warning("[pipeline] 陈旧管线锁（持锁进程已不存在）——抢占")
            _PIPELINE_LOCK_PATH.unlink(missing_ok=True)
            return _acquire_pipeline_lock()
        except OSError:
            pass
        return None


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

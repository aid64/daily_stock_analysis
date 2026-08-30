# -*- coding: utf-8 -*-
"""Runtime scheduler service for long-lived API/Web/Desktop processes."""

from __future__ import annotations

import logging
import os
import threading
import _thread
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Set

from src.config import Config, get_config
from src.scheduler import Scheduler, normalize_schedule_times
from src.services.data_purge import build_data_purge_background_task

logger = logging.getLogger(__name__)
CLI_SCHEDULER_OWNER_ENV = "DSA_CLI_SCHEDULER_OWNS_SCHEDULE"
RUNTIME_SCHEDULER_FORCE_ENABLED_ENV = "DSA_RUNTIME_SCHEDULER_FORCE_ENABLED"
RUNTIME_SCHEDULER_RUN_IMMEDIATELY_ENV = "DSA_RUNTIME_SCHEDULER_RUN_IMMEDIATELY"
RUNTIME_SCHEDULER_SUPPRESS_START_ENV = "DSA_RUNTIME_SCHEDULER_SUPPRESS_START"
RUNTIME_SCHEDULER_ARGS_ENV = "DSA_RUNTIME_SCHEDULER_ARGS"
_RUNTIME_ANALYSIS_LOCK = threading.Lock()
SCHEDULE_ARGS_OVERRIDE_KEYS = {
    "no_notify",
    "no_market_review",
    "dry_run",
    "force_run",
    "single_notify",
    "no_context_snapshot",
    "workers",
    "portfolio",
}


def run_with_global_analysis_lock(
    task_runner: Callable[[Config, Any, Optional[List[str]]], Any],
    config: Config,
    args: Any,
    stock_codes: Optional[List[str]] = None,
    *,
    blocking: bool = True,
) -> bool:
    """Execute a task while holding the shared runtime analysis lock."""
    if not _RUNTIME_ANALYSIS_LOCK.acquire(blocking=blocking):
        return False
    try:
        task_runner(config, args, stock_codes)
    finally:
        _RUNTIME_ANALYSIS_LOCK.release()
    return True


def _agent_event_monitor_interval_seconds(config: Config) -> int:
    """Return the validated Event Monitor polling interval in seconds."""
    interval_minutes = getattr(config, "agent_event_monitor_interval_minutes", 5)
    try:
        interval_minutes = max(1, int(interval_minutes))
    except (TypeError, ValueError):  # pragma: no cover - defensive branch
        logger.warning(
            "Invalid AGENT_EVENT_MONITOR_INTERVAL_MINUTES=%r; use fallback 5",
            interval_minutes,
        )
        interval_minutes = 5
    return interval_minutes * 60


def build_agent_event_monitor_background_tasks(
    config: Config,
    *,
    config_provider: Callable[[], Config],
) -> List[Dict[str, Any]]:
    """Build scheduler background tasks used by the runtime scheduler."""
    if not getattr(config, "agent_event_monitor_enabled", False):
        return []

    from src.services.alert_worker import AlertWorker

    interval_seconds = _agent_event_monitor_interval_seconds(config)
    try:
        alert_worker = AlertWorker(config_provider=config_provider)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("Failed to initialize AlertWorker for event monitor: %s", exc)
        return []

    def event_monitor_task() -> None:
        stats = alert_worker.run_once()
        triggered_count = stats.get("triggered", 0)
        if triggered_count:
            logger.info("[EventMonitor] triggered %d alert(s)", triggered_count)

    return [{
        "task": event_monitor_task,
        "interval_seconds": interval_seconds,
        "run_immediately": True,
        "name": "agent_event_monitor",
    }]


def _market_monitor_interval_seconds(config: Config) -> int:
    """Return the validated market-monitor polling interval in seconds."""
    interval_minutes = getattr(config, "market_monitor_interval_minutes", 60)
    try:
        interval_minutes = max(5, int(interval_minutes))
    except (TypeError, ValueError):  # pragma: no cover - defensive branch
        logger.warning(
            "Invalid MARKET_MONITOR_INTERVAL_MINUTES=%r; use fallback 60",
            interval_minutes,
        )
        interval_minutes = 60
    return interval_minutes * 60


def build_market_monitor_background_tasks(config: Config) -> List[Dict[str, Any]]:
    """构建三个市场监控的后台任务（事件套利 / 转债打新日历 / ETF 溢价护栏）。

    Bug-2 修复（2026-08-29）：这些监控此前只注册在 CLI schedule 模式的
    background_tasks（main.py），serve/Web 模式下 runtime scheduler 的
    默认装配不含它们，导致 `--serve --schedule` 或 Web 调度时监控静默
    失效。现与 agent_event_monitor / data_purge 相同的模式接入。
    任务闭包惰性构造服务实例（首次调用时），与服务既有行为一致。
    """
    interval_seconds = _market_monitor_interval_seconds(config)
    tasks: List[Dict[str, Any]] = []

    if getattr(config, "event_arbitrage_monitor_enabled", False):
        service_holder: Dict[str, Any] = {}

        def event_arbitrage_task() -> None:
            service = service_holder.get("svc")
            if service is None:
                from src.services.event_arbitrage_monitor import EventArbitrageMonitorService
                service = EventArbitrageMonitorService()
                service_holder["svc"] = service
            stats = service.run_once()
            if stats.get("notified", 0):
                logger.info(
                    "[EventArbitrageMonitor] 本轮推送 %d 条事件（抑制重复 %d 条）",
                    stats["notified"], stats.get("suppressed", 0),
                )

        tasks.append({
            "task": event_arbitrage_task,
            "interval_seconds": interval_seconds,
            "run_immediately": True,
            "name": "event_arbitrage_monitor",
        })

    if getattr(config, "bond_new_issue_monitor_enabled", False):
        service_holder_bond: Dict[str, Any] = {}

        def bond_new_issue_task() -> None:
            service = service_holder_bond.get("svc")
            if service is None:
                from src.services.bond_new_issue_calendar import BondNewIssueCalendarService
                service = BondNewIssueCalendarService()
                service_holder_bond["svc"] = service
            stats = service.run_once()
            if stats.get("notified", 0):
                logger.info(
                    "[BondNewIssueCalendar] 本轮推送 %d 条提醒（抑制重复 %d 条）",
                    stats["notified"], stats.get("suppressed", 0),
                )

        tasks.append({
            "task": bond_new_issue_task,
            "interval_seconds": interval_seconds,
            "run_immediately": True,
            "name": "bond_new_issue_monitor",
        })

    if getattr(config, "etf_premium_monitor_enabled", False):
        service_holder_etf: Dict[str, Any] = {}
        etf_symbol = str(getattr(config, "etf_premium_symbol", "511380") or "511380")
        etf_threshold = float(getattr(config, "etf_premium_max_pct", 1.5))

        def etf_premium_task() -> None:
            service = service_holder_etf.get("svc")
            if service is None:
                from src.services.etf_premium_monitor import EtfPremiumMonitorService
                service = EtfPremiumMonitorService(
                    symbol=etf_symbol, max_premium_pct=etf_threshold,
                )
                service_holder_etf["svc"] = service
            stats = service.run_once()
            if stats.get("notified", 0):
                logger.info(
                    "[EtfPremiumMonitor] %s 溢价 %.2f%% 超阈值，已推送暂缓买入提醒",
                    stats.get("symbol", etf_symbol),
                    stats.get("premium_pct") or 0.0,
                )

        tasks.append({
            "task": etf_premium_task,
            "interval_seconds": interval_seconds,
            "run_immediately": True,
            "name": "etf_premium_monitor",
        })

    return tasks


class RuntimeSchedulerService:
    """Manage scheduled analysis inside the current API/Web/Desktop process."""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Config] = get_config,
        task_runner: Optional[Callable[[Config, Any, Optional[List[str]]], Any]] = None,
        owns_schedule: Optional[bool] = None,
        force_enabled: bool = False,
        run_immediately_in_background: bool = False,
        background_tasks_provider: Optional[Callable[[Config], List[Dict[str, Any]]]] = None,
        schedule_args_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._config_provider = config_provider
        self._task_runner = task_runner
        if owns_schedule is None:
            owns_schedule = os.getenv(CLI_SCHEDULER_OWNER_ENV, "").strip().lower() not in {
                "1",
                "true",
                "yes",
                "on",
            }
        self._owns_schedule = owns_schedule
        self._force_enabled = force_enabled
        self._run_immediately_in_background = run_immediately_in_background
        self._background_tasks_provider = background_tasks_provider
        self._schedule_args_overrides = {
            key: value
            for key, value in (schedule_args_overrides or {}).items()
            if key in SCHEDULE_ARGS_OVERRIDE_KEYS
        }
        self._background_task_cache: Dict[str, Dict[str, Any]] = {}
        self._background_task_registered_names: Set[str] = set()
        self._lock = threading.RLock()
        self._run_lock = _RUNTIME_ANALYSIS_LOCK
        self._scheduler: Optional[Scheduler] = None
        self._thread: Optional[threading.Thread] = None
        self._enabled = False
        self._last_run_at: Optional[str] = None
        self._last_success_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_skipped_at: Optional[str] = None
        self._last_skip_reason: Optional[str] = None

    def _make_schedule_args(self) -> SimpleNamespace:
        defaults = {
            "schedule": True,
            "no_run_immediately": True,
            "no_notify": False,
            "no_market_review": False,
            "dry_run": False,
            "force_run": False,
            "single_notify": False,
            "no_context_snapshot": False,
            "market_review": False,
            "serve": False,
            "serve_only": True,
            "stocks": None,
            "portfolio": None,
            "workers": None,
        }
        defaults.update(self._schedule_args_overrides)
        return SimpleNamespace(**defaults)

    def _reload_config(self) -> Config:
        from main import _reload_runtime_config

        return _reload_runtime_config()

    def _record_analysis_busy_skip(self) -> None:
        self._last_skipped_at = datetime.now().isoformat()
        self._last_skip_reason = "analysis_already_running"
        logger.warning("Runtime scheduler skipped run: analysis already running")

    def _run_analysis_locked(self, stock_codes: Optional[List[str]]) -> None:
        try:
            config = self._reload_config()
            runner = self._task_runner
            if runner is None:
                from main import run_scheduled_analysis

                runner = run_scheduled_analysis
            self._last_run_at = datetime.now().isoformat()
            result = runner(config, self._make_schedule_args(), stock_codes)
            if result is False:
                raise RuntimeError("runtime scheduled analysis reported failure")
            self._last_success_at = datetime.now().isoformat()
            self._last_error = None
        except Exception as exc:  # noqa: BLE001 - scheduled runs must not kill API process.
            self._last_error = str(exc)
            logger.exception("Runtime scheduled analysis failed: %s", exc)

    def _run_analysis_once(self, stock_codes: Optional[List[str]] = None) -> bool:
        if not self._run_lock.acquire(blocking=False):
            self._record_analysis_busy_skip()
            return False
        try:
            self._run_analysis_locked(stock_codes)
        finally:
            self._run_lock.release()
        return True

    def _current_times(self) -> List[str]:
        config = self._config_provider()
        return normalize_schedule_times(
            getattr(config, "schedule_times", None),
            fallback_time=getattr(config, "schedule_time", "18:00"),
        )

    def _is_schedule_enabled(self, config: Config) -> bool:
        return self._force_enabled or bool(getattr(config, "schedule_enabled", False))

    def _current_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        if self._background_tasks_provider is not None:
            return self._background_tasks_provider(config)
        tasks: List[Dict[str, Any]] = []
        tasks.extend(self._current_agent_event_monitor_background_tasks(config))
        tasks.extend(self._current_market_monitor_background_tasks(config))
        tasks.extend(self._current_data_purge_background_tasks(config))
        return tasks

    _MARKET_MONITOR_TASK_NAMES = (
        "event_arbitrage_monitor",
        "bond_new_issue_monitor",
        "etf_premium_monitor",
    )

    def _current_market_monitor_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        """装配三个市场监控后台任务（按配置开关；沿用按名缓存模式）。"""
        entries = build_market_monitor_background_tasks(config)
        names = {entry["name"] for entry in entries}
        # 已关闭的监控：清理缓存与注册标记（复用 agent_event_monitor 的清理语义）
        for name in self._MARKET_MONITOR_TASK_NAMES:
            if name not in names:
                self._background_task_cache.pop(name, None)
                self._background_task_registered_names.discard(name)

        result: List[Dict[str, Any]] = []
        for entry in entries:
            name = entry["name"]
            cached = self._background_task_cache.get(name)
            if cached is None:
                cached = dict(entry)
                self._background_task_cache[name] = cached
            else:
                # 保留缓存的 task 闭包避免每次重建，但刷新 interval
                cached["interval_seconds"] = int(entry["interval_seconds"])
            run_immediately = (
                bool(cached.get("run_immediately", False))
                and name not in self._background_task_registered_names
            )
            self._background_task_registered_names.add(name)
            result.append({
                "task": cached["task"],
                "interval_seconds": int(cached["interval_seconds"]),
                "run_immediately": run_immediately,
                "name": name,
            })
        return result

    def _current_data_purge_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        """构建数据清理后台任务 entry。

        当所有 retention 配置均为 0 时返回空列表（不注册清理任务）。
        """
        name = "data_purge"
        entry = build_data_purge_background_task(
            config,
            config_provider=self._reload_config,
        )
        if entry is None:
            self._background_task_cache.pop(name, None)
            self._background_task_registered_names.discard(name)
            return []

        cached = self._background_task_cache.get(name)
        if cached is None:
            cached = dict(entry)
            cached["name"] = name
            self._background_task_cache[name] = cached
        else:
            # 保留缓存的 task 闭包避免每次重建，但刷新 interval
            cached["interval_seconds"] = int(entry["interval_seconds"])
        interval_seconds = int(cached["interval_seconds"])

        run_immediately = (
            bool(cached.get("run_immediately", False))
            and name not in self._background_task_registered_names
        )
        self._background_task_registered_names.add(name)
        return [{
            "task": cached["task"],
            "interval_seconds": interval_seconds,
            "run_immediately": run_immediately,
            "name": name,
        }]

    def _current_agent_event_monitor_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        name = "agent_event_monitor"
        if not getattr(config, "agent_event_monitor_enabled", False):
            self._background_task_cache.pop(name, None)
            self._background_task_registered_names.discard(name)
            return []

        cached = self._background_task_cache.get(name)
        if cached is None:
            entries = build_agent_event_monitor_background_tasks(
                config,
                config_provider=self._reload_config,
            )
            if not entries:
                self._background_task_cache.pop(name, None)
                self._background_task_registered_names.discard(name)
                return []
            cached = dict(entries[0])
            cached["name"] = name
            self._background_task_cache[name] = cached
            interval_seconds = int(cached["interval_seconds"])
        else:
            interval_seconds = _agent_event_monitor_interval_seconds(config)

        run_immediately = (
            bool(cached.get("run_immediately", False))
            and name not in self._background_task_registered_names
        )
        self._background_task_registered_names.add(name)
        return [{
            "task": cached["task"],
            "interval_seconds": interval_seconds,
            "run_immediately": run_immediately,
            "name": name,
        }]

    @staticmethod
    def _run_in_background_thread(target: Callable[[], None]) -> None:
        """Run a callback in a background thread without blocking startup."""
        try:
            _thread.start_new_thread(target, ())
            return
        except Exception:
            # Best-effort fallback for environments where the low-level thread API
            # is unavailable or restricted.
            thread = threading.Thread(target=target, daemon=True)
            thread.start()

    def start(self, *, run_immediately: bool = False) -> None:
        """启动调度器；读取最新 config 决定是否启用、注册后台任务与每日分析任务。

        Args:
            run_immediately: 是否在启动后立即触发一次分析（受 run_immediately_in_background 控制）。
        """
        with self._lock:
            if not self._owns_schedule:
                self.stop()
                return
            config = self._config_provider()
            if not self._is_schedule_enabled(config):
                self.stop()
                return
            background_tasks = self._current_background_tasks(config)
            self.stop()
            times = normalize_schedule_times(
                getattr(config, "schedule_times", None),
                fallback_time=getattr(config, "schedule_time", "18:00"),
            )
            scheduler = Scheduler(
                schedule_time=getattr(config, "schedule_time", "18:00"),
                schedule_times=times,
                schedule_times_provider=self._current_times,
                register_signals=False,
            )
            if run_immediately and self._run_immediately_in_background:
                scheduler.set_daily_task(self._run_analysis_once, run_immediately=False)
            else:
                scheduler.set_daily_task(self._run_analysis_once, run_immediately=run_immediately)
            for entry in background_tasks:
                scheduler.add_background_task(
                    entry["task"],
                    interval_seconds=entry["interval_seconds"],
                    run_immediately=entry.get("run_immediately", False),
                    name=entry.get("name"),
                )
            if run_immediately and self._run_immediately_in_background:
                self._run_in_background_thread(self._run_analysis_once)
            thread = threading.Thread(
                target=scheduler.run,
                daemon=True,
                name="runtime-scheduler",
            )
            self._scheduler = scheduler
            self._thread = thread
            self._enabled = True
            thread.start()

    def stop(self) -> None:
        """停止调度器并清理 scheduler / thread 引用；幂等。"""
        scheduler = self._scheduler
        if scheduler is not None:
            scheduler.stop()
        self._scheduler = None
        self._thread = None
        self._enabled = False

    def reconcile_from_config(
        self,
        *,
        run_immediately: bool = False,
        clear_enabled_override: bool = False,
    ) -> None:
        """按当前 config 重新判定启用/停用状态，必要时重启调度器。

        Args:
            run_immediately: 启用时是否立即触发一次分析。
            clear_enabled_override: 是否清除 force_enabled 覆盖位，恢复 config 默认。
        """
        if clear_enabled_override:
            self._force_enabled = False
        if not self._owns_schedule:
            self.stop()
            return
        config = self._config_provider()
        if self._is_schedule_enabled(config):
            self.start(run_immediately=run_immediately)
        else:
            self.stop()

    def run_now(self) -> Dict[str, Any]:
        """立即触发一次分析（非阻塞，后台线程执行）；返回 accepted/running 状态。"""
        if not self._run_lock.acquire(blocking=False):
            self._record_analysis_busy_skip()
            return {
                "accepted": False,
                "running": True,
                "reason": "analysis_already_running",
            }

        def run_and_release() -> None:
            try:
                self._run_analysis_locked(None)
            finally:
                self._run_lock.release()

        worker = threading.Thread(
            target=run_and_release,
            daemon=True,
            name="runtime-scheduler-run-now",
        )
        try:
            worker.start()
        except Exception:
            self._run_lock.release()
            raise
        return {"accepted": True, "running": True}

    def status(self) -> Dict[str, Any]:
        """返回调度器当前状态快照（enabled / running / schedule_times / next_run_at）。"""
        scheduler = self._scheduler
        jobs = scheduler.schedule.get_jobs() if scheduler is not None else []
        next_run = None
        if jobs:
            next_run = min(job.next_run for job in jobs).isoformat()
        if scheduler is not None:
            schedule_times = list(getattr(scheduler, "schedule_times", []))
        else:
            try:
                schedule_times = self._current_times()
            except Exception:  # pragma: no cover - defensive status fallback
                schedule_times = []
        running = self._run_lock.locked()
        return {
            "enabled": self._enabled,
            "running": running,
            "schedule_times": schedule_times,
            "next_run_at": next_run,
            "last_run_at": self._last_run_at,
            "last_success_at": self._last_success_at,
            "last_error": self._last_error,
            "last_skipped_at": self._last_skipped_at,
            "last_skip_reason": self._last_skip_reason,
        }

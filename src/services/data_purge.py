# -*- coding: utf-8 -*-
"""过期数据自动清理服务。

由 runtime_scheduler_service 注册为后台任务，按 DATA_PURGE_INTERVAL_SECONDS 间隔执行。
清理范围：
  - llm_usage 表（按 called_at）
  - decision_signals + decision_signal_outcomes + decision_signal_feedback（按 created_at）
  - analysis_history 表 + 关联 BacktestResult / DecisionSignal（按 created_at）
  - reports/*.md 报告文件（按 mtime）

retention_days <= 0 表示对应类型永不清理。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import Config
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


def purge_expired_reports(retention_days: int, reports_dir: Optional[Path] = None) -> int:
    """删除超过保留天数的报告文件（reports/*.md）。

    Args:
        retention_days: 保留天数；<=0 表示不清理。
        reports_dir: 报告目录；None 时使用项目根目录下的 reports/。

    Returns:
        实际删除的文件数。
    """
    if int(retention_days) <= 0:
        return 0
    if reports_dir is None:
        reports_dir = Path(__file__).resolve().parent.parent.parent / "reports"
    if not reports_dir.exists():
        return 0
    cutoff_ts = time.time() - int(retention_days) * 86400
    deleted = 0
    for entry in reports_dir.iterdir():
        if not entry.is_file() or entry.suffix.lower() != ".md":
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff_ts:
            try:
                entry.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("清理报告文件失败 %s: %s", entry, exc)
    return deleted


def run_data_purge(config: Config) -> Dict[str, int]:
    """执行一次完整的数据清理，返回各类删除统计。

    单次清理内的各类异常被独立捕获，避免某一类失败中断后续清理。

    Args:
        config: 运行时配置实例。

    Returns:
        各类清理删除数量的字典。
    """
    stats: Dict[str, int] = {
        "llm_usage": 0,
        "decision_signals": 0,
        "analysis_history": 0,
        "reports": 0,
    }

    # 数据库清理
    try:
        db = DatabaseManager.get_instance()
    except Exception:
        db = None
    if db is not None:
        try:
            stats["llm_usage"] = db.purge_expired_llm_usage(
                int(getattr(config, "llm_usage_retention_days", 0))
            )
        except Exception as exc:
            logger.warning("清理 llm_usage 失败: %s", exc)
        try:
            stats["decision_signals"] = db.purge_expired_decision_signals(
                int(getattr(config, "decision_signal_retention_days", 0))
            )
        except Exception as exc:
            logger.warning("清理 decision_signals 失败: %s", exc)
        try:
            stats["analysis_history"] = db.purge_expired_analysis_history(
                int(getattr(config, "analysis_history_retention_days", 0))
            )
        except Exception as exc:
            logger.warning("清理 analysis_history 失败: %s", exc)

    # 报告文件清理
    try:
        stats["reports"] = purge_expired_reports(
            int(getattr(config, "report_retention_days", 0))
        )
    except Exception as exc:
        logger.warning("清理报告文件失败: %s", exc)

    total = sum(stats.values())
    if total > 0:
        logger.info(
            "数据清理完成: llm_usage=%d, decision_signals=%d, analysis_history=%d, reports=%d",
            stats["llm_usage"],
            stats["decision_signals"],
            stats["analysis_history"],
            stats["reports"],
        )
    return stats


def build_data_purge_background_task(
    config: Config,
    config_provider: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """构建数据清理后台任务 entry，供 runtime_scheduler_service 注册。

    当所有 retention 配置均为 0 时返回 None，表示不注册清理任务。

    Args:
        config: 当前运行时配置。
        config_provider: 可选的 config 刷新回调；传入时任务每次执行前重新读取最新配置。

    Returns:
        任务 entry dict（含 task / interval_seconds / run_immediately / name）或 None。
    """
    interval_seconds = int(getattr(config, "data_purge_interval_seconds", 86400))
    retention_keys = (
        "llm_usage_retention_days",
        "decision_signal_retention_days",
        "analysis_history_retention_days",
        "report_retention_days",
    )
    if all(int(getattr(config, key, 0)) <= 0 for key in retention_keys):
        return None

    def _task() -> None:
        active_config = config
        if config_provider is not None:
            try:
                refreshed = config_provider()
                if refreshed is not None:
                    active_config = refreshed
            except Exception as exc:
                logger.warning("数据清理任务刷新配置失败，使用旧配置: %s", exc)
        run_data_purge(active_config)

    return {
        "task": _task,
        "interval_seconds": interval_seconds,
        "run_immediately": False,
        "name": "data_purge",
    }

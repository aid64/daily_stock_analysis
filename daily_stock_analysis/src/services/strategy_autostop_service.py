# -*- coding: utf-8 -*-
"""Strategy auto-stop service for the rule-based timing module.

Two pause triggers:
1. Drawdown breach: pause when max drawdown exceeds threshold.
2. Consecutive underperformance: pause when strategy underperforms
   benchmark for N consecutive evaluation periods.

State is persisted in ``strategy_autostop_state`` (SQLite via existing
DatabaseManager) so decisions survive process restarts and alert
deduplication is possible — only ``active -> paused`` transitions emit
notifications.

This module is orthogonal to the remote LLM decision-signal system: it
operates purely on rule-based timing signals and does not consume remote
screening outputs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from src.config import Config, get_config
from src.storage import DatabaseManager, StrategyAutostopState

logger = logging.getLogger(__name__)


PAUSE_REASON_DRAWDOWN = "drawdown"
PAUSE_REASON_UNDERPERFORM = "underperform"
PAUSE_REASON_MANUAL = "manual"

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"


@dataclass(frozen=True)
class AutostopConfig:
    """Configuration for strategy auto-stop."""

    enabled: bool = True
    drawdown_threshold_pct: float = 20.0
    underperform_periods_threshold: int = 5
    # P3：跑输幅度门——一期跑输基准需超过此百分点才计入连续跑输
    underperform_margin_pct: float = 1.0
    # When True, a paused strategy stays paused until manually resumed.
    # When False, auto-resume once metrics recover below thresholds.
    manual_resume_required: bool = True
    # Recovery thresholds (used when manual_resume_required is False)
    drawdown_recover_pct: float = 10.0
    underperform_recover_periods: int = 2


@dataclass(frozen=True)
class AutostopEvaluation:
    """Result of a single autostop evaluation."""

    strategy_key: str
    status: str  # active / paused
    should_pause: bool
    should_resume: bool
    pause_reason: Optional[str]
    pause_metric_value: Optional[float]
    consecutive_underperform_count: int
    last_drawdown_pct: Optional[float]
    last_strategy_return_pct: Optional[float]
    last_benchmark_return_pct: Optional[float]
    notification_message: Optional[str] = None


@dataclass(frozen=True)
class AutostopStateSnapshot:
    """Read-only snapshot of persisted autostop state."""

    strategy_key: str
    status: str
    paused_at: Optional[datetime]
    pause_reason: Optional[str]
    pause_metric_value: Optional[float]
    consecutive_underperform_count: int
    last_evaluated_at: Optional[datetime]
    last_drawdown_pct: Optional[float]
    last_strategy_return_pct: Optional[float]
    last_benchmark_return_pct: Optional[float]


class StrategyAutostopService:
    """Evaluate and persist strategy auto-stop decisions.

    Usage:
        service = StrategyAutostopService()
        eval_result = service.evaluate(
            strategy_key="momentum_quality_v1",
            max_drawdown_pct=22.5,
            strategy_return_pct=-1.2,
            benchmark_return_pct=0.8,
        )
        if eval_result.should_pause:
            # strategy will be paused and notification sent
            ...
    """

    def __init__(
        self,
        *,
        db_manager: Optional[DatabaseManager] = None,
        config: Optional[Config] = None,
        autostop_config: Optional[AutostopConfig] = None,
        notification_dispatcher: Optional[Any] = None,
    ):
        self.config = config or get_config()
        self.autostop_config = autostop_config or self._build_autostop_config(self.config)
        self._db_manager = db_manager
        self._notification_dispatcher = notification_dispatcher

    @staticmethod
    def _build_autostop_config(cfg: Config) -> AutostopConfig:
        return AutostopConfig(
            enabled=bool(getattr(cfg, "strategy_autostop_enabled", True)),
            drawdown_threshold_pct=float(
                getattr(cfg, "strategy_autostop_drawdown_threshold_pct", 20.0)
            ),
            underperform_periods_threshold=int(
                getattr(cfg, "strategy_autostop_underperform_periods", 5)
            ),
            underperform_margin_pct=float(
                getattr(cfg, "strategy_autostop_underperform_margin_pct", 1.0)
            ),
            manual_resume_required=bool(
                getattr(cfg, "strategy_autostop_manual_resume_required", True)
            ),
            drawdown_recover_pct=float(
                getattr(cfg, "strategy_autostop_drawdown_recover_pct", 10.0)
            ),
            underperform_recover_periods=int(
                getattr(cfg, "strategy_autostop_underperform_recover_periods", 2)
            ),
        )

    def _resolve_db_manager(self) -> DatabaseManager:
        if self._db_manager is not None:
            return self._db_manager
        # Lazy init to avoid import-time DB connection
        self._db_manager = DatabaseManager()
        return self._db_manager

    # ── Public API ──

    def evaluate(
        self,
        *,
        strategy_key: str,
        max_drawdown_pct: Optional[float] = None,
        strategy_return_pct: Optional[float] = None,
        benchmark_return_pct: Optional[float] = None,
        evaluation_timestamp: Optional[datetime] = None,
    ) -> AutostopEvaluation:
        """Evaluate autostop for a single strategy.

        Returns an AutostopEvaluation describing the decision. Persists
        state changes and dispatches notification on active->paused
        transitions.
        """
        if not strategy_key or not strategy_key.strip():
            raise ValueError("strategy_key is required")

        if not self.autostop_config.enabled:
            return self._build_noop_evaluation(strategy_key)

        ts = evaluation_timestamp or datetime.now()
        normalized_dd = self._normalize_pct(max_drawdown_pct)
        normalized_strategy_ret = self._normalize_pct(strategy_return_pct)
        normalized_benchmark_ret = self._normalize_pct(benchmark_return_pct)

        current_state = self._get_state(strategy_key)
        current_underperform = (
            current_state.consecutive_underperform_count if current_state else 0
        )

        # Update consecutive underperform count based on this period
        is_underperforming = self._is_underperforming(
            normalized_strategy_ret, normalized_benchmark_ret,
            self.autostop_config.underperform_margin_pct,
        )
        if is_underperforming:
            new_underperform_count = current_underperform + 1
        else:
            new_underperform_count = 0

        # Determine decision
        should_pause = False
        pause_reason: Optional[str] = None
        pause_metric_value: Optional[float] = None
        should_resume = False

        is_currently_paused = (
            current_state is not None and current_state.status == STATUS_PAUSED
        )

        if is_currently_paused:
            # Already paused — check for auto-resume if allowed
            if not self.autostop_config.manual_resume_required:
                should_resume = self._check_resume(
                    normalized_dd, new_underperform_count
                )
        else:
            # Currently active — check for pause triggers
            if (
                normalized_dd is not None
                and normalized_dd >= self.autostop_config.drawdown_threshold_pct
            ):
                should_pause = True
                pause_reason = PAUSE_REASON_DRAWDOWN
                pause_metric_value = normalized_dd
            elif (
                new_underperform_count
                >= self.autostop_config.underperform_periods_threshold
            ):
                should_pause = True
                pause_reason = PAUSE_REASON_UNDERPERFORM
                pause_metric_value = float(new_underperform_count)

        # Persist state changes
        notification_message: Optional[str] = None
        if should_pause:
            self._persist_pause(
                strategy_key=strategy_key,
                pause_reason=pause_reason or PAUSE_REASON_MANUAL,
                pause_metric_value=pause_metric_value,
                consecutive_underperform_count=new_underperform_count,
                drawdown_pct=normalized_dd,
                strategy_return_pct=normalized_strategy_ret,
                benchmark_return_pct=normalized_benchmark_ret,
                evaluated_at=ts,
            )
            notification_message = self._build_pause_notification(
                strategy_key=strategy_key,
                pause_reason=pause_reason or PAUSE_REASON_MANUAL,
                pause_metric_value=pause_metric_value,
                consecutive_underperform_count=new_underperform_count,
                drawdown_pct=normalized_dd,
                strategy_return_pct=normalized_strategy_ret,
                benchmark_return_pct=normalized_benchmark_ret,
            )
            self._dispatch_notification(notification_message)
            # P3：运行态暂停 → 注册表 dormant（打通 autostop 与 strategies.yaml，
            # 此前两套状态脱节）。失败不影响 autostop 主判决。
            self._sync_registry_dormant(strategy_key)
        elif should_resume:
            self._persist_resume(
                strategy_key=strategy_key,
                consecutive_underperform_count=new_underperform_count,
                drawdown_pct=normalized_dd,
                strategy_return_pct=normalized_strategy_ret,
                benchmark_return_pct=normalized_benchmark_ret,
                evaluated_at=ts,
            )
            notification_message = self._build_resume_notification(strategy_key)
            self._dispatch_notification(notification_message)
        else:
            # No transition — just update metrics
            self._persist_metrics_update(
                strategy_key=strategy_key,
                consecutive_underperform_count=new_underperform_count,
                drawdown_pct=normalized_dd,
                strategy_return_pct=normalized_strategy_ret,
                benchmark_return_pct=normalized_benchmark_ret,
                evaluated_at=ts,
            )

        return AutostopEvaluation(
            strategy_key=strategy_key,
            status=STATUS_PAUSED if should_pause else (
                STATUS_ACTIVE if should_resume else (
                    current_state.status if current_state else STATUS_ACTIVE
                )
            ),
            should_pause=should_pause,
            should_resume=should_resume,
            pause_reason=pause_reason if should_pause else None,
            pause_metric_value=pause_metric_value if should_pause else None,
            consecutive_underperform_count=new_underperform_count,
            last_drawdown_pct=normalized_dd,
            last_strategy_return_pct=normalized_strategy_ret,
            last_benchmark_return_pct=normalized_benchmark_ret,
            notification_message=notification_message,
        )

    def manual_pause(
        self,
        strategy_key: str,
        reason: str = PAUSE_REASON_MANUAL,
        note: Optional[str] = None,
    ) -> AutostopEvaluation:
        """Manually pause a strategy. Always emits a notification."""
        if not strategy_key or not strategy_key.strip():
            raise ValueError("strategy_key is required")

        ts = datetime.now()
        self._persist_pause(
            strategy_key=strategy_key,
            pause_reason=reason,
            pause_metric_value=None,
            consecutive_underperform_count=0,
            drawdown_pct=None,
            strategy_return_pct=None,
            benchmark_return_pct=None,
            evaluated_at=ts,
        )
        message = self._build_pause_notification(
            strategy_key=strategy_key,
            pause_reason=reason,
            pause_metric_value=None,
            consecutive_underperform_count=0,
            drawdown_pct=None,
            strategy_return_pct=None,
            benchmark_return_pct=None,
            extra_note=note,
        )
        self._dispatch_notification(message)
        return AutostopEvaluation(
            strategy_key=strategy_key,
            status=STATUS_PAUSED,
            should_pause=True,
            should_resume=False,
            pause_reason=reason,
            pause_metric_value=None,
            consecutive_underperform_count=0,
            last_drawdown_pct=None,
            last_strategy_return_pct=None,
            last_benchmark_return_pct=None,
            notification_message=message,
        )

    def manual_resume(self, strategy_key: str) -> AutostopEvaluation:
        """Manually resume a paused strategy. Always emits a notification."""
        if not strategy_key or not strategy_key.strip():
            raise ValueError("strategy_key is required")

        ts = datetime.now()
        self._persist_resume(
            strategy_key=strategy_key,
            consecutive_underperform_count=0,
            drawdown_pct=None,
            strategy_return_pct=None,
            benchmark_return_pct=None,
            evaluated_at=ts,
        )
        message = self._build_resume_notification(strategy_key)
        self._dispatch_notification(message)
        return AutostopEvaluation(
            strategy_key=strategy_key,
            status=STATUS_ACTIVE,
            should_pause=False,
            should_resume=True,
            pause_reason=None,
            pause_metric_value=None,
            consecutive_underperform_count=0,
            last_drawdown_pct=None,
            last_strategy_return_pct=None,
            last_benchmark_return_pct=None,
            notification_message=message,
        )

    def get_state(self, strategy_key: str) -> Optional[AutostopStateSnapshot]:
        """Read persisted state for a strategy."""
        return self._get_state(strategy_key)

    def list_states(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[AutostopStateSnapshot]:
        """List persisted autostop states, optionally filtered by status."""
        try:
            db = self._resolve_db_manager()
            with db.session_scope() as session:
                query = session.query(StrategyAutostopState)
                if status:
                    query = query.filter(StrategyAutostopState.status == status)
                rows = (
                    query.order_by(StrategyAutostopState.updated_at.desc())
                    .limit(max(1, min(int(limit), 500)))
                    .all()
                )
                return [self._row_to_snapshot(row) for row in rows]
        except SQLAlchemyError as exc:
            logger.warning(
                "[StrategyAutostop] list_states failed: %s", exc, exc_info=True
            )
            return []

    def list_paused_strategies(self, limit: int = 100) -> List[AutostopStateSnapshot]:
        """Convenience wrapper: list only paused strategies."""
        return self.list_states(status=STATUS_PAUSED, limit=limit)

    def is_paused(self, strategy_key: str) -> bool:
        """Check whether a strategy is currently paused."""
        state = self._get_state(strategy_key)
        return state is not None and state.status == STATUS_PAUSED

    # ── Internal helpers ──

    @staticmethod
    def _build_noop_evaluation(strategy_key: str) -> AutostopEvaluation:
        return AutostopEvaluation(
            strategy_key=strategy_key,
            status=STATUS_ACTIVE,
            should_pause=False,
            should_resume=False,
            pause_reason=None,
            pause_metric_value=None,
            consecutive_underperform_count=0,
            last_drawdown_pct=None,
            last_strategy_return_pct=None,
            last_benchmark_return_pct=None,
            notification_message=None,
        )

    @staticmethod
    def _normalize_pct(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_underperforming(
        strategy_return: Optional[float],
        benchmark_return: Optional[float],
        margin_pct: float = 0.0,
    ) -> bool:
        """A period counts as underperforming only when both returns are
        available and strategy trails benchmark by MORE than margin_pct
        (P3 批评#14：跑输幅度门——跑输 0.01% 不该累计触发降级)."""
        if strategy_return is None or benchmark_return is None:
            return False
        return (benchmark_return - strategy_return) > margin_pct

    def _sync_registry_dormant(self, strategy_key: str) -> None:
        """暂停时把注册表对应策略状态改为 dormant（跨模块状态打通）。"""
        try:
            from src.services.strategy_intake import (
                DEFAULT_REGISTRY_PATH, STATUS_DORMANT, set_registry_status)
            set_registry_status(strategy_key, STATUS_DORMANT,
                                 registry_path=DEFAULT_REGISTRY_PATH)
        except Exception as exc:  # noqa: BLE001 - 状态同步失败不影响主判决
            logger.warning("[StrategyAutostop] 注册表 dormant 同步失败 %s: %r",
                           strategy_key, exc)

    def _check_resume(
        self,
        current_drawdown_pct: Optional[float],
        consecutive_underperform_count: int,
    ) -> bool:
        """Check whether a paused strategy may auto-resume."""
        if current_drawdown_pct is not None:
            dd_recovered = current_drawdown_pct <= self.autostop_config.drawdown_recover_pct
        else:
            dd_recovered = True
        up_recovered = (
            consecutive_underperform_count
            <= self.autostop_config.underperform_recover_periods
        )
        return dd_recovered and up_recovered

    def _get_state(self, strategy_key: str) -> Optional[AutostopStateSnapshot]:
        try:
            db = self._resolve_db_manager()
            with db.session_scope() as session:
                row = (
                    session.query(StrategyAutostopState)
                    .filter(StrategyAutostopState.strategy_key == strategy_key)
                    .one_or_none()
                )
                if row is None:
                    return None
                return self._row_to_snapshot(row)
        except SQLAlchemyError as exc:
            logger.warning(
                "[StrategyAutostop] get_state failed for %s: %s",
                strategy_key,
                exc,
                exc_info=True,
            )
            return None

    @staticmethod
    def _row_to_snapshot(row: StrategyAutostopState) -> AutostopStateSnapshot:
        return AutostopStateSnapshot(
            strategy_key=row.strategy_key,
            status=row.status,
            paused_at=row.paused_at,
            pause_reason=row.pause_reason,
            pause_metric_value=row.pause_metric_value,
            consecutive_underperform_count=int(row.consecutive_underperform_count or 0),
            last_evaluated_at=row.last_evaluated_at,
            last_drawdown_pct=row.last_drawdown_pct,
            last_strategy_return_pct=row.last_strategy_return_pct,
            last_benchmark_return_pct=row.last_benchmark_return_pct,
        )

    def _persist_pause(
        self,
        *,
        strategy_key: str,
        pause_reason: str,
        pause_metric_value: Optional[float],
        consecutive_underperform_count: int,
        drawdown_pct: Optional[float],
        strategy_return_pct: Optional[float],
        benchmark_return_pct: Optional[float],
        evaluated_at: datetime,
    ) -> None:
        try:
            db = self._resolve_db_manager()
            with db.session_scope() as session:
                row = (
                    session.query(StrategyAutostopState)
                    .filter(StrategyAutostopState.strategy_key == strategy_key)
                    .one_or_none()
                )
                if row is None:
                    session.add(StrategyAutostopState(
                        strategy_key=strategy_key,
                        status=STATUS_PAUSED,
                        paused_at=evaluated_at,
                        pause_reason=pause_reason,
                        pause_metric_value=pause_metric_value,
                        consecutive_underperform_count=consecutive_underperform_count,
                        last_evaluated_at=evaluated_at,
                        last_drawdown_pct=drawdown_pct,
                        last_strategy_return_pct=strategy_return_pct,
                        last_benchmark_return_pct=benchmark_return_pct,
                    ))
                else:
                    row.status = STATUS_PAUSED
                    row.paused_at = evaluated_at
                    row.pause_reason = pause_reason
                    row.pause_metric_value = pause_metric_value
                    row.consecutive_underperform_count = consecutive_underperform_count
                    row.last_evaluated_at = evaluated_at
                    row.last_drawdown_pct = drawdown_pct
                    row.last_strategy_return_pct = strategy_return_pct
                    row.last_benchmark_return_pct = benchmark_return_pct
        except SQLAlchemyError as exc:
            logger.exception(
                "[StrategyAutostop] persist_pause failed for %s: %s",
                strategy_key,
                exc,
            )

    def _persist_resume(
        self,
        *,
        strategy_key: str,
        consecutive_underperform_count: int,
        drawdown_pct: Optional[float],
        strategy_return_pct: Optional[float],
        benchmark_return_pct: Optional[float],
        evaluated_at: datetime,
    ) -> None:
        try:
            db = self._resolve_db_manager()
            with db.session_scope() as session:
                row = (
                    session.query(StrategyAutostopState)
                    .filter(StrategyAutostopState.strategy_key == strategy_key)
                    .one_or_none()
                )
                if row is None:
                    session.add(StrategyAutostopState(
                        strategy_key=strategy_key,
                        status=STATUS_ACTIVE,
                        paused_at=None,
                        pause_reason=None,
                        pause_metric_value=None,
                        consecutive_underperform_count=consecutive_underperform_count,
                        last_evaluated_at=evaluated_at,
                        last_drawdown_pct=drawdown_pct,
                        last_strategy_return_pct=strategy_return_pct,
                        last_benchmark_return_pct=benchmark_return_pct,
                    ))
                else:
                    row.status = STATUS_ACTIVE
                    row.paused_at = None
                    row.pause_reason = None
                    row.pause_metric_value = None
                    row.consecutive_underperform_count = consecutive_underperform_count
                    row.last_evaluated_at = evaluated_at
                    row.last_drawdown_pct = drawdown_pct
                    row.last_strategy_return_pct = strategy_return_pct
                    row.last_benchmark_return_pct = benchmark_return_pct
        except SQLAlchemyError as exc:
            logger.exception(
                "[StrategyAutostop] persist_resume failed for %s: %s",
                strategy_key,
                exc,
            )

    def _persist_metrics_update(
        self,
        *,
        strategy_key: str,
        consecutive_underperform_count: int,
        drawdown_pct: Optional[float],
        strategy_return_pct: Optional[float],
        benchmark_return_pct: Optional[float],
        evaluated_at: datetime,
    ) -> None:
        try:
            db = self._resolve_db_manager()
            with db.session_scope() as session:
                row = (
                    session.query(StrategyAutostopState)
                    .filter(StrategyAutostopState.strategy_key == strategy_key)
                    .one_or_none()
                )
                if row is None:
                    session.add(StrategyAutostopState(
                        strategy_key=strategy_key,
                        status=STATUS_ACTIVE,
                        consecutive_underperform_count=consecutive_underperform_count,
                        last_evaluated_at=evaluated_at,
                        last_drawdown_pct=drawdown_pct,
                        last_strategy_return_pct=strategy_return_pct,
                        last_benchmark_return_pct=benchmark_return_pct,
                    ))
                else:
                    row.consecutive_underperform_count = consecutive_underperform_count
                    row.last_evaluated_at = evaluated_at
                    row.last_drawdown_pct = drawdown_pct
                    row.last_strategy_return_pct = strategy_return_pct
                    row.last_benchmark_return_pct = benchmark_return_pct
        except SQLAlchemyError as exc:
            logger.warning(
                "[StrategyAutostop] persist_metrics_update failed for %s: %s",
                strategy_key,
                exc,
                exc_info=True,
            )

    # ── Notification ──

    def _build_pause_notification(
        self,
        *,
        strategy_key: str,
        pause_reason: str,
        pause_metric_value: Optional[float],
        consecutive_underperform_count: int,
        drawdown_pct: Optional[float],
        strategy_return_pct: Optional[float],
        benchmark_return_pct: Optional[float],
        extra_note: Optional[str] = None,
    ) -> str:
        reason_label = {
            PAUSE_REASON_DRAWDOWN: "回撤超阈值",
            PAUSE_REASON_UNDERPERFORM: "连续跑输基准",
            PAUSE_REASON_MANUAL: "手动暂停",
        }.get(pause_reason, pause_reason)

        lines: List[str] = [
            f"⚠️ 策略自动停止告警",
            f"",
            f"策略：{strategy_key}",
            f"状态：active → **paused**",
            f"原因：{reason_label}",
        ]
        if pause_metric_value is not None:
            if pause_reason == PAUSE_REASON_DRAWDOWN:
                lines.append(f"回撤：{pause_metric_value:.2f}%（阈值 {self.autostop_config.drawdown_threshold_pct:.2f}%）")
            elif pause_reason == PAUSE_REASON_UNDERPERFORM:
                lines.append(
                    f"连续跑输期数：{int(pause_metric_value)}（阈值 {self.autostop_config.underperform_periods_threshold}）"
                )
        if drawdown_pct is not None:
            lines.append(f"当前回撤：{drawdown_pct:.2f}%")
        if strategy_return_pct is not None and benchmark_return_pct is not None:
            lines.append(
                f"本期收益：策略 {strategy_return_pct:.2f}% / 基准 {benchmark_return_pct:.2f}%"
            )
        if consecutive_underperform_count > 0:
            lines.append(f"连续跑输计数：{consecutive_underperform_count}")
        if extra_note:
            lines.append(f"备注：{extra_note}")
        lines.append("")
        lines.append(
            f"恢复方式：{'手动恢复' if self.autostop_config.manual_resume_required else '指标恢复后自动恢复'}"
        )
        return "\n".join(lines)

    def _build_resume_notification(self, strategy_key: str) -> str:
        return (
            f"✅ 策略恢复通知\n"
            f"\n"
            f"策略：{strategy_key}\n"
            f"状态：paused → **active**\n"
            f"原因：{'手动恢复' if self.autostop_config.manual_resume_required else '指标已恢复，自动恢复'}"
        )

    def _dispatch_notification(self, message: str) -> None:
        """Send notification via injected dispatcher or NotificationService."""
        if not message:
            return
        if self._notification_dispatcher is not None:
            try:
                self._notification_dispatcher(message)
                return
            except Exception as exc:  # pragma: no cover - notification best-effort
                logger.warning(
                    "[StrategyAutostop] custom notification dispatcher failed: %s",
                    exc,
                    exc_info=True,
                )
                return
        # Fallback to NotificationService — lazy import to avoid circular deps
        try:
            from src.notification import get_notification_service

            service = get_notification_service()
            service.send(message, route_type="strategy_autostop", severity="critical")
        except Exception as exc:  # pragma: no cover - notification best-effort
            logger.warning(
                "[StrategyAutostop] NotificationService dispatch failed: %s",
                exc,
                exc_info=True,
            )


def build_autostop_service_from_config(
    *,
    config: Optional[Config] = None,
    db_manager: Optional[DatabaseManager] = None,
    notification_dispatcher: Optional[Any] = None,
) -> StrategyAutostopService:
    """Build a StrategyAutostopService using app-level Config overrides."""
    return StrategyAutostopService(
        db_manager=db_manager,
        config=config,
        notification_dispatcher=notification_dispatcher,
    )

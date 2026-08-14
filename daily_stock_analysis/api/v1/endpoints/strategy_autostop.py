# -*- coding: utf-8 -*-
"""
===================================
策略自动停止接口（rule-based timing 模块）
===================================

职责：
1. 评估单个策略是否触发自动停止（回撤超阈值 / 连续跑输基准）
2. 查询/管理已持久化的策略暂停状态
3. 手动暂停 / 恢复策略

与 portfolio_risk_service 的协同：
- portfolio_risk_service.get_risk_report() 返回的组合级 drawdown 可作为
  autostop.evaluate() 的 max_drawdown_pct 输入；
- autostop 端点本身不直接依赖 portfolio_risk_service，调用方可自行组合两者。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.strategy_autostop import (
    AutostopEvaluateRequest,
    AutostopEvaluationResponse,
    AutostopStateItem,
    AutostopStateListResponse,
    ManualPauseRequest,
)
from src.services.strategy_autostop_service import (
    StrategyAutostopService,
    PAUSE_REASON_MANUAL,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_service(db_manager: DatabaseManager) -> StrategyAutostopService:
    return StrategyAutostopService(db_manager=db_manager)


def _bad_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "invalid_params", "message": message},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.exception("[StrategyAutostopAPI] %s", message, exc_info=exc)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": message},
    )


def _snapshot_to_item(snapshot) -> AutostopStateItem:
    return AutostopStateItem(
        strategy_key=snapshot.strategy_key,
        status=snapshot.status,
        paused_at=snapshot.paused_at,
        pause_reason=snapshot.pause_reason,
        pause_metric_value=snapshot.pause_metric_value,
        consecutive_underperform_count=snapshot.consecutive_underperform_count,
        last_evaluated_at=snapshot.last_evaluated_at,
        last_drawdown_pct=snapshot.last_drawdown_pct,
        last_strategy_return_pct=snapshot.last_strategy_return_pct,
        last_benchmark_return_pct=snapshot.last_benchmark_return_pct,
    )


@router.post(
    "/evaluate",
    response_model=AutostopEvaluationResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Evaluate strategy auto-stop (评估策略自动停止)",
)
def evaluate_strategy(
    request: AutostopEvaluateRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),  # noqa: B008
) -> AutostopEvaluationResponse:
    """评估单个策略是否需要触发自动停止。

    输入本期策略回撤、策略收益率、基准收益率，服务会：
    1. 更新连续跑输基准计数；
    2. 判断是否触发回撤超阈值 / 连续跑输暂停；
    3. 在 active → paused 状态变化时发送告警通知（去重）；
    4. 持久化状态，跨进程重启保留。
    """
    service = _build_service(db_manager)
    try:
        result = service.evaluate(
            strategy_key=request.strategy_key,
            max_drawdown_pct=request.max_drawdown_pct,
            strategy_return_pct=request.strategy_return_pct,
            benchmark_return_pct=request.benchmark_return_pct,
        )
    except ValueError as exc:
        raise _bad_request(str(exc))
    except Exception as exc:
        raise _internal_error("Evaluate strategy failed", exc)

    return AutostopEvaluationResponse(
        strategy_key=result.strategy_key,
        status=result.status,
        should_pause=result.should_pause,
        should_resume=result.should_resume,
        pause_reason=result.pause_reason,
        pause_metric_value=result.pause_metric_value,
        consecutive_underperform_count=result.consecutive_underperform_count,
        last_drawdown_pct=result.last_drawdown_pct,
        last_strategy_return_pct=result.last_strategy_return_pct,
        last_benchmark_return_pct=result.last_benchmark_return_pct,
        notification_message=result.notification_message,
    )


@router.get(
    "/states",
    response_model=AutostopStateListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List autostop states (查询策略自动停止状态列表)",
)
def list_states(
    status: Optional[str] = Query(
        None, description="按状态过滤：active / paused（默认全部）"
    ),
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
    db_manager: DatabaseManager = Depends(get_database_manager),  # noqa: B008
) -> AutostopStateListResponse:
    """查询所有策略的自动停止状态，可按 active/paused 过滤。"""
    service = _build_service(db_manager)
    try:
        snapshots = service.list_states(status=status, limit=limit)
    except Exception as exc:
        raise _internal_error("List autostop states failed", exc)

    items = [_snapshot_to_item(s) for s in snapshots]
    return AutostopStateListResponse(items=items, total=len(items))


@router.get(
    "/states/{strategy_key}",
    response_model=AutostopStateItem,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Get autostop state by strategy_key (查询单个策略状态)",
)
def get_state(
    strategy_key: str,
    db_manager: DatabaseManager = Depends(get_database_manager),  # noqa: B008
) -> AutostopStateItem:
    """查询单个策略的自动停止状态。"""
    service = _build_service(db_manager)
    try:
        snapshot = service.get_state(strategy_key)
    except Exception as exc:
        raise _internal_error("Get autostop state failed", exc)

    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"strategy_key '{strategy_key}' not found",
            },
        )
    return _snapshot_to_item(snapshot)


@router.post(
    "/states/{strategy_key}/pause",
    response_model=AutostopEvaluationResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Manually pause a strategy (手动暂停策略)",
)
def manual_pause(
    strategy_key: str,
    request: ManualPauseRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),  # noqa: B008
) -> AutostopEvaluationResponse:
    """手动暂停指定策略，并发送通知。"""
    service = _build_service(db_manager)
    try:
        result = service.manual_pause(
            strategy_key=strategy_key,
            reason=request.reason or PAUSE_REASON_MANUAL,
            note=request.note,
        )
    except ValueError as exc:
        raise _bad_request(str(exc))
    except Exception as exc:
        raise _internal_error("Manual pause failed", exc)

    return AutostopEvaluationResponse(
        strategy_key=result.strategy_key,
        status=result.status,
        should_pause=result.should_pause,
        should_resume=result.should_resume,
        pause_reason=result.pause_reason,
        pause_metric_value=result.pause_metric_value,
        consecutive_underperform_count=result.consecutive_underperform_count,
        last_drawdown_pct=result.last_drawdown_pct,
        last_strategy_return_pct=result.last_strategy_return_pct,
        last_benchmark_return_pct=result.last_benchmark_return_pct,
        notification_message=result.notification_message,
    )


@router.post(
    "/states/{strategy_key}/resume",
    response_model=AutostopEvaluationResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Manually resume a strategy (手动恢复策略)",
)
def manual_resume(
    strategy_key: str,
    db_manager: DatabaseManager = Depends(get_database_manager),  # noqa: B008
) -> AutostopEvaluationResponse:
    """手动恢复指定策略，并发送通知。"""
    service = _build_service(db_manager)
    try:
        result = service.manual_resume(strategy_key=strategy_key)
    except ValueError as exc:
        raise _bad_request(str(exc))
    except Exception as exc:
        raise _internal_error("Manual resume failed", exc)

    return AutostopEvaluationResponse(
        strategy_key=result.strategy_key,
        status=result.status,
        should_pause=result.should_pause,
        should_resume=result.should_resume,
        pause_reason=result.pause_reason,
        pause_metric_value=result.pause_metric_value,
        consecutive_underperform_count=result.consecutive_underperform_count,
        last_drawdown_pct=result.last_drawdown_pct,
        last_strategy_return_pct=result.last_strategy_return_pct,
        last_benchmark_return_pct=result.last_benchmark_return_pct,
        notification_message=result.notification_message,
    )

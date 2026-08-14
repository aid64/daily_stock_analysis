# -*- coding: utf-8 -*-
"""Schemas for the strategy auto-stop API (rule-based timing module)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AutostopEvaluateRequest(BaseModel):
    """Request body for evaluating a strategy against autostop rules."""

    strategy_key: str = Field(..., description="策略唯一标识", min_length=1, max_length=128)
    max_drawdown_pct: Optional[float] = Field(
        None, description="当前最大回撤百分比", ge=0.0
    )
    strategy_return_pct: Optional[float] = Field(
        None, description="本期策略收益率百分比"
    )
    benchmark_return_pct: Optional[float] = Field(
        None, description="本期基准收益率百分比"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "strategy_key": "momentum_quality_v1",
                "max_drawdown_pct": 22.5,
                "strategy_return_pct": -1.2,
                "benchmark_return_pct": 0.8,
            }
        }
    )


class AutostopEvaluationResponse(BaseModel):
    """Response describing the autostop decision for one strategy."""

    strategy_key: str = Field(..., description="策略唯一标识")
    status: str = Field(..., description="当前状态：active / paused")
    should_pause: bool = Field(..., description="本次评估是否触发暂停")
    should_resume: bool = Field(..., description="本次评估是否触发恢复")
    pause_reason: Optional[str] = Field(
        None, description="暂停原因：drawdown / underperform / manual"
    )
    pause_metric_value: Optional[float] = Field(
        None, description="触发暂停的指标值（回撤百分比或连续跑输期数）"
    )
    consecutive_underperform_count: int = Field(
        0, description="连续跑输基准期数"
    )
    last_drawdown_pct: Optional[float] = Field(None, description="最近一次回撤百分比")
    last_strategy_return_pct: Optional[float] = Field(
        None, description="最近一次策略收益率百分比"
    )
    last_benchmark_return_pct: Optional[float] = Field(
        None, description="最近一次基准收益率百分比"
    )
    notification_message: Optional[str] = Field(
        None, description="本次触发的告警/恢复通知内容（无状态变化时为 null）"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "strategy_key": "momentum_quality_v1",
                "status": "paused",
                "should_pause": True,
                "should_resume": False,
                "pause_reason": "drawdown",
                "pause_metric_value": 22.5,
                "consecutive_underperform_count": 3,
                "last_drawdown_pct": 22.5,
                "last_strategy_return_pct": -1.2,
                "last_benchmark_return_pct": 0.8,
                "notification_message": "⚠️ 策略自动停止告警...",
            }
        }
    )


class AutostopStateItem(BaseModel):
    """Snapshot of one strategy's persisted autostop state."""

    strategy_key: str
    status: str
    paused_at: Optional[datetime] = None
    pause_reason: Optional[str] = None
    pause_metric_value: Optional[float] = None
    consecutive_underperform_count: int = 0
    last_evaluated_at: Optional[datetime] = None
    last_drawdown_pct: Optional[float] = None
    last_strategy_return_pct: Optional[float] = None
    last_benchmark_return_pct: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class AutostopStateListResponse(BaseModel):
    """Paginated list of autostop states."""

    items: List[AutostopStateItem] = Field(default_factory=list)
    total: int = Field(0, description="返回条数")


class ManualPauseRequest(BaseModel):
    """Request body for manually pausing a strategy."""

    reason: Optional[str] = Field(
        None, description="自定义暂停原因（默认 manual）", max_length=64
    )
    note: Optional[str] = Field(None, description="备注", max_length=512)


class ManualResumeRequest(BaseModel):
    """Empty request body for manually resuming a strategy (kept for future
    extension)."""

    pass

"""Pydantic Schema：所有运行参数的类型校验

设计原则：
1. 单一真源：所有参数集中在 config/*.yaml，代码不写死
2. 启动即失败：Pydantic 校验配置合法性，避免运行中崩溃
3. 类型严格：Optional 显式标注，枚举值限定取值范围
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DataSource(str, Enum):
    """支持的数据源"""

    TUSHARE = "tushare"
    BAOSTOCK = "baostock"
    AKSHARE = "akshare"


class AdjustMethod(str, Enum):
    """复权方式
    - hfq: 后复权（推荐，避免未来函数）
    - qfq: 前复权
    - none: 不复权
    """

    HFQ = "hfq"
    QFQ = "qfq"
    NONE = "none"


class CacheCompression(str, Enum):
    SNAPPY = "snappy"
    ZSTD = "zstd"
    GZIP = "gzip"


class TushareConfig(BaseModel):
    """Tushare Pro API 配置"""

    token: str = Field(default="", description="从 .env 的 TUSHARE_TOKEN 注入")
    points: int = Field(default=15000, ge=2000, description="积分权限")
    rate_limit_per_min: int = Field(default=200, ge=1, le=600, description="每分钟调用上限")
    retry_max: int = Field(default=3, ge=0, le=10, description="最大重试次数")
    retry_backoff: float = Field(default=1.5, ge=1.0, le=5.0, description="指数退避底数")

    @field_validator("token")
    @classmethod
    def _validate_token(cls, v: str) -> str:
        # token 可以为空（fallback 到 baostock/akshare），但需警告
        return v.strip()


class DataConfig(BaseModel):
    """数据层配置"""

    primary: DataSource = DataSource.TUSHARE
    fallbacks: list[DataSource] = Field(
        default_factory=lambda: [DataSource.BAOSTOCK, DataSource.AKSHARE]
    )
    cache_dir: str = Field(default="data/cache", description="Parquet 缓存根目录")
    cache_format: str = Field(default="parqueue")
    adjust: AdjustMethod = AdjustMethod.HFQ
    cache_compression: CacheCompression = CacheCompression.ZSTD

    @field_validator("cache_format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        if v != "parquet":
            raise ValueError(f"cache_format 仅支持 parquet, got {v}")
        return "parquet"

    @field_validator("fallbacks")
    @classmethod
    def _validate_fallbacks(cls, v: list[DataSource]) -> list[DataSource]:
        if not v:
            raise ValueError("fallbacks 不能为空，至少需要一个备选数据源")
        return v


class StrategyConfig(BaseModel):
    """策略配置（P1 启用）"""

    name: str = "etf_momentum"
    params: dict = Field(default_factory=dict)
    rebalance_freq: str = Field(default="W", pattern="^[WMwD]$")  # W 周 / M 月 / D 日
    max_positions: int = Field(default=5, ge=1, le=50)
    universe: Optional[list[str]] = None
    universe_source: Optional[str] = None  # dynamic 表示动态生成


class BacktestConfig(BaseModel):
    """回测配置（P2 启用）"""

    start: str = "2015-01-01"
    end: str = "2024-12-31"
    initial_capital: float = Field(default=10000.0, gt=0)
    commission_rate: float = Field(default=0.0001, ge=0, le=0.01)
    stamp_duty: float = Field(default=0.0005, ge=0, le=0.01)
    transfer_fee: float = Field(default=0.00002, ge=0, le=0.001)
    min_commission: float = Field(default=5.0, ge=0)
    slippage: float = Field(default=0.001, ge=0, le=0.05)
    allow_t_plus_1: bool = True
    respect_limit_up_down: bool = True


class RiskConfig(BaseModel):
    """风控配置（P2 启用）"""

    max_single_position: float = Field(default=0.3, gt=0, le=1.0)
    max_drawdown_circuit_breaker: float = Field(default=0.15, gt=0, le=1.0)
    stop_loss_atr_mult: float = Field(default=2.0, gt=0)
    three_tier_ratios: dict = Field(
        default_factory=lambda: {"base": 0.5, "tactical": 0.3, "cash": 0.2}
    )

    @field_validator("three_tier_ratios")
    @classmethod
    def _validate_ratios(cls, v: dict) -> dict:
        total = sum(v.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"三层仓位比例之和必须为 1.0, got {total}")
        return v


class NotifyConfig(BaseModel):
    """通知配置（P5 启用）"""

    enabled: bool = False
    bot_api: str = "http://localhost:8000"


class LoggingConfig(BaseModel):
    """日志配置"""

    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class AppConfig(BaseModel):
    """应用总配置"""

    data: DataConfig
    tushare: TushareConfig
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

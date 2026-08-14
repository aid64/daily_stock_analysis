"""配置层入口

用法：
    from quant_research.config import load_config, AppConfig
    cfg = load_config("config/default.yaml")
"""

from .schema import (
    AppConfig,
    DataConfig,
    TushareConfig,
    StrategyConfig,
    BacktestConfig,
    RiskConfig,
    NotifyConfig,
    LoggingConfig,
)
from .loader import load_config, get_default_config_path

__all__ = [
    "AppConfig",
    "DataConfig",
    "TushareConfig",
    "StrategyConfig",
    "BacktestConfig",
    "RiskConfig",
    "NotifyConfig",
    "LoggingConfig",
    "load_config",
    "get_default_config_path",
]

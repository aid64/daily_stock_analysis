"""回测层入口

用法：
    from quant_research.backtest import BacktestEngine, AShareCommission
"""
from .broker import AShareCommission, TPlus1Broker, LimitUpDownFilter
from .engine import BacktestEngine, BacktestResult
from .strategies import SMACross, ETFMomentum
from .mwu import (
    ETFMWU,
    MomentumExpert,
    MeanReversionExpert,
    DefensiveExpert,
    build_experts,
)
from .convertible import (
    ConvertibleDoubleLow,
    ConvertibleDoubleLowExpert,
    PITConvertibleDoubleLowExpert,
    compute_double_low_scores,
    load_pit_snapshot,
    validate_pit_snapshot,
    snapshot_picks,
    normalize_cb_symbol,
)

__all__ = [
    "AShareCommission",
    "TPlus1Broker",
    "LimitUpDownFilter",
    "BacktestEngine",
    "BacktestResult",
    "SMACross",
    "ETFMomentum",
    "ETFMWU",
    "MomentumExpert",
    "MeanReversionExpert",
    "DefensiveExpert",
    "build_experts",
    "ConvertibleDoubleLow",
    "ConvertibleDoubleLowExpert",
    "PITConvertibleDoubleLowExpert",
    "compute_double_low_scores",
    "load_pit_snapshot",
    "validate_pit_snapshot",
    "snapshot_picks",
    "normalize_cb_symbol",
]

"""核心域层：与回测/实盘运行时无关的纯逻辑

- market：市场数据视图协议（MarketView/BarSeries）与数组实现
- mwu_core：MWU 权重融合核（回测 ETFMWU 与实盘 LiveMWU 共用）
- rules：A 股交易规则表（品种 × 日期 → 费率/涨跌停/T+1）

重构 R1/R2（2026-08-29）：协议真源与规则真源从 backtrader 侧/
散落常量收拢到本包，回测侧与实盘侧各自实现/查询。
"""
from .market import ArrayMarketView, ArraySeries, BarSeries, MarketView
from .mwu_core import MWUCore
from . import rules
from .rules import AssetRules, asset_type, get_limit_pct, get_rules

__all__ = [
    "ArrayMarketView",
    "ArraySeries",
    "BarSeries",
    "MarketView",
    "MWUCore",
    "rules",
    "AssetRules",
    "asset_type",
    "get_limit_pct",
    "get_rules",
]

"""核心域层：与回测/实盘运行时无关的纯逻辑

- market：市场数据视图协议（MarketView/BarSeries）与数组实现
- mwu_core：MWU 权重融合核（回测 ETFMWU 与实盘 LiveMWU 共用）

重构 R1（2026-08-29）：协议真源从 backtrader line 移到本包，
回测侧与实盘侧各自实现协议（此前是实盘侧反向模仿 backtrader line，
协议真源落在第三方库上，权重更新算法在两处手抄易漂移）。
"""
from .market import ArrayMarketView, ArraySeries, BarSeries, MarketView
from .mwu_core import MWUCore

__all__ = [
    "ArrayMarketView",
    "ArraySeries",
    "BarSeries",
    "MarketView",
    "MWUCore",
]

"""执行层（P4）：券商接入 + 离散优化下单 + 订单执行引擎

方法论映射（栀染《谈量化交易中被严重低估的执行模块》）：
- 策略融合 MWU → quant_research/backtest/mwu.py（P3 已完成）
- 离散优化（整数手）→ trade/sizer.py（本文要点二）
- 拆单 → trade/executor.py 的 OrderSplitter（资金放大后启用）
- 执行模块是"被严重低估的 1%"：策略信号再好，执行坏了也白搭。
  本层负责把 MWU 目标组合真正变成券商账户里的持仓。
"""

from .broker import AbstractBroker, PaperBroker, TDXJKBroker, BrokerError, Order, Side, OrderStatus
from .sizer import integer_lot_allocate, Target, Position
from .executor import Executor, ExecutorConfig, TradeAction, MarketClock

__all__ = [
    "AbstractBroker",
    "PaperBroker",
    "TDXJKBroker",
    "BrokerError",
    "Order",
    "Side",
    "OrderStatus",
    "integer_lot_allocate",
    "Target",
    "Position",
    "Executor",
    "ExecutorConfig",
    "TradeAction",
    "MarketClock",
]

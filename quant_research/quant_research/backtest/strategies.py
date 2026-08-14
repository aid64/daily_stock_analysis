"""示例策略：SMA 双均线交叉

用于验证回测引擎是否正常工作。
P1 阶段会实现 ETF 动量轮动和可转债双低轮动。
"""

from __future__ import annotations

import backtrader as bt


class SMACross(bt.Strategy):
    """SMA 双均线交叉策略

    - 短期均线上穿长期均线 → 买入
    - 短期均线下穿长期均线 → 卖出
    """

    params = (
        ("fast_period", 5),
        ("slow_period", 20),
    )

    def __init__(self):
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast_period)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
        self.order = None

    def next(self):
        if self.order:
            return

        if not self.position:
            if self.crossover > 0:  # 金叉买入
                self.order = self.buy()
        else:
            if self.crossover < 0:  # 死叉卖出
                self.order = self.sell()

    def notify_order(self, order):
        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.order = None

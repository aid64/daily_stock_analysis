"""回测侧 MarketView 适配器

把 backtrader data lines 包装成 core.market 协议（重构 R1：适配方向
反转——协议真源在 core.market，backtrader 侧去实现协议，而不是像
此前实盘侧 PriceSeries 那样反向模仿 backtrader line）。

不 import backtrader：data lines 按 duck-typing 消费
（`_name` / `len()` / `close.get(size=n)` / `amount.get(size=n)` /
`close[0]` / `datetime.date(0)`）。
"""

from __future__ import annotations

import datetime

import numpy as np

from ..core.market import BarSeries


class BTBarSeries:
    """单标的 backtrader data line 适配"""

    __slots__ = ("name", "_data")

    def __init__(self, data):
        self.name = data._name
        self._data = data

    def __len__(self) -> int:
        return len(self._data)

    def closes(self, n: int) -> np.ndarray:
        return np.asarray(self._data.close.get(size=max(1, int(n))), dtype=float)

    def amounts(self, n: int) -> np.ndarray:
        return np.asarray(self._data.amount.get(size=max(1, int(n))), dtype=float)

    def last_close(self) -> float:
        return float(self._data.close[0])


class BTMarketView:
    """backtrader 多标的视图（ experts select() 的回测侧入口）"""

    def __init__(self, datas):
        self._datas = list(datas)
        self._series = [BTBarSeries(d) for d in self._datas]
        self._by_name = {s.name: s for s in self._series}

    def current_date(self) -> datetime.date | None:
        if not self._datas:
            return None
        return self._datas[0].datetime.date(0)

    def series(self, name: str) -> BarSeries | None:
        return self._by_name.get(name)

    def all_series(self) -> list[BTBarSeries]:
        return list(self._series)

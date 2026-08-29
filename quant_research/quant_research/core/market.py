"""市场数据视图协议（专家的数据访问抽象）

专家（Expert）只消费 MarketView，不感知 backtrader line 或实盘行情源。
两侧实现同一时间语义约定：

- closes(n) / amounts(n)：最近 n 根、时间升序（[-1] 为当前 bar），
  数据不足时返回实际可得的行数（与 backtrader line.get(size=n) 一致）
- last_close()：当前 bar 收盘价（可能为 NaN，调用方自行做有限性检查）
- current_date()：当前调仓日（PIT 专家按日期取历史快照；实盘为当天）

backtrader 侧适配见 backtest/adapter.py；实盘/测试侧用 ArrayMarketView。
"""
from __future__ import annotations

import datetime
from typing import Mapping, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class BarSeries(Protocol):
    """单标的行情视图"""

    name: str

    def __len__(self) -> int: ...

    def closes(self, n: int) -> np.ndarray: ...

    def amounts(self, n: int) -> np.ndarray: ...

    def last_close(self) -> float: ...


@runtime_checkable
class MarketView(Protocol):
    """全市场行情视图（专家 select() 的唯一输入）"""

    def current_date(self) -> datetime.date | None: ...

    def series(self, name: str) -> BarSeries | None: ...

    def all_series(self) -> list[BarSeries]: ...


class ArraySeries:
    """数组行情序列（实盘/测试侧实现）

    参数 amount 为标量时按常数成交额填充（与原 PriceSeries 一致，
    供无成交额数据的场景做流动性过滤豁免）。
    """

    def __init__(
        self,
        name: str,
        closes: np.ndarray,
        amount: float | np.ndarray = 1e8,
    ):
        self.name = name
        self._closes = np.asarray(closes, dtype=float)
        if isinstance(amount, (int, float)):
            self._amount = np.full(len(self._closes), float(amount))
        else:
            self._amount = np.asarray(amount, dtype=float)

    def __len__(self) -> int:
        return len(self._closes)

    def closes(self, n: int) -> np.ndarray:
        n = max(1, int(n))
        return self._closes[-n:]

    def amounts(self, n: int) -> np.ndarray:
        n = max(1, int(n))
        return self._amount[-n:]

    def last_close(self) -> float:
        return float(self._closes[-1])


class ArrayMarketView:
    """由 {symbol: (closes, amounts)} 构建的市场视图

    标的遍历顺序与输入 dict 插入顺序一致（动量排序等依赖稳定顺序）。
    """

    def __init__(
        self,
        prices: Mapping[str, tuple[np.ndarray, float | np.ndarray]],
        current_date: datetime.date | None = None,
    ):
        self._series = [
            ArraySeries(sym, closes, amount) for sym, (closes, amount) in prices.items()
        ]
        self._by_name = {s.name: s for s in self._series}
        self._current_date = current_date

    def current_date(self) -> datetime.date | None:
        return self._current_date

    def series(self, name: str) -> ArraySeries | None:
        return self._by_name.get(name)

    def all_series(self) -> list[ArraySeries]:
        return list(self._series)

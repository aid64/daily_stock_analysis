"""数据源 Client 抽象基类

所有 Client 必须实现本接口，确保 Loader 可以无缝 fallback。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from threading import Lock
from typing import Optional

import pandas as pd


class DataClientError(Exception):
    """数据源 Client 异常基类"""


class RateLimitError(DataClientError):
    """限流异常（可重试）"""


class AuthError(DataClientError):
    """认证异常（不可重试，应 fallback）"""


class AbstractDataClient(ABC):
    """统一数据源接口

    所有实现必须线程安全（Loader 可能在并发场景调用）。
    所有方法返回的 DataFrame 必须经过 normalize_ohlc 标准化（Loader 层会强制调用）。
    """

    name: str = "abstract"

    def __init__(self, rate_limit_per_min: int = 200, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(self.name)
        self._rate_limit_per_min = max(1, rate_limit_per_min)
        self._min_interval = 60.0 / self._rate_limit_per_min
        self._last_call_ts = 0.0
        self._lock = Lock()

    def _throttle(self) -> None:
        """流控：保证两次调用间隔 >= self._min_interval"""
        with self._lock:
            elapsed = time.time() - self._last_call_ts
            if elapsed < self._min_interval:
                sleep_sec = self._min_interval - elapsed
                self.logger.debug(f"[{self.name}] throttle sleep {sleep_sec:.3f}s")
                time.sleep(sleep_sec)
            self._last_call_ts = time.time()

    @abstractmethod
    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        """日 K 线

        Args:
            symbol: 标的代码（统一格式 600519.SH / 510300.SH / 159915.SZ）
            start: 起始日期 YYYY-MM-DD
            end: 结束日期 YYYY-MM-DD
            adjust: 复权方式 hfq / qfq / none

        Returns:
            DataFrame，列：date, open, high, low, close, volume, amount
            date 列为 datetime64[ns]；其余为 float64
            按 date 升序排列

        Raises:
            DataClientError: 数据获取失败
        """

    @abstractmethod
    def get_stock_basic(self) -> pd.DataFrame:
        """股票基础信息

        Returns:
            DataFrame，列：symbol, name, list_date, delist_date, exchange
            - symbol: 标的代码
            - name: 名称
            - list_date: 上市日期 datetime64[ns]
            - delist_date: 退市日期，未退市为 NaT
            - exchange: 交易所 SSE / SZSE / BSE
        """

    @abstractmethod
    def get_trade_calendar(self, start: str, end: str) -> list[str]:
        """交易日历

        Returns:
            日期字符串列表，格式 YYYY-MM-DD
        """

    @abstractmethod
    def get_index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        """指数日线

        Args:
            code: 指数代码，如 000300.SH（沪深 300）
        """

    def get_etf_daily(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        """ETF 日线（默认实现 fallback 到 get_daily_bars）

        子类可覆写以使用专用 ETF 接口（如 Tushare fund_daily）。
        """
        return self.get_daily_bars(symbol, start, end, adjust)

    def get_convertible_bond_daily(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """可转债日线（默认实现 fallback 到 get_daily_bars）

        子类可覆写以使用专用可转债接口（如 AKShare bond_zh_hs_cov_daily）。
        """
        return self.get_daily_bars(symbol, start, end, adjust="none")

    def get_convertible_bond_list(self) -> pd.DataFrame:
        """可转债基础信息列表（含信用评级、强赎状态等）

        默认返回空 DataFrame，子类按需覆写。
        """
        return pd.DataFrame()

    def close(self) -> None:
        """释放资源（如 baostock 的 login session）"""
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

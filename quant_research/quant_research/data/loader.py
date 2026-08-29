"""统一数据加载入口（DataLoader）

特性：
1. 多源 fallback：按 chain 顺序调用，失败切换到下一个数据源
2. 缓存优先：先查 Parquet 缓存，命中则直接返回；未命中才调用 client
3. 统一标准化：所有返回数据经过 normalize_ohlc 处理
4. 上下文管理：自动管理 client 生命周期
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from ..config.schema import AppConfig, DataSource
from .clients.base import AbstractDataClient, DataClientError
from .clients.tushare_client import TushareClient
from .clients.baostock_client import BaostockClient
from .clients.akshare_client import AkshareClient
from .cache import ParquetCache
from .universe import UniverseBuilder
from .normalizer import normalize_ohlc, normalize_stock_basic


def build_client_chain(config: AppConfig) -> list[AbstractDataClient]:
    """根据配置构建数据源 chain（主源 + fallbacks）"""
    chain: list[AbstractDataClient] = []
    seen = set()

    order = [config.data.primary] + list(config.data.fallbacks)
    for src in order:
        if src in seen:
            continue
        seen.add(src)

        try:
            if src == DataSource.TUSHARE:
                if not config.tushare.token:
                    logging.getLogger("data").warning(
                        "Tushare token 为空，跳过 Tushare 数据源"
                    )
                    continue
                chain.append(TushareClient(
                    token=config.tushare.token,
                    rate_limit_per_min=config.tushare.rate_limit_per_min,
                    retry_max=config.tushare.retry_max,
                    retry_backoff=config.tushare.retry_backoff,
                ))
            elif src == DataSource.BAOSTOCK:
                chain.append(BaostockClient())
            elif src == DataSource.AKSHARE:
                chain.append(AkshareClient())
        except DataClientError as e:
            logging.getLogger("data").warning(f"初始化 {src.value} 失败，跳过: {e}")
            continue

    if not chain:
        raise DataClientError("没有可用的数据源，请检查配置和 .env")
    return chain


class DataLoader:
    """统一数据加载器

    用法：
        cfg = load_config()
        loader = DataLoader(cfg)
        df = loader.get_daily_bars("510300.SH", "2015-01-01", "2024-12-31")
        loader.close()
    """

    def __init__(self, config: AppConfig, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or logging.getLogger("data_loader")
        self.chain = build_client_chain(config)
        self.cache = ParquetCache(
            root=config.data.cache_dir,
            compression=config.data.cache_compression.value,
            logger=self.logger,
        )
        self._stock_basic_cache: Optional[pd.DataFrame] = None
        self._universe_builder: Optional[UniverseBuilder] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self) -> None:
        for client in self.chain:
            try:
                client.close()
            except Exception as e:
                self.logger.warning(f"关闭 {client.name} 失败: {e}")

    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: Optional[str] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取日 K 线（缓存优先，多源 fallback）

        Args:
            symbol: 标的代码 600519.SH / 510300.SH
            start: 起始日期 YYYY-MM-DD
            end: 结束日期 YYYY-MM-DD
            adjust: 复权方式 hfq/qfq/none；None 用配置默认值
            use_cache: 是否启用缓存

        Returns:
            标准 OHLC DataFrame
        """
        adj = adjust or self.config.data.adjust.value

        # 1. 缓存优先
        if use_cache:
            cached = self.cache.get_bars(symbol, start, end, adj)
            if cached is not None and len(cached) > 0:
                self.logger.debug(f"缓存命中 {symbol} [{start}~{end}]: {len(cached)} 行")
                return cached

        # 2. 按 chain 顺序 fallback
        last_err: Optional[Exception] = None
        for client in self.chain:
            try:
                df = client.get_daily_bars(symbol, start, end, adj)
                if df is not None and len(df) > 0:
                    df = normalize_ohlc(df)
                    if use_cache:
                        self.cache.put_bars(symbol, df, adj)
                    self.logger.info(
                        f"[{client.name}] {symbol} [{start}~{end}]: {len(df)} 行"
                    )
                    return df
            except DataClientError as e:
                self.logger.warning(f"[{client.name}] {symbol} 失败: {e}")
                last_err = e
                continue
            except Exception as e:
                self.logger.error(f"[{client.name}] {symbol} 异常: {e}", exc_info=True)
                last_err = e
                continue

        if last_err:
            raise DataClientError(
                f"所有数据源均失败 {symbol} [{start}~{end}]: {last_err}"
            ) from last_err
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

    def get_stock_basic(self, use_cache: bool = True) -> pd.DataFrame:
        """获取股票基础信息（含已退市股）"""
        if self._stock_basic_cache is not None:
            return self._stock_basic_cache

        if use_cache:
            cached = self.cache.get_stock_basic()
            if cached is not None and len(cached) > 0:
                self._stock_basic_cache = cached
                return cached

        last_err: Optional[Exception] = None
        for client in self.chain:
            try:
                df = client.get_stock_basic()
                if df is not None and len(df) > 0:
                    df = normalize_stock_basic(df)
                    if use_cache:
                        self.cache.put_stock_basic(df)
                    self._stock_basic_cache = df
                    self.logger.info(f"[{client.name}] stock_basic: {len(df)} 行")
                    return df
            except DataClientError as e:
                self.logger.warning(f"[{client.name}] stock_basic 失败: {e}")
                last_err = e
                continue

        if last_err:
            raise DataClientError(f"所有数据源均失败 stock_basic: {last_err}") from last_err
        return pd.DataFrame(columns=["symbol", "name", "list_date", "delist_date", "exchange"])

    def get_trade_calendar(self, start: str, end: str) -> list[str]:
        """获取交易日历"""
        for client in self.chain:
            try:
                dates = client.get_trade_calendar(start, end)
                if dates:
                    self.logger.info(f"[{client.name}] trade_cal [{start}~{end}]: {len(dates)} 日")
                    return dates
            except DataClientError as e:
                self.logger.warning(f"[{client.name}] trade_cal 失败: {e}")
                continue
        return []

    def get_index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        """获取指数日线（不缓存，因指数数据量小）"""
        for client in self.chain:
            try:
                df = client.get_index_daily(code, start, end)
                if df is not None and len(df) > 0:
                    self.logger.info(f"[{client.name}] index {code} [{start}~{end}]: {len(df)} 行")
                    return normalize_ohlc(df)
            except DataClientError as e:
                self.logger.warning(f"[{client.name}] index {code} 失败: {e}")
                continue
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

    def get_universe_builder(self) -> UniverseBuilder:
        """获取 UniverseBuilder 实例（懒加载）"""
        if self._universe_builder is None:
            basic = self.get_stock_basic()
            self._universe_builder = UniverseBuilder(basic, logger=self.logger)
        return self._universe_builder

    def _etf_chain(self) -> list[AbstractDataClient]:
        """ETF 专用数据源顺序

        tushare 的 ETF 日线不处理份额分拆/折算（2026-08-18 实证：512480/
        510500/512010/512100/512890 在分拆日产生 ±50%~300% 假跳变），
        因此 AKShare 排第一：东财源带完整复权；东财断连时自动降级新浪源
        （经 adjust_etf_share_split 修正分拆，仅缺现金分红/计息）。
        Baostock 仅覆盖近期，兜底。顺序：akshare > tushare > baostock。
        """
        prio = {"akshare": 0, "tushare": 1, "baostock": 2}
        return sorted(self.chain, key=lambda c: prio.get(c.name, 9))

    def get_etf_daily(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: Optional[str] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取 ETF 日线（多源 fallback，缓存优先）

        对 ETF 标的优先调用各 Client 的 get_etf_daily 专用接口。
        """
        adj = adjust or self.config.data.adjust.value

        if use_cache:
            cached = self.cache.get_bars(symbol, start, end, adj)
            if cached is not None and len(cached) > 0:
                self.logger.debug(f"ETF 缓存命中 {symbol}: {len(cached)} 行")
                return cached

        last_err: Optional[Exception] = None
        for client in self._etf_chain():
            try:
                df = client.get_etf_daily(symbol, start, end, adj)
                if df is not None and len(df) > 0:
                    df = normalize_ohlc(df)
                    if use_cache:
                        self.cache.put_bars(symbol, df, adj)
                    self.logger.info(
                        f"[{client.name}] ETF {symbol} [{start}~{end}]: {len(df)} 行"
                    )
                    return df
            except DataClientError as e:
                self.logger.warning(f"[{client.name}] ETF {symbol} 失败: {e}")
                last_err = e
                continue

        if last_err:
            raise DataClientError(
                f"所有数据源均失败 ETF {symbol}: {last_err}"
            ) from last_err
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

    def get_convertible_bond_daily(
        self,
        symbol: str,
        start: str,
        end: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取可转债日线（多源 fallback）"""
        adj = "none"  # 可转债不复权

        if use_cache:
            cached = self.cache.get_bars(symbol, start, end, adj)
            if cached is not None and len(cached) > 0:
                return cached

        last_err: Optional[Exception] = None
        for client in self.chain:
            try:
                df = client.get_convertible_bond_daily(symbol, start, end)
                if df is not None and len(df) > 0:
                    df = normalize_ohlc(df)
                    if use_cache:
                        self.cache.put_bars(symbol, df, adj)
                    self.logger.info(
                        f"[{client.name}] CB {symbol} [{start}~{end}]: {len(df)} 行"
                    )
                    return df
            except DataClientError as e:
                self.logger.warning(f"[{client.name}] CB {symbol} 失败: {e}")
                last_err = e
                continue

        if last_err:
            raise DataClientError(
                f"所有数据源均失败 CB {symbol}: {last_err}"
            ) from last_err
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

    def get_convertible_bond_list(self) -> pd.DataFrame:
        """获取可转债列表（双低策略用，需含价格/溢价率/评级）

        可转债双低评分必须要有 price / premium_rate / rating，只有
        AKShare bond_zh_cov 的当日快照提供完整字段；Tushare cb_basic
        仅有基础字段（无价格/溢价率），因此这里固定优先 AKShare，
        而不是默认 chain（tushare 优先会把完整快照遮蔽掉）。
        """
        prio = {"akshare": 0, "tushare": 1, "baostock": 2}
        ordered = sorted(self.chain, key=lambda c: prio.get(c.name, 9))
        for client in ordered:
            try:
                df = client.get_convertible_bond_list()
                if df is not None and len(df) > 0:
                    # AKShare 快照缺必要字段时继续 fallback，不静默返回残缺表
                    if client.name == "akshare" and not {
                        "price", "premium_rate", "rating"
                    }.issubset(df.columns):
                        self.logger.warning("AKShare 可转债列表缺 price/溢价/评级，尝试下一源")
                        continue
                    self.logger.info(f"[{client.name}] 可转债列表: {len(df)} 条")
                    return df
            except DataClientError as e:
                self.logger.warning(f"[{client.name}] 可转债列表失败: {e}")
                continue
        return pd.DataFrame()

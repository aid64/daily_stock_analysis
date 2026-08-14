"""Tushare Pro 数据源 Client

优势：
- 字段最全（OHLCV + 财务 + 可转债 + 指数）
- 15000 积分权限足够覆盖日线 / 财报 / 可转债

局限：
- 不含已退市股的完整历史日线（仅有基础信息）
- 限流严格，500 次/分
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import AbstractDataClient, DataClientError, RateLimitError, AuthError
from ..normalizer import normalize_ohlc, normalize_stock_basic


# 标的代码转换：600519.SH -> 600519.SH (tushare 直接用)
# 但 pro_bar 需要 ts_code 格式 600519.SH
def _to_ts_code(symbol: str) -> str:
    """统一标的代码到 Tushare ts_code 格式（带 .SH / .SZ 后缀）"""
    s = symbol.strip().upper()
    if "." in s:
        return s
    # 纯数字：6 位
    if s.isdigit() and len(s) == 6:
        if s.startswith(("60", "68", "51", "56", "58", "11", "13")):
            return f"{s}.SH"
        if s.startswith(("00", "30", "15", "12")):
            return f"{s}.SZ"
        if s.startswith(("43", "83", "87", "88")):
            return f"{s}.BJ"
    return s


class TushareClient(AbstractDataClient):
    name = "tushare"

    def __init__(
        self,
        token: str,
        rate_limit_per_min: int = 200,
        retry_max: int = 3,
        retry_backoff: float = 1.5,
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(rate_limit_per_min=rate_limit_per_min, logger=logger)
        if not token:
            raise AuthError("Tushare token 为空，请在 .env 中配置 TUSHARE_TOKEN")

        try:
            import tushare as ts
        except ImportError as e:
            raise DataClientError("未安装 tushare, 请 pip install tushare") from e

        # tushare 的 set_token 会写 ~/tk.csv 文件，沙箱/只读环境会失败。
        # 改用环境变量 TUSHARE_TOKEN，tushare get_token() 会优先读取环境变量。
        import os
        os.environ["TUSHARE_TOKEN"] = token

        self._ts = ts
        # pro_api(token) 直接传 token，不依赖全局 set_token
        self._pro = ts.pro_api(token)
        self._retry_max = retry_max
        self._retry_backoff = retry_backoff

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=30),
        reraise=True,
    )
    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        ts_code = _to_ts_code(symbol)
        self._throttle()

        # adjust 字段映射：tushare pro_bar adj 参数为 None / 'qfq' / 'hfq'
        adj = None if adjust == "none" else adjust
        try:
            df = self._ts.pro_bar(
                ts_code=ts_code,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adj=adj,
            )
        except Exception as e:
            msg = str(e).lower()
            if "limit" in msg or "rate" in msg or "freq" in msg:
                raise RateLimitError(f"Tushare 限流: {e}") from e
            if "token" in msg or "auth" in msg or "权限" in msg:
                raise AuthError(f"Tushare 认证失败: {e}") from e
            raise DataClientError(f"Tushare get_daily_bars 失败 ({ts_code}): {e}") from e

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

        return normalize_ohlc(
            df,
            rename_map={
                "trade_date": "date",
                "vol": "volume",
                # open/high/low/close/amount 已同名
            },
        )

    def get_stock_basic(self) -> pd.DataFrame:
        """获取全部股票基础信息，含已退市（list_status='D'）和暂停上市（'P'）"""
        all_dfs = []
        for status in ("L", "D", "P"):
            self._throttle()
            try:
                df = self._pro.stock_basic(
                    list_status=status,
                    fields="ts_code,symbol,name,area,industry,market,list_date,delist_date,exchange",
                )
            except Exception as e:
                self.logger.warning(f"Tushare stock_basic(list_status={status}) 失败: {e}")
                continue
            if df is not None and len(df) > 0:
                all_dfs.append(df)

        if not all_dfs:
            return pd.DataFrame(columns=["symbol", "name", "list_date", "delist_date", "exchange"])

        combined = pd.concat(all_dfs, ignore_index=True)
        return normalize_stock_basic(
            combined,
            rename_map={"ts_code": "symbol"},
        )

    def get_trade_calendar(self, start: str, end: str) -> list[str]:
        self._throttle()
        try:
            df = self._pro.trade_cal(
                exchange="SSE",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                is_open="1",
            )
        except Exception as e:
            raise DataClientError(f"Tushare trade_cal 失败: {e}") from e

        if df is None or len(df) == 0:
            return []
        dates = pd.to_datetime(df["cal_date"], format="%Y%m%d")
        return [d.strftime("%Y-%m-%d") for d in sorted(dates.unique())]

    def get_index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        ts_code = _to_ts_code(code)
        self._throttle()
        try:
            df = self._pro.index_daily(
                ts_code=ts_code,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            )
        except Exception as e:
            raise DataClientError(f"Tushare index_daily 失败 ({ts_code}): {e}") from e

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

        return normalize_ohlc(
            df,
            rename_map={"trade_date": "date", "vol": "volume"},
        )

    def get_etf_daily(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        """ETF 日线：优先用 fund_daily（场内基金日线）

        Tushare 的 pro_bar 对 ETF 支持不稳定，fund_daily 是专用接口。
        复权：fund_daily 不支持 adj 参数，需用 pro_bar(adj=...) 补复权。
        """
        ts_code = _to_ts_code(symbol)

        # 1. 先尝试 fund_daily（不复权）
        self._throttle()
        try:
            df = self._pro.fund_daily(
                ts_code=ts_code,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            )
        except Exception as e:
            msg = str(e).lower()
            if "limit" in msg or "rate" in msg:
                raise RateLimitError(f"Tushare 限流: {e}") from e
            if "权限" in msg or "permission" in msg:
                # 权限不足，fallback 到 pro_bar
                self.logger.info(f"Tushare fund_daily 权限不足，回退 pro_bar: {e}")
                return self.get_daily_bars(symbol, start, end, adjust)
            raise DataClientError(f"Tushare fund_daily 失败 ({ts_code}): {e}") from e

        if df is None or len(df) == 0:
            # fund_daily 空数据，fallback 到 pro_bar
            self.logger.debug(f"Tushare fund_daily 空数据 {ts_code}，回退 pro_bar")
            return self.get_daily_bars(symbol, start, end, adjust)

        df = normalize_ohlc(
            df,
            rename_map={"trade_date": "date", "vol": "volume"},
        )

        # 2. 如果需要复权，用 pro_bar 覆盖（fund_daily 不支持复权）
        if adjust != "none" and len(df) > 0:
            try:
                adj_df = self.get_daily_bars(symbol, start, end, adjust)
                if len(adj_df) > 0:
                    return adj_df
            except Exception as e:
                self.logger.warning(f"ETF 复权数据获取失败 {ts_code}，使用不复权: {e}")

        return df

    def get_convertible_bond_list(self) -> pd.DataFrame:
        """可转债基础信息（Tushare cb_basic）"""
        self._throttle()
        try:
            df = self._pro.cb_basic(fields="ts_code,bond_short_name,stk_code,list_date,delist_date,exchange")
        except Exception as e:
            raise DataClientError(f"Tushare cb_basic 失败: {e}") from e

        if df is None or len(df) == 0:
            return pd.DataFrame()

        df = df.rename(columns={
            "ts_code": "symbol",
            "bond_short_name": "name",
            "stk_code": "underlying_stock",
        })
        df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
        df["delist_date"] = pd.to_datetime(df["delist_date"], errors="coerce")
        return df

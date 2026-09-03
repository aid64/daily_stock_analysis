"""Baostock 数据源 Client

优势：
- 完全免费
- 包含已退市股的完整历史日线（反幸存者偏差关键）
- 接口稳定

局限：
- 字段较 Tushare 少
- 单连接串行，速度慢
- 可转债 / ETF 数据不全
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import AbstractDataClient, DataClientError
from ..normalizer import normalize_ohlc, normalize_stock_basic


def _to_bs_code(symbol: str) -> str:
    """统一标的代码到 Baostock 格式
    600519.SH -> sh.600519
    159915.SZ -> sz.159915
    """
    s = symbol.strip().upper()
    if "." in s:
        code, exchange = s.split(".", 1)
        if exchange in ("SH", "SS"):
            return f"sh.{code}"
        if exchange == "SZ":
            return f"sz.{code}"
        if exchange == "BJ":
            return f"bj.{code}"
        return s.lower().replace(".", ".")
    # 纯数字
    if s.isdigit() and len(s) == 6:
        if s.startswith(("60", "68", "51", "56", "58", "11", "13")):
            return f"sh.{s}"
        if s.startswith(("00", "30", "15", "12")):
            return f"sz.{s}"
        if s.startswith(("43", "83", "87", "88")):
            return f"bj.{s}"
    return s


# 复权标记映射
_ADJUST_FLAG = {
    "hfq": "2",  # 后复权
    "qfq": "1",  # 前复权
    "none": "3",  # 不复权
}


class BaostockClient(AbstractDataClient):
    name = "baostock"

    def __init__(self, logger: Optional[logging.Logger] = None):
        # baostock 单连接串行，保守限流 60 次/分
        super().__init__(rate_limit_per_min=60, logger=logger)
        self._logged_in = False
        self._login()

    def _login(self) -> None:
        try:
            import baostock as bs
        except ImportError as e:
            raise DataClientError("未安装 baostock, 请 pip install baostock") from e

        self._bs = bs
        try:
            result = bs.login()
            if result.error_code != "0":
                raise DataClientError(f"Baostock login 失败: {result.error_msg}")
            self._logged_in = True
            self.logger.info("Baostock login 成功")
        except DataClientError:
            raise
        except Exception as e:
            raise DataClientError(f"Baostock login 异常: {e}") from e

    def close(self) -> None:
        if self._logged_in:
            try:
                self._bs.logout()
                self._logged_in = False
            except Exception as e:
                self.logger.warning(f"Baostock logout 失败: {e}")

    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        bs_code = _to_bs_code(symbol)
        flag = _ADJUST_FLAG.get(adjust, "3")
        self._throttle()

        try:
            rs = self._bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag=flag,
            )
        except Exception as e:
            raise DataClientError(f"Baostock get_daily_bars 失败 ({bs_code}): {e}") from e

        if rs is None or rs.error_code != "0":
            err_msg = rs.error_msg if rs else "None response"
            self.logger.warning(f"Baostock {bs_code} 返回错误: {err_msg}")
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

        # rs.next() 迭代
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

        df = pd.DataFrame(rows, columns=rs.fields)
        # Baostock 返回字符串，需转 float
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        # volume 单位：股（与 tushare 一致）

        return normalize_ohlc(df)

    def get_stock_basic(self) -> pd.DataFrame:
        self._throttle()
        try:
            rs = self._bs.query_all_stock(day=pd.Timestamp.now().strftime("%Y-%m-%d"))
        except Exception as e:
            raise DataClientError(f"Baostock query_all_stock 失败: {e}") from e

        if rs is None or rs.error_code != "0":
            return pd.DataFrame(columns=["symbol", "name", "list_date", "delist_date", "exchange"])

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            return pd.DataFrame(columns=["symbol", "name", "list_date", "delist_date", "exchange"])

        df = pd.DataFrame(rows, columns=rs.fields)
        # Baostock 代码格式 sh.600519 -> 600519.SH
        def _revert(code: str) -> str:
            if "." not in code:
                return code
            prefix, num = code.split(".", 1)
            mapping = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
            return f"{num}.{mapping.get(prefix, prefix.upper())}"

        if "code" in df.columns:
            df["symbol"] = df["code"].apply(_revert)
        elif "symbol" not in df.columns:
            df["symbol"] = None

        # Baostock 不提供 list_date / delist_date，留空
        df["list_date"] = pd.NaT
        df["delist_date"] = pd.NaT
        df["exchange"] = df["symbol"].str.split(".").str[-1].map(
            {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
        )

        return normalize_stock_basic(df)

    def get_trade_calendar(self, start: str, end: str) -> list[str]:
        self._throttle()
        try:
            rs = self._bs.query_trade_dates(start_date=start, end_date=end)
        except Exception as e:
            raise DataClientError(f"Baostock query_trade_dates 失败: {e}") from e

        if rs is None or rs.error_code != "0":
            return []

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        # fields: date, is_trading_day
        dates = []
        for row in rows:
            if len(row) >= 2 and row[1] == "1":
                dates.append(row[0])
        return sorted(set(dates))

    def get_index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        # Baostock 指数代码：sh.000300（沪深 300）
        bs_code = _to_bs_code(code)
        # Baostock 的指数代码前缀用 sh./sz.
        # 000300.SH -> sh.000300
        if bs_code.startswith("SH."):
            bs_code = "sh." + bs_code[3:]
        elif bs_code.startswith("SZ."):
            bs_code = "sz." + bs_code[3:]

        self._throttle()
        try:
            rs = self._bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="3",  # 指数不复权
            )
        except Exception as e:
            raise DataClientError(f"Baostock index_daily 失败 ({bs_code}): {e}") from e

        rows = []
        while rs and rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

        df = pd.DataFrame(rows, columns=rs.fields)
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return normalize_ohlc(df)

"""AKShare 数据源 Client

优势：
- 完全免费
- 可转债 / ETF / 期货数据最全
- 接口丰富（实时 + 历史）

局限：
- 字段名不稳定，akshare 版本迭代频繁
- 不含退市股完整历史
- 限流不严格但偶发封禁
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import AbstractDataClient, DataClientError
from ..normalizer import normalize_ohlc, normalize_stock_basic


def _parse_symbol(symbol: str) -> tuple[str, str]:
    """统一标的代码解析为 (code, exchange)
    600519.SH -> ('600519', 'sh')
    159915.SZ -> ('159915', 'sz')
    """
    s = symbol.strip().upper()
    if "." in s:
        code, ex = s.split(".", 1)
        return code, ex.lower()
    # 纯数字
    if s.isdigit() and len(s) == 6:
        if s.startswith(("60", "68", "51", "56", "58", "11", "13")):
            return s, "sh"
        if s.startswith(("00", "30", "15", "12")):
            return s, "sz"
        if s.startswith(("43", "83", "87", "88")):
            return s, "bj"
    return s, ""


# akshare 复权标记映射："" 不复权 / "qfq" 前复权 / "hfq" 后复权
_AK_ADJUST = {
    "hfq": "hfq",
    "qfq": "qfq",
    "none": "",
}


class AkshareClient(AbstractDataClient):
    name = "akshare"

    def __init__(self, logger: Optional[logging.Logger] = None):
        super().__init__(rate_limit_per_min=120, logger=logger)
        try:
            import akshare as ak
        except ImportError as e:
            raise DataClientError("未安装 akshare, 请 pip install akshare") from e
        self._ak = ak

    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        code, _ = _parse_symbol(symbol)
        adj = _AK_ADJUST.get(adjust, "")
        self._throttle()

        try:
            df = self._ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust=adj if adj else "",
            )
        except Exception as e:
            raise DataClientError(f"AKShare stock_zh_a_hist 失败 ({code}): {e}") from e

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

        return normalize_ohlc(
            df,
            rename_map={
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
            },
        )

    def get_stock_basic(self) -> pd.DataFrame:
        self._throttle()
        try:
            df = self._ak.stock_info_a_code_name()
        except Exception as e:
            raise DataClientError(f"AKShare stock_info_a_code_name 失败: {e}") from e

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["symbol", "name", "list_date", "delist_date", "exchange"])

        # akshare 字段：code, name
        df = df.rename(columns={"code": "symbol_short", "name": "name"})
        # 加后缀
        def _to_full(code: str) -> str:
            if code.startswith(("60", "68", "51", "56", "58", "11", "13")):
                return f"{code}.SH"
            if code.startswith(("00", "30", "15", "12")):
                return f"{code}.SZ"
            if code.startswith(("43", "83", "87", "88")):
                return f"{code}.BJ"
            return code

        df["symbol"] = df["symbol_short"].apply(_to_full)
        df["list_date"] = pd.NaT
        df["delist_date"] = pd.NaT
        df["exchange"] = df["symbol"].str.split(".").str[-1].map(
            {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
        )

        return normalize_stock_basic(df)

    def get_trade_calendar(self, start: str, end: str) -> list[str]:
        self._throttle()
        try:
            df = self._ak.tool_trade_date_hist_sina()
        except Exception as e:
            raise DataClientError(f"AKShare tool_trade_date_hist_sina 失败: {e}") from e

        if df is None or len(df) == 0:
            return []

        dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
        mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
        return [d.strftime("%Y-%m-%d") for d in sorted(dates[mask].unique())]

    def get_index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        """akshare 指数日线
        使用 stock_zh_index_daily_em（东方财富源）
        """
        # akshare 指数代码格式：sh000300（沪深 300）
        symbol_code, ex = _parse_symbol(code)
        ak_symbol = f"{ex}{symbol_code}" if ex else code

        self._throttle()
        try:
            df = self._ak.stock_zh_index_daily_em(symbol=ak_symbol)
        except Exception as e:
            raise DataClientError(f"AKShare index_daily 失败 ({ak_symbol}): {e}") from e

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

        # 字段：date, open, high, low, close, volume, amount
        return normalize_ohlc(df)

    def get_etf_daily(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        """ETF 日线：使用 fund_etf_hist_em（东方财富 ETF 专用接口）

        AKShare 的 stock_zh_a_hist 对 ETF 支持不稳定，fund_etf_hist_em 是专用接口。
        """
        code, _ = _parse_symbol(symbol)
        adj = _AK_ADJUST.get(adjust, "")
        self._throttle()

        try:
            df = self._ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust=adj if adj else "",
            )
        except Exception as e:
            raise DataClientError(f"AKShare fund_etf_hist_em 失败 ({code}): {e}") from e

        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

        return normalize_ohlc(
            df,
            rename_map={
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
            },
        )

    def get_convertible_bond_daily(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """可转债日线：使用 bond_zh_hs_cov_min（日频聚合）或 bond_cb_redeem_jsl

        AKShare 的可转债日线专用接口是 bond_zh_hs_cov_daily（已废弃），
        改用 bond_cb_redeem_jsl 获取当前价 + bond_zh_hs_cov_min 按日聚合。
        实际可用的是 fund_etf_hist_em 对可转债代码也适用（部分版本），
        或直接用 stock_zh_a_hist（可转债代码以 11/12 开头在沪深交易所）。
        """
        # 可转债代码：沪市 11xxxx，深市 12xxxx
        # 先尝试 stock_zh_a_hist（部分可转债可用）
        return self.get_daily_bars(symbol, start, end, adjust="none")

    def get_convertible_bond_list(self) -> pd.DataFrame:
        """可转债基础信息列表（含信用评级、强赎状态）

        使用 bond_zh_cov 接口获取全市场可转债列表 + 剩余期限 + 评级等。
        """
        self._throttle()
        try:
            df = self._ak.bond_zh_cov(
                symbol="全部",
            )
        except Exception as e:
            # 部分版本接口名不同，尝试备用
            try:
                df = self._ak.bond_cb_redeem_jsl()
            except Exception as e2:
                raise DataClientError(
                    f"AKShare 可转债列表获取失败: {e} / {e2}"
                ) from e2

        if df is None or len(df) == 0:
            return pd.DataFrame()

        # 统一列名（不同 akshare 版本字段差异较大，做兼容处理）
        rename = {}
        for col in df.columns:
            cl = col.lower() if isinstance(col, str) else str(col)
            if "代码" in str(col) or cl == "symbol" or cl == "bondcode":
                rename[col] = "symbol"
            elif "名称" in str(col) or cl == "name" or cl == "bondshortname":
                rename[col] = "name"
            elif "评级" in str(col) or "rating" in cl:
                rename[col] = "rating"
            elif "强赎" in str(col) or "redeem" in cl:
                rename[col] = "force_redeem"
            elif "到期" in str(col) or "maturity" in cl:
                rename[col] = "maturity_date"
            elif "溢价" in str(col) or "premium" in cl:
                rename[col] = "premium_rate"
            elif "价格" in str(col) or "price" in cl:
                rename[col] = "price"

        df = df.rename(columns=rename)
        # 去重列
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated()]

        return df

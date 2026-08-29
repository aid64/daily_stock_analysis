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

import numpy as np
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


def adjust_etf_share_split(df: pd.DataFrame, threshold: float = 0.3) -> pd.DataFrame:
    """检测 ETF 份额折算跳变并做前复权调整

    新浪源返回不复权价格，ETF 份额折算（拆分/合并）日会产生远超涨跌停的
    价格跳变（实测案例：510500 2015-04-15 +248.6% 合并、512480 2026-07-03
    -50.7% 拆分），动量/均线类策略会被完全误导。

    判定与调整：
    - |close[i]/close[i-1] - 1| > threshold 视为折算点
      （ETF 涨跌停上限 20%，折算跳变最小约 49%，0.3 阈值安全区分两者）
    - 前复权：折算点之前的 open/high/low/close 全部乘 ratio（当日真实
      收益近似为 0，误差 < 单日涨跌停幅度，一次性且量级小）
    - volume 反向除以 ratio（份额合并后单价升、份额数降），amount 不变

    Args:
        df: 含 date/open/high/low/close/volume/amount 的标准化 DataFrame
        threshold: 跳变判定阈值

    Returns:
        调整后的 DataFrame（原地副本）
    """
    if df is None or len(df) < 2:
        return df

    closes = df["close"].to_numpy(dtype=float)
    ratios = closes[1:] / closes[:-1]
    split_idx = [i + 1 for i in range(len(ratios)) if abs(ratios[i] - 1.0) > threshold]
    if not split_idx:
        return df

    # 累计前复权因子：位置 j 之前（含）的每个折算点 i 都贡献 r_i，
    # 即 factor[j] = ∏ {r_i | i > j}，等价于从后往前逐点调整。
    out = df.copy()
    factor = np.ones(len(out))
    for i in split_idx:
        factor[:i] *= closes[i] / closes[i - 1]

    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * factor
    out["volume"] = out["volume"] / factor

    return out


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
        """ETF 日线：东财 fund_etf_hist_em 优先，新浪 fund_etf_hist_sina 兜底

        AKShare 的 stock_zh_a_hist 对 ETF 支持不稳定，fund_etf_hist_em 是专用接口。
        东财源走 push2his.eastmoney.com，部分网络环境（代理/防火墙）下 TLS 握手
        持续失败；新浪源（finance.sina.com.cn）返回全部上市历史但不支持复权，
        作为兜底并显式警告（宽基 ETF 分红少，不复权对动量计算影响有限）。
        """
        code, ex = _parse_symbol(symbol)
        adj = _AK_ADJUST.get(adjust, "")
        self._throttle()

        df = None
        em_error: Optional[Exception] = None
        try:
            df = self._ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust=adj if adj else "",
            )
        except Exception as e:  # noqa: BLE001 - 东财源异常需触发兜底
            em_error = e

        if df is None or len(df) == 0:
            self._throttle()
            try:
                sina_symbol = f"{ex}{code}" if ex else code
                df = self._ak.fund_etf_hist_sina(symbol=sina_symbol)
            except Exception as e2:  # noqa: BLE001
                raise DataClientError(
                    f"AKShare fund_etf_hist_em 失败 ({code}): {em_error}; "
                    f"fund_etf_hist_sina 兜底失败: {e2}"
                ) from e2

            if adj:
                self.logger.warning(
                    f"[akshare] ETF {code} 东财源失败，降级新浪源（不复权，"
                    f"忽略 adjust={adjust}）"
                )
            # sina 返回全部上市历史，按请求区间过滤；并修正份额折算跳变
            df = normalize_ohlc(df)
            df = adjust_etf_share_split(df)
            mask = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
            return df.loc[mask].reset_index(drop=True)

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
            df = self._ak.bond_zh_cov()  # 新版无参；旧版传 symbol="全部"
        except TypeError:
            try:
                df = self._ak.bond_zh_cov(symbol="全部")
            except Exception as e2:
                raise DataClientError(
                    f"AKShare 可转债列表获取失败: {e2}"
                ) from e2
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

        # 统一列名（不同 akshare 版本字段差异较大，做兼容处理）。
        # 注意映射优先级：先匹配更具体的"正股代码"，避免被"代码"吞掉；
        # "债券代码"才是 symbol，"申购代码"是打新专用需忽略。
        rename = {}
        for col in df.columns:
            cl = col.lower() if isinstance(col, str) else str(col)
            if "正股" in str(col) and "代码" in str(col):
                rename[col] = "underlying_stock"
            elif "债券代码" in str(col) or cl == "symbol" or cl == "bondcode":
                rename[col] = "symbol"
            elif "债券简称" in str(col) or "名称" in str(col) or cl in ("name", "bondshortname"):
                rename[col] = "name"
            elif "评级" in str(col) or "rating" in cl:
                rename[col] = "rating"
            elif "强赎" in str(col) or "redeem" in cl:
                rename[col] = "force_redeem"
            elif "到期" in str(col) or "maturity" in cl:
                rename[col] = "maturity_date"
            elif "溢价" in str(col) or "premium" in cl:
                rename[col] = "premium_rate"
            # 债现价 / 最新价 / 现价 都映射到 price（"价格"关键词匹配不到"债现价"）
            elif ("价格" in str(col) or "price" in cl
                  or "债现价" in str(col) or cl in ("latestprice", "lastprice")):
                rename[col] = "price"
            elif "上市" in str(col) or "list" in cl:
                rename[col] = "list_date"
            elif "规模" in str(col) or "size" in cl:
                rename[col] = "issue_size"

        df = df.rename(columns=rename)
        # 去重列
        if df.columns.duplicated().any():
            df = df.loc[:, ~df.columns.duplicated()]

        # 数值列转 float（AKShare 返回多为字符串）
        for col in ("price", "premium_rate"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

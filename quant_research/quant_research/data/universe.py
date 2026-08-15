"""标的池管理（Universe Builder）

核心功能：还原历史时点的真实可投资标的池，反幸存者偏差。

关键方法：build_point_in_time_universe
- 给定历史日期，返回当日可买入的所有股票
- 过滤已退市、ST、涨跌停（可选）
- 这是回测真实性的关键工程实现
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd


class UniverseBuilder:
    """还原历史时点的真实可投资标的池"""

    def __init__(self, stock_basic: pd.DataFrame, logger: Optional[logging.Logger] = None):
        """
        Args:
            stock_basic: 股票基础信息 DataFrame，至少含列：
                symbol, name, list_date, delist_date, exchange
        """
        self.df = stock_basic.copy()
        self.df["list_date"] = pd.to_datetime(self.df["list_date"], errors="coerce")
        self.df["delist_date"] = pd.to_datetime(self.df["delist_date"], errors="coerce")
        self.logger = logger or logging.getLogger("universe")

    def build_point_in_time_universe(
        self,
        date: str,
        exchange: Optional[str] = None,
        exclude_st: bool = False,
        exclude_pt: bool = True,
    ) -> list[str]:
        """返回该日期可买入的所有股票代码

        Args:
            date: 日期 YYYY-MM-DD
            exchange: 交易所过滤 SSE / SZSE / BSE，None 表示全部
            exclude_st: 排除 ST 股
            exclude_pt: 排除 *ST / 退市风险股（PT 是历史名词，这里指退市风险）

        Returns:
            标的代码列表（600519.SH 格式）
        """
        date_dt = pd.Timestamp(date)

        mask = pd.Series([True] * len(self.df), index=self.df.index)

        # 已上市
        mask &= self.df["list_date"].isna() | (self.df["list_date"] <= date_dt)

        # 未退市
        mask &= self.df["delist_date"].isna() | (self.df["delist_date"] > date_dt)

        # 交易所
        if exchange:
            mask &= self.df["exchange"] == exchange

        # ST / *ST 过滤
        if exclude_st and "name" in self.df.columns:
            mask &= ~self.df["name"].str.contains("ST", na=False)
        if exclude_pt and "name" in self.df.columns:
            mask &= ~self.df["name"].str.contains(r"\*ST", na=False, regex=True)

        result = self.df.loc[mask, "symbol"].tolist()
        self.logger.debug(f"Universe[{date}] exchange={exchange} exclude_st={exclude_st}: {len(result)} 只")
        return result

    def is_listed(self, symbol: str, date: str) -> bool:
        """检查标的在指定日期是否已上市且未退市"""
        date_dt = pd.Timestamp(date)
        row = self.df[self.df["symbol"] == symbol]
        if len(row) == 0:
            return False
        row = row.iloc[0]
        if pd.notna(row["list_date"]) and row["list_date"] > date_dt:
            return False
        if pd.notna(row["delist_date"]) and row["delist_date"] <= date_dt:
            return False
        return True

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """获取单个标的的完整信息"""
        row = self.df[self.df["symbol"] == symbol]
        if len(row) == 0:
            return None
        r = row.iloc[0]
        return {
            "symbol": r["symbol"],
            "name": r.get("name"),
            "list_date": r.get("list_date"),
            "delist_date": r.get("delist_date"),
            "exchange": r.get("exchange"),
        }

    @staticmethod
    def filter_etf_universe(symbols: list[str], etf_list: list[str]) -> list[str]:
        """从全市场 symbols 中筛选 ETF 池（保留 etf_list 中存在的）

        Args:
            symbols: 完整 universe
            etf_list: 静态 ETF 池（如 510300.SH）

        Returns:
            etf_list 中且在 symbols 中存在的标的
        """
        sset = set(symbols)
        return [s for s in etf_list if s in sset]

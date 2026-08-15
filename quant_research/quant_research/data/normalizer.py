"""数据标准化

不同数据源字段名、单位、日期格式不一致，统一标准化为 STANDARD_OHLC_COLUMNS。

标准化规则：
    date     -> datetime64[ns]
    open/high/low/close -> float64
    volume   -> float64（股数）
    amount   -> float64（元）
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


# 标准 OHLC + 成交量成交额列
STANDARD_OHLC_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]

# 股票基础信息标准列
STANDARD_BASIC_COLUMNS = ["symbol", "name", "list_date", "delist_date", "exchange"]


def normalize_ohlc(
    df: pd.DataFrame,
    *,
    date_col: str = "date",
    rename_map: Optional[dict] = None,
) -> pd.DataFrame:
    """将 DataFrame 标准化为 OHLC 格式

    Args:
        df: 原始 DataFrame
        date_col: 日期列名（rename 后）
        rename_map: 字段重命名映射，如 {"trade_date": "date", "vol": "volume"}

    Returns:
        仅包含 STANDARD_OHLC_COLUMNS 的 DataFrame
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=STANDARD_OHLC_COLUMNS)

    if rename_map:
        df = df.rename(columns=rename_map)

    # 缺列补 NaN
    for col in STANDARD_OHLC_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    # 日期标准化
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    # 数值列强制 float（pd.to_numeric 对整数列保留 int，需 astype(float)）
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    df = df[STANDARD_OHLC_COLUMNS].sort_values("date").reset_index(drop=True)
    return df


def normalize_stock_basic(
    df: pd.DataFrame,
    *,
    rename_map: Optional[dict] = None,
) -> pd.DataFrame:
    """将股票基础信息标准化"""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=STANDARD_BASIC_COLUMNS)

    if rename_map:
        df = df.rename(columns=rename_map)

    # 去除重复列名（部分数据源返回重复列）
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]

    for col in STANDARD_BASIC_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
    df["delist_date"] = pd.to_datetime(df["delist_date"], errors="coerce")
    return df[STANDARD_BASIC_COLUMNS]

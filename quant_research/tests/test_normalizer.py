"""数据标准化测试"""
from __future__ import annotations

import pandas as pd
import pytest

from quant_research.data.normalizer import (
    normalize_ohlc,
    normalize_stock_basic,
    STANDARD_OHLC_COLUMNS,
)


class TestNormalizeOhlc:
    def test_basic_rename_and_normalize(self, sample_ohlc_df):
        df = normalize_ohlc(
            sample_ohlc_df,
            rename_map={"trade_date": "date", "vol": "volume"},
        )
        assert list(df.columns) == STANDARD_OHLC_COLUMNS
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        assert df["close"].dtype == float
        assert df["volume"].dtype == float
        # 升序排列
        assert df["date"].is_monotonic_increasing

    def test_empty_df(self):
        df = normalize_ohlc(pd.DataFrame())
        assert list(df.columns) == STANDARD_OHLC_COLUMNS
        assert len(df) == 0

    def test_none_df(self):
        df = normalize_ohlc(None)
        assert len(df) == 0

    def test_missing_columns(self):
        df = pd.DataFrame({"date": ["20240101"], "close": [10.0]})
        result = normalize_ohlc(df)
        assert list(result.columns) == STANDARD_OHLC_COLUMNS
        assert pd.isna(result["open"].iloc[0])

    def test_invalid_dates_dropped(self):
        df = pd.DataFrame({
            "date": ["20240101", "invalid", "20240103"],
            "close": [10.0, 11.0, 12.0],
        })
        result = normalize_ohlc(df)
        assert len(result) == 2

    def test_sort_order(self):
        df = pd.DataFrame({
            "date": ["20240103", "20240101", "20240102"],
            "close": [12.0, 10.0, 11.0],
        })
        result = normalize_ohlc(df)
        assert result["date"].iloc[0] == pd.Timestamp("2024-01-01")
        assert result["close"].iloc[0] == 10.0


class TestNormalizeStockBasic:
    def test_basic(self, sample_stock_basic_df):
        df = normalize_stock_basic(
            sample_stock_basic_df,
            rename_map={"ts_code": "symbol"},
        )
        assert "symbol" in df.columns
        assert "list_date" in df.columns
        assert pd.api.types.is_datetime64_any_dtype(df["list_date"])
        assert "贵州茅台" in df["name"].values

    def test_empty(self):
        df = normalize_stock_basic(pd.DataFrame())
        assert len(df) == 0

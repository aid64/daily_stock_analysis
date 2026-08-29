"""Parquet 缓存测试"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from quant_research.data.cache import ParquetCache


@pytest.fixture
def sample_bars_df():
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "open": [10.0, 10.5, 10.3],
        "high": [10.8, 10.9, 10.6],
        "low": [9.9, 10.2, 10.0],
        "close": [10.5, 10.3, 10.4],
        "volume": [100000.0, 120000.0, 95000.0],
        "amount": [1050000.0, 1236000.0, 988000.0],
    })


class TestParquetCache:
    def test_put_and_get_bars(self, tmp_cache_dir: Path, sample_bars_df):
        cache = ParquetCache(tmp_cache_dir)
        cache.put_bars("600519.SH", sample_bars_df, "hfq")

        result = cache.get_bars("600519.SH", "2024-01-01", "2024-01-03", "hfq")
        assert result is not None
        assert len(result) == 3
        assert result["close"].iloc[0] == 10.5

    def test_get_nonexistent(self, tmp_cache_dir: Path):
        cache = ParquetCache(tmp_cache_dir)
        result = cache.get_bars("999999.SH", "2024-01-01", "2024-01-03", "hfq")
        assert result is None

    def test_date_range_filter(self, tmp_cache_dir: Path, sample_bars_df):
        cache = ParquetCache(tmp_cache_dir)
        cache.put_bars("600519.SH", sample_bars_df, "hfq")

        # 部分范围（缓存完整覆盖请求区间）
        result = cache.get_bars("600519.SH", "2024-01-02", "2024-01-03", "hfq")
        assert len(result) == 2

        # 请求终点超出缓存覆盖（增量更新场景）→ miss 触发重新下载
        result = cache.get_bars("600519.SH", "2024-02-01", "2024-02-28", "hfq")
        assert result is None

    def test_start_not_covered_returns_none(self, tmp_cache_dir: Path, sample_bars_df):
        """请求起点早于缓存起点（预热期场景）→ miss，防止静默截断"""
        cache = ParquetCache(tmp_cache_dir)
        cache.put_bars("600519.SH", sample_bars_df, "hfq")

        result = cache.get_bars("600519.SH", "2023-11-01", "2024-01-03", "hfq")
        assert result is None

    def test_incremental_merge(self, tmp_cache_dir: Path, sample_bars_df):
        cache = ParquetCache(tmp_cache_dir)
        cache.put_bars("600519.SH", sample_bars_df, "hfq")

        # 增量追加新日期
        new_data = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-04", "2024-01-05"]),
            "open": [10.6, 10.7],
            "high": [10.9, 11.0],
            "low": [10.4, 10.5],
            "close": [10.8, 10.9],
            "volume": [110000.0, 130000.0],
            "amount": [1188000.0, 1417000.0],
        })
        cache.put_bars("600519.SH", new_data, "hfq")

        result = cache.get_bars("600519.SH", "2024-01-01", "2024-01-05", "hfq")
        assert len(result) == 5
        assert result["date"].is_monotonic_increasing

    def test_dedup_on_overlap(self, tmp_cache_dir: Path, sample_bars_df):
        cache = ParquetCache(tmp_cache_dir)
        cache.put_bars("600519.SH", sample_bars_df, "hfq")

        # 重复写入第一天数据，新值应覆盖
        overlap = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-01"]),
            "open": [99.0],
            "high": [99.0],
            "low": [99.0],
            "close": [99.0],
            "volume": [999.0],
            "amount": [999.0],
        })
        cache.put_bars("600519.SH", overlap, "hfq")

        result = cache.get_bars("600519.SH", "2024-01-01", "2024-01-01", "hfq")
        assert len(result) == 1
        assert result["close"].iloc[0] == 99.0

    def test_stock_basic_cache(self, tmp_cache_dir: Path):
        cache = ParquetCache(tmp_cache_dir)
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "name": ["贵州茅台"],
            "list_date": pd.to_datetime(["2001-08-27"]),
            "delist_date": [pd.NaT],
            "exchange": ["SSE"],
        })
        cache.put_stock_basic(df)
        result = cache.get_stock_basic()
        assert result is not None
        assert len(result) == 1

    def test_list_cached_symbols(self, tmp_cache_dir: Path, sample_bars_df):
        cache = ParquetCache(tmp_cache_dir)
        cache.put_bars("600519.SH", sample_bars_df, "hfq")
        cache.put_bars("000001.SZ", sample_bars_df, "hfq")

        symbols = cache.list_cached_symbols()
        assert "600519.SH" in symbols
        assert "000001.SZ" in symbols

        # 按交易所过滤
        sh_symbols = cache.list_cached_symbols(exchange="sh")
        assert "600519.SH" in sh_symbols
        assert "000001.SZ" not in sh_symbols

    def test_clear_all(self, tmp_cache_dir: Path, sample_bars_df):
        cache = ParquetCache(tmp_cache_dir)
        cache.put_bars("600519.SH", sample_bars_df, "hfq")
        assert cache.get_bars("600519.SH", "2024-01-01", "2024-01-03", "hfq") is not None

        cache.clear()
        assert cache.get_bars("600519.SH", "2024-01-01", "2024-01-03", "hfq") is None

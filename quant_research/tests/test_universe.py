"""Universe Builder 测试"""
from __future__ import annotations

import pandas as pd
import pytest

from quant_research.data.universe import UniverseBuilder


@pytest.fixture
def universe_builder():
    basic = pd.DataFrame({
        "symbol": [
            "600519.SH",   # 已上市
            "000001.SZ",   # 已上市
            "400001.BJ",   # 已退市（2023-01-15）
            "600002.SH",   # 2020 年后上市
            "000002.SZ",   # ST 股
        ],
        "name": ["贵州茅台", "平安银行", "退市公司A", "新上市", "ST测试"],
        "list_date": pd.to_datetime(["2001-08-27", "1991-04-03", "2010-01-01", "2020-06-01", "2005-01-01"]),
        "delist_date": [pd.NaT, pd.NaT, pd.to_datetime("2023-01-15"), pd.NaT, pd.NaT],
        "exchange": ["SSE", "SZSE", "BSE", "SSE", "SZSE"],
    })
    return UniverseBuilder(basic)


class TestUniverseBuilder:
    def test_point_in_time_basic(self, universe_builder):
        # 2022 年所有 5 只都应可投
        result = universe_builder.build_point_in_time_universe("2022-06-01")
        assert len(result) == 5

    def test_exclude_delisted(self, universe_builder):
        # 2024 年应排除已退市的 400001.BJ
        result = universe_builder.build_point_in_time_universe("2024-06-01")
        assert "400001.BJ" not in result
        assert len(result) == 4

    def test_exclude_not_listed_yet(self, universe_builder):
        # 2010 年 600002.SH 还未上市
        result = universe_builder.build_point_in_time_universe("2010-06-01")
        assert "600002.SH" not in result
        # 600519/000001/400001/000002 上市了
        assert "600519.SH" in result
        assert len(result) == 4

    def test_exclude_st(self, universe_builder):
        result = universe_builder.build_point_in_time_universe(
            "2024-06-01", exclude_st=True
        )
        assert "000002.SZ" not in result  # ST 被排除
        assert "600519.SH" in result

    def test_filter_by_exchange(self, universe_builder):
        result = universe_builder.build_point_in_time_universe(
            "2024-06-01", exchange="SSE"
        )
        assert all(s.endswith(".SH") for s in result)
        assert "600519.SH" in result

    def test_is_listed(self, universe_builder):
        assert universe_builder.is_listed("600519.SH", "2024-06-01") is True
        assert universe_builder.is_listed("400001.BJ", "2024-06-01") is False
        assert universe_builder.is_listed("600002.SH", "2010-06-01") is False  # 未上市
        assert universe_builder.is_listed("600002.SH", "2024-06-01") is True

    def test_get_symbol_info(self, universe_builder):
        info = universe_builder.get_symbol_info("600519.SH")
        assert info is not None
        assert info["name"] == "贵州茅台"
        assert info["exchange"] == "SSE"

    def test_get_symbol_info_nonexistent(self, universe_builder):
        info = universe_builder.get_symbol_info("999999.SH")
        assert info is None

    def test_filter_etf_universe(self):
        symbols = ["510300.SH", "510500.SH", "600519.SH"]
        etf_list = ["510300.SH", "510050.SH", "510500.SH"]
        result = UniverseBuilder.filter_etf_universe(symbols, etf_list)
        assert "510300.SH" in result
        assert "510500.SH" in result
        assert "510050.SH" not in result  # 不在 symbols 中
        assert "600519.SH" not in result  # 不在 etf_list 中

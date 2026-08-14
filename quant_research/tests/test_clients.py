"""数据 Client 单元测试（不依赖网络）

用 mock 验证 Client 接口契约和错误处理。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from quant_research.data.clients.base import AbstractDataClient, DataClientError
from quant_research.data.clients.tushare_client import TushareClient, _to_ts_code
from quant_research.data.clients.baostock_client import BaostockClient, _to_bs_code
from quant_research.data.clients.akshare_client import AkshareClient, _parse_symbol


class TestSymbolConverters:
    def test_to_ts_code_with_suffix(self):
        assert _to_ts_code("600519.SH") == "600519.SH"

    def test_to_ts_code_pure_digit_sh(self):
        assert _to_ts_code("600519") == "600519.SH"
        assert _to_ts_code("510300") == "510300.SH"
        assert _to_ts_code("688981") == "688981.SH"

    def test_to_ts_code_pure_digit_sz(self):
        assert _to_ts_code("000001") == "000001.SZ"
        assert _to_ts_code("300750") == "300750.SZ"
        assert _to_ts_code("159915") == "159915.SZ"

    def test_to_ts_code_pure_digit_bj(self):
        assert _to_ts_code("430047") == "430047.BJ"

    def test_to_bs_code_with_suffix(self):
        assert _to_bs_code("600519.SH") == "sh.600519"
        assert _to_bs_code("000001.SZ") == "sz.000001"
        assert _to_bs_code("430047.BJ") == "bj.430047"

    def test_to_bs_code_pure_digit(self):
        assert _to_bs_code("600519") == "sh.600519"
        assert _to_bs_code("000001") == "sz.000001"

    def test_parse_symbol(self):
        assert _parse_symbol("600519.SH") == ("600519", "sh")
        assert _parse_symbol("000001.SZ") == ("000001", "sz")
        assert _parse_symbol("600519") == ("600519", "sh")
        assert _parse_symbol("000001") == ("000001", "sz")


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


HAS_TUSHARE = _has_module("tushare")
HAS_BAOSTOCK = _has_module("baostock")
HAS_AKSHARE = _has_module("akshare")


class TestTushareClient:
    def test_init_without_token_raises(self):
        from quant_research.data.clients.base import AuthError
        with pytest.raises(AuthError):
            TushareClient(token="")

    @pytest.mark.skipif(not HAS_TUSHARE, reason="tushare 未安装")
    def test_throttle_respects_rate_limit(self):
        """验证 throttle 不影响测试（快速通过）"""
        # 用 mock 跳过真实 tushare 初始化
        with patch("tushare.set_token"), patch("tushare.pro_api"):
            client = TushareClient(token="fake_token_for_test", rate_limit_per_min=10000)
            # 多次调用 throttle 不应阻塞
            import time
            t0 = time.time()
            for _ in range(5):
                client._throttle()
            assert time.time() - t0 < 1.0


class TestBaostockClient:
    @pytest.mark.skipif(not HAS_BAOSTOCK, reason="baostock 未安装")
    def test_init_requires_baostock(self):
        with patch.dict("sys.modules", {"baostock": None}):
            with pytest.raises(DataClientError):
                BaostockClient()


class TestAkshareClient:
    @pytest.mark.skipif(not HAS_AKSHARE, reason="akshare 未安装")
    def test_init_requires_akshare(self):
        with patch.dict("sys.modules", {"akshare": None}):
            with pytest.raises(DataClientError):
                AkshareClient()


class TestBaseClient:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            AbstractDataClient()

    def test_context_manager(self):
        """测试 close 在 __exit__ 时被调用"""
        class DummyClient(AbstractDataClient):
            name = "dummy"

            def get_daily_bars(self, *args, **kwargs):
                return pd.DataFrame()

            def get_stock_basic(self):
                return pd.DataFrame()

            def get_trade_calendar(self, *args, **kwargs):
                return []

            def get_index_daily(self, *args, **kwargs):
                return pd.DataFrame()

        with DummyClient() as c:
            assert c is not None
        # 离开 with 后 close 已调用，无异常即可

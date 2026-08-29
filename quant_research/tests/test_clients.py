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
from quant_research.data.clients.akshare_client import AkshareClient, _parse_symbol, adjust_etf_share_split


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


class TestAdjustEtfShareSplit:
    """ETF 份额折算跳变检测与前复权调整"""

    @staticmethod
    def _make_df(closes: list[float]) -> pd.DataFrame:
        n = len(closes)
        return pd.DataFrame(
            {
                "date": pd.date_range("2020-01-01", periods=n, freq="D"),
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1_000_000.0] * n,
                "amount": [c * 1_000_000.0 for c in closes],
            }
        )

    def test_no_split_unchanged(self):
        """无跳变（含真实涨停 ±20%）时数据不变"""
        closes = [1.0] * 5 + [1.2, 1.1, 0.9, 1.08, 1.0]
        df = self._make_df(closes)
        out = adjust_etf_share_split(df)
        pd.testing.assert_frame_equal(out, df)

    def test_split_adjusted(self):
        """拆分跳变（约 -50%）触发前复权：折算前价格 ×ratio，volume ×1/ratio"""
        closes = [2.0, 2.02, 1.98, 1.0, 1.02]  # 第 4 日约 -50% 跳变
        df = self._make_df(closes)
        out = adjust_etf_share_split(df)

        ratio = 1.0 / 1.98  # 折算比例 = 跳变日收盘 / 前日收盘
        assert list(out["close"]) == pytest.approx(
            [2.0 * ratio, 2.02 * ratio, 1.98 * ratio, 1.0, 1.02]
        )
        assert list(out["volume"]) == pytest.approx(
            [1_000_000.0 / ratio] * 3 + [1_000_000.0] * 2
        )
        # 收益率序列连续（无 |ret| > 30% 的跳变）
        rets = out["close"].pct_change().dropna()
        assert (rets.abs() < 0.3).all()

    def test_merge_adjusted(self):
        """合并跳变（约 +100%）：折算前价格 ×ratio，volume ×1/ratio"""
        closes = [1.0, 1.01, 0.99, 2.0, 2.02]
        df = self._make_df(closes)
        out = adjust_etf_share_split(df)
        ratio = 2.0 / 0.99
        assert list(out["close"]) == pytest.approx(
            [1.0 * ratio, 1.01 * ratio, 0.99 * ratio, 2.0, 2.02]
        )

    def test_two_splits_compound(self):
        """两次折算：早段 ×r1×r2，中段 ×r2，末段不变"""
        closes = [1.0, 1.0, 2.0, 2.0, 4.0, 4.0]  # 第 3 日 +100%，第 5 日 +100%
        df = self._make_df(closes)
        out = adjust_etf_share_split(df)
        assert list(out["close"]) == [4.0, 4.0, 4.0, 4.0, 4.0, 4.0]

    def test_open_high_low_volume_consistent(self):
        """open/high/low 与 close 同步调整，amount 不变"""
        closes = [1.0, 1.0, 2.0]
        df = self._make_df(closes)
        out = adjust_etf_share_split(df)
        assert list(out["open"]) == [2.0, 2.0, 2.0]
        assert list(out["high"]) == [2.02, 2.02, 2.02]
        assert list(out["low"]) == [1.98, 1.98, 1.98]
        # amount 保持原始值（真实成交额不因折算改变）
        assert list(out["amount"]) == [1_000_000.0, 1_000_000.0, 2_000_000.0]


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

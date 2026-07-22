# -*- coding: utf-8 -*-
"""MoneyFlowFetcher 单元测试（mock 网络，不依赖真实 EastMoney API）。

覆盖：
- secid 转换（沪深京市场识别）
- 个股资金流：即时汇总解析、10 日 kline 派生 5d/10d 累计与连续天数
- 行业资金流：排行解析、个股所属行业排名查找
- 北向资金：s2n 解析、个股北向持股字段映射
- fail-open：任一子接口失败不影响其他子接口
- enabled=False 时所有方法返回 not_supported
- AkshareFundamentalAdapter.get_capital_flow 集成：主路径成功直接返回，失败走 akshare 兜底
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, Optional
from unittest.mock import patch, MagicMock

from data_provider.moneyflow_fetcher import (
    MoneyFlowFetcher,
    _to_eastmoney_secid,
    _safe_float,
    _request_json,
)


class TestSecidConversion(unittest.TestCase):
    """验证 A 股代码到 EastMoney secid 的转换。"""

    def test_shanghai_main_board(self):
        self.assertEqual(_to_eastmoney_secid("600519"), "1.600519")  # 茅台
        self.assertEqual(_to_eastmoney_secid("601318"), "1.601318")  # 平安

    def test_shanghai_star_market(self):
        self.assertEqual(_to_eastmoney_secid("688981"), "1.688981")  # 中芯

    def test_shenzhen_main_board(self):
        self.assertEqual(_to_eastmoney_secid("000001"), "0.000001")  # 平安银行
        self.assertEqual(_to_eastmoney_secid("002594"), "0.002594")  # 比亚迪

    def test_shenzhen_chinext(self):
        self.assertEqual(_to_eastmoney_secid("300750"), "0.300750")  # 宁德

    def test_beijing_exchange(self):
        self.assertEqual(_to_eastmoney_secid("830799"), "0.830799")

    def test_non_a_share_returns_none(self):
        self.assertIsNone(_to_eastmoney_secid("AAPL"))
        self.assertIsNone(_to_eastmoney_secid("hk00700"))
        self.assertIsNone(_to_eastmoney_secid(""))
        self.assertIsNone(_to_eastmoney_secid("12345"))  # 长度不足
        self.assertIsNone(_to_eastmoney_secid("abcdef"))  # 非数字

    def test_normalization_with_prefix(self):
        # 带 SH/SZ 前缀也应正确解析
        self.assertEqual(_to_eastmoney_secid("SH600519"), "1.600519")
        self.assertEqual(_to_eastmoney_secid("sz000001"), "0.000001")


class TestSafeFloat(unittest.TestCase):
    def test_normal_number(self):
        self.assertEqual(_safe_float(123.45), 123.45)
        self.assertEqual(_safe_float("123.45"), 123.45)
        self.assertEqual(_safe_float(0), 0.0)
        self.assertEqual(_safe_float(-100.5), -100.5)

    def test_none_and_dash(self):
        self.assertIsNone(_safe_float(None))
        self.assertIsNone(_safe_float("-"))

    def test_invalid_string(self):
        self.assertIsNone(_safe_float("abc"))
        self.assertIsNone(_safe_float(""))
        self.assertIsNone(_safe_float("123,456.78"))  # 含逗号无法直接转换

    def test_nan(self):
        import math
        self.assertIsNone(_safe_float(math.nan))


class TestRequestJson(unittest.TestCase):
    """验证 _request_json 的 fail-open 行为。"""

    def test_successful_response(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"rc": 0, "data": {"foo": "bar"}}
        resp.raise_for_status.return_value = None
        session.get.return_value = resp

        data, err = _request_json(session, "http://test", {}, 1.0, "test_block")
        self.assertIsNone(err)
        self.assertEqual(data, {"rc": 0, "data": {"foo": "bar"}})

    def test_timeout(self):
        import requests
        session = MagicMock()
        session.get.side_effect = requests.Timeout("timeout")
        data, err = _request_json(session, "http://test", {}, 1.0, "test_block")
        self.assertIsNone(data)
        self.assertEqual(err, "test_block:timeout")

    def test_request_exception(self):
        import requests
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("conn refused")
        data, err = _request_json(session, "http://test", {}, 1.0, "test_block")
        self.assertIsNone(data)
        self.assertIn("test_block:request_error", err)

    def test_invalid_json(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.side_effect = ValueError("not json")
        resp.raise_for_status.return_value = None
        session.get.return_value = resp
        data, err = _request_json(session, "http://test", {}, 1.0, "test_block")
        self.assertIsNone(data)
        self.assertIn("test_block:json_error", err)

    def test_non_zero_rc(self):
        session = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"rc": -1, "data": None}
        resp.raise_for_status.return_value = None
        session.get.return_value = resp
        data, err = _request_json(session, "http://test", {}, 1.0, "test_block")
        self.assertIsNone(data)
        self.assertIn("rc=-1", err)


class TestGetStockFlow(unittest.TestCase):
    """个股资金流解析。"""

    def _mock_session_with_responses(self, responses):
        """构造 mock session，按顺序返回 responses。"""
        session = MagicMock()
        session.get.side_effect = responses
        return session

    def _make_realtime_resp(self, data_dict):
        resp = MagicMock()
        resp.json.return_value = {"rc": 0, "data": data_dict}
        resp.raise_for_status.return_value = None
        return resp

    def _make_kline_resp(self, klines_list):
        resp = MagicMock()
        resp.json.return_value = {"rc": 0, "data": {"klines": klines_list}}
        resp.raise_for_status.return_value = None
        return resp

    def test_disabled_returns_not_supported(self):
        fetcher = MoneyFlowFetcher(enabled=False)
        result = fetcher.get_stock_flow("600519")
        self.assertEqual(result["status"], "not_supported")
        self.assertIn("moneyflow_api_disabled", result["errors"])

    def test_non_a_share_returns_not_supported(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        result = fetcher.get_stock_flow("AAPL")
        self.assertEqual(result["status"], "not_supported")
        self.assertTrue(any("invalid_a_share_code" in e for e in result["errors"]))

    def test_realtime_only_returns_partial(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        # 即时成功，kline 失败
        rt_resp = self._make_realtime_resp({
            "f62": 1000000.0,  # 主力净流入
            "f184": 5.2,
            "f66": 800000.0,  # 超大单
            "f69": 4.1,
            "f72": 200000.0,  # 大单
            "f75": 1.1,
            "f78": -300000.0,  # 中单
            "f81": -1.5,
            "f84": -700000.0,  # 小单
            "f87": -3.7,
        })
        kl_resp = MagicMock()
        kl_resp.json.side_effect = ValueError("invalid")

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, kl_resp]
            result = fetcher.get_stock_flow("600519")

        self.assertEqual(result["status"], "partial")
        sf = result["stock_flow"]
        self.assertEqual(sf["main_net_inflow"], 1000000.0)
        self.assertEqual(sf["super_large_net_inflow"], 800000.0)
        self.assertEqual(sf["small_net_inflow"], -700000.0)
        self.assertIsNone(sf.get("inflow_5d"))  # kline 失败
        self.assertIn("moneyflow:stock_flow_realtime", result["source_chain"])
        self.assertTrue(any("stock_flow_kline" in e for e in result["errors"]))

    def test_realtime_and_kline_returns_ok(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        rt_resp = self._make_realtime_resp({"f62": 500000.0, "f66": 300000.0})
        # 构造 10 日 kline（5 日累计应为最后 5 日之和）
        klines = [
            "2025-01-0X,100000.0,-30000.0,-20000.0,50000.0,80000.0,1.2"
            for _ in range(10)
        ]
        # 简化：每日主力 100000，5d=500000，10d=1000000，consecutive=10
        klines = [f"2025-01-{i:02d},100000.0,-30000.0,-20000.0,50000.0,80000.0,1.2" for i in range(1, 11)]
        kl_resp = self._make_kline_resp(klines)

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, kl_resp]
            result = fetcher.get_stock_flow("600519")

        self.assertEqual(result["status"], "ok")
        sf = result["stock_flow"]
        self.assertEqual(sf["inflow_5d"], 500000.0)
        self.assertEqual(sf["inflow_10d"], 1000000.0)
        self.assertEqual(sf["consecutive_inflow_days"], 10)

    def test_consecutive_outflow_days_negative(self):
        """连续净流出应为负数。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        rt_resp = self._make_realtime_resp({"f62": -500000.0})
        klines = [f"2025-01-{i:02d},-100000.0,30000.0,20000.0,-50000.0,-80000.0,-1.2" for i in range(1, 4)]
        kl_resp = self._make_kline_resp(klines)

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, kl_resp]
            result = fetcher.get_stock_flow("600519")

        self.assertEqual(result["stock_flow"]["consecutive_inflow_days"], -3)


class TestGetSectorFlow(unittest.TestCase):
    """行业资金流解析。"""

    def test_sector_ranking_with_industry_match(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        ranking_resp = MagicMock()
        ranking_resp.json.return_value = {
            "rc": 0,
            "data": {"diff": [
                {"f12": "BK0475", "f14": "半导体", "f62": 1000000.0, "f66": 500000.0, "f72": 300000.0, "f78": 100000.0, "f84": 100000.0},
                {"f12": "BK0438", "f14": "电池", "f62": 500000.0},
                {"f12": "BK0479", "f14": "酿酒", "f62": -200000.0},
            ]},
        }
        ranking_resp.raise_for_status.return_value = None

        # 个股 600519（茅台）属于酿酒行业
        industry_resp = MagicMock()
        industry_resp.json.return_value = {
            "rc": 0,
            "data": {"f127": "BK0479", "f128": "酿酒"},
        }
        industry_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [ranking_resp, industry_resp]
            result = fetcher.get_sector_flow("600519", top_n=2)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["sector_rankings"]["top"]), 2)
        self.assertEqual(result["sector_rankings"]["top"][0]["name"], "半导体")
        # 个股所属行业在 90 个行业中排第 3（最弱）
        self.assertEqual(result["stock_industry"]["name"], "酿酒")
        self.assertEqual(result["stock_industry"]["rank_in_flow"], 3)
        self.assertEqual(result["stock_industry"]["total_industries"], 3)
        self.assertEqual(result["stock_industry"]["industry_main_net_inflow"], -200000.0)

    def test_ranking_failure_returns_not_supported(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = Exception("network down")
            # _build_session 内部 try/except 会吞掉异常返回 session，session.get 会抛
            # 实际 _request_json 会捕获 requests.RequestException；这里直接 mock _request_json
            with patch("data_provider.moneyflow_fetcher._request_json") as mock_req:
                mock_req.return_value = (None, "sector_flow_ranking:request_error:ConnectionError")
                result = fetcher.get_sector_flow("600519")
        self.assertEqual(result["status"], "not_supported")
        self.assertIn("sector_flow_ranking:request_error:ConnectionError", result["errors"])


class TestGetNorthboundFlow(unittest.TestCase):
    """北向资金解析。"""

    def test_total_and_holding_parsed(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        # s2n 是字符串列表，最后一项是最新数据
        rt_resp = MagicMock()
        rt_resp.json.return_value = {
            "rc": 0,
            "data": {"s2n": [
                "2025-01-01 09:30,1000000.0,500000.0,1500000.0,...",
                "2025-01-01 15:00,2000000.0,800000.0,2800000.0,...",
            ]},
        }
        rt_resp.raise_for_status.return_value = None

        holding_resp = MagicMock()
        holding_resp.json.return_value = {
            "rc": 0,
            "data": {"f100": 50000000.0, "f101": 8000000000.0, "f102": 6.5},
        }
        holding_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, holding_resp]
            result = fetcher.get_northbound_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_flow"]["total_net_buy"], 2800000.0)
        self.assertEqual(result["total_flow"]["sh_connect_net_buy"], 2000000.0)
        self.assertEqual(result["total_flow"]["sz_connect_net_buy"], 800000.0)
        self.assertEqual(result["stock_holding"]["shares"], 50000000.0)
        self.assertEqual(result["stock_holding"]["holding_pct"], 6.5)

    def test_no_data_returns_not_supported(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch("data_provider.moneyflow_fetcher._request_json") as mock_req:
            mock_req.return_value = (None, "northbound_total:timeout")
            result = fetcher.get_northbound_flow("600519")
        self.assertEqual(result["status"], "not_supported")


class TestGetCapitalFlowAggregate(unittest.TestCase):
    """综合入口：fail-open 合并。"""

    def test_one_submodule_ok_merges_to_ok(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch.object(fetcher, "get_stock_flow") as mock_stock, \
             patch.object(fetcher, "get_sector_flow") as mock_sector, \
             patch.object(fetcher, "get_northbound_flow") as mock_nb:
            mock_stock.return_value = {"status": "ok", "stock_flow": {"main_net_inflow": 100}, "source_chain": ["a"], "errors": []}
            mock_sector.return_value = {"status": "not_supported", "sector_rankings": {"top": [], "bottom": []}, "stock_industry": {}, "source_chain": [], "errors": ["e1"]}
            mock_nb.return_value = {"status": "partial", "total_flow": {"total_net_buy": 1}, "stock_holding": {}, "source_chain": ["b"], "errors": []}

            result = fetcher.get_capital_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stock_flow"], {"main_net_inflow": 100})
        self.assertEqual(result["sector_rankings"], {"top": [], "bottom": []})
        self.assertEqual(result["northbound"]["total_flow"], {"total_net_buy": 1})
        self.assertIn("a", result["source_chain"])
        self.assertIn("b", result["source_chain"])
        self.assertIn("e1", result["errors"])
        self.assertIn("duration_ms", result)

    def test_all_submodules_not_supported(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch.object(fetcher, "get_stock_flow") as mock_stock, \
             patch.object(fetcher, "get_sector_flow") as mock_sector, \
             patch.object(fetcher, "get_northbound_flow") as mock_nb:
            mock_stock.return_value = {"status": "not_supported", "stock_flow": {}, "source_chain": [], "errors": ["e1"]}
            mock_sector.return_value = {"status": "not_supported", "sector_rankings": {"top": [], "bottom": []}, "stock_industry": {}, "source_chain": [], "errors": ["e2"]}
            mock_nb.return_value = {"status": "not_supported", "total_flow": {}, "stock_holding": {}, "source_chain": [], "errors": ["e3"]}

            result = fetcher.get_capital_flow("600519")

        self.assertEqual(result["status"], "not_supported")
        self.assertEqual(len(result["errors"]), 3)


class TestAkshareAdapterIntegration(unittest.TestCase):
    """验证 AkshareFundamentalAdapter.get_capital_flow 主路径优先 + 兜底。"""

    def test_moneyflow_primary_success_skips_akshare(self):
        """MoneyFlowFetcher 返回 ok/partial 时应直接返回，不调用 akshare。"""
        from data_provider.fundamental_adapter import AkshareFundamentalAdapter

        adapter = AkshareFundamentalAdapter()
        mf_result = {
            "status": "ok",
            "stock_flow": {"main_net_inflow": 999},
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": ["moneyflow:stock_flow_realtime"],
            "errors": [],
        }

        with patch("src.config.get_config") as mock_cfg, \
             patch("data_provider.moneyflow_fetcher.MoneyFlowFetcher") as mock_fetcher_cls:
            mock_cfg.return_value.moneyflow_api_enabled = True
            mock_cfg.return_value.moneyflow_api_timeout_seconds = 4.0
            mock_fetcher_cls.return_value.get_capital_flow.return_value = mf_result

            # _call_df_candidates 应该不被调用（如果被调用说明没走主路径）
            with patch.object(adapter, "_call_df_candidates") as mock_akshare:
                result = adapter.get_capital_flow("600519")
                mock_akshare.assert_not_called()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stock_flow"], {"main_net_inflow": 999})

    def test_moneyflow_failure_falls_back_to_akshare(self):
        """MoneyFlowFetcher 全部失败时应走 akshare 兜底。"""
        from data_provider.fundamental_adapter import AkshareFundamentalAdapter
        import pandas as pd

        adapter = AkshareFundamentalAdapter()
        mf_result = {
            "status": "not_supported",
            "stock_flow": {},
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": [],
            "errors": ["stock_flow_realtime:timeout"],
        }

        with patch("src.config.get_config") as mock_cfg, \
             patch("data_provider.moneyflow_fetcher.MoneyFlowFetcher") as mock_fetcher_cls, \
             patch.object(adapter, "_call_df_candidates") as mock_candidates:
            mock_cfg.return_value.moneyflow_api_enabled = True
            mock_cfg.return_value.moneyflow_api_timeout_seconds = 4.0
            mock_fetcher_cls.return_value.get_capital_flow.return_value = mf_result
            # 第一次 _call_df_candidates（stock_df）返回空 DataFrame
            mock_candidates.side_effect = [
                (None, None, ["stock_individual_fund_flow:ImportError"]),
                (None, None, ["stock_sector_fund_flow_rank:ImportError"]),
            ]

            result = adapter.get_capital_flow("600519")

        self.assertEqual(result["status"], "not_supported")
        # 应包含 moneyflow 错误 + akshare 错误
        self.assertTrue(any("stock_flow_realtime:timeout" in e for e in result["errors"]))
        self.assertTrue(any("ImportError" in e for e in result["errors"]))

    def test_moneyflow_disabled_uses_akshare_only(self):
        """moneyflow_api_enabled=False 时应完全跳过 MoneyFlowFetcher。"""
        from data_provider.fundamental_adapter import AkshareFundamentalAdapter

        adapter = AkshareFundamentalAdapter()

        with patch("src.config.get_config") as mock_cfg, \
             patch("data_provider.moneyflow_fetcher.MoneyFlowFetcher") as mock_fetcher_cls, \
             patch.object(adapter, "_call_df_candidates") as mock_candidates:
            mock_cfg.return_value.moneyflow_api_enabled = False
            mock_candidates.side_effect = [
                (None, None, ["akshare_error"]),
                (None, None, ["akshare_error"]),
            ]

            result = adapter.get_capital_flow("600519")

        # MoneyFlowFetcher 不应被实例化
        mock_fetcher_cls.assert_not_called()
        self.assertEqual(result["status"], "not_supported")
        self.assertIn("moneyflow_api_disabled", result["errors"])


class TestRealtimeFieldsKlineFallback(unittest.TestCase):
    """实时接口 f66/f72/f78/f84 返回 None 时用 kline 最新日兜底。"""

    def _make_realtime_resp(self, data_dict):
        resp = MagicMock()
        resp.json.return_value = {"rc": 0, "data": data_dict}
        resp.raise_for_status.return_value = None
        return resp

    def _make_kline_resp(self, klines_list):
        resp = MagicMock()
        resp.json.return_value = {"rc": 0, "data": {"klines": klines_list}}
        resp.raise_for_status.return_value = None
        return resp

    def test_super_large_falls_back_to_kline_latest(self):
        """realtime 返回 None 时，super_large/large/medium/small 用 kline 最新日兜底。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        # realtime 仅返回 f62 主力净流入，f66/f72/f78/f84 缺失
        rt_resp = self._make_realtime_resp({"f62": 1000000.0, "f184": 5.2})
        # kline 最新日：main=100000, super_large=80000, large=30000, medium=-10000, small=-20000
        klines = [
            "2025-01-0X,100000.0,-20000.0,-10000.0,30000.0,80000.0,0.5"
            for _ in range(5)
        ]
        klines = [f"2025-01-{i:02d},100000.0,-20000.0,-10000.0,30000.0,80000.0,0.5" for i in range(1, 6)]
        kl_resp = self._make_kline_resp(klines)

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, kl_resp]
            result = fetcher.get_stock_flow("600519")

        sf = result["stock_flow"]
        # 实时返回 None 的字段应用 kline 最新日数据兜底
        self.assertEqual(sf["super_large_net_inflow"], 80000.0)
        self.assertEqual(sf["large_net_inflow"], 30000.0)
        self.assertEqual(sf["medium_net_inflow"], -10000.0)
        self.assertEqual(sf["small_net_inflow"], -20000.0)
        # main_net_inflow 实时已返回，不应被覆盖
        self.assertEqual(sf["main_net_inflow"], 1000000.0)
        self.assertEqual(sf["inflow_5d"], 500000.0)  # 5 × 100000

    def test_main_net_inflow_also_falls_back_when_realtime_returns_none(self):
        """realtime f62 也返回 None 时，main_net_inflow 用 kline 最新日兜底。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        # realtime 完全空（数据缺失场景）
        rt_resp = self._make_realtime_resp({})
        klines = [f"2025-01-{i:02d},50000.0,-10000.0,-5000.0,15000.0,40000.0,0.3" for i in range(1, 4)]
        kl_resp = self._make_kline_resp(klines)

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, kl_resp]
            result = fetcher.get_stock_flow("600519")

        sf = result["stock_flow"]
        self.assertEqual(sf["main_net_inflow"], 50000.0)
        self.assertEqual(sf["super_large_net_inflow"], 40000.0)
        self.assertEqual(sf["large_net_inflow"], 15000.0)
        self.assertEqual(sf["medium_net_inflow"], -5000.0)
        self.assertEqual(sf["small_net_inflow"], -10000.0)

    def test_extract_klines_parses_pct_fields(self):
        """_extract_klines 解析 f57-f61 占比字段（main/small/medium/large/super_large pct）。"""
        payload = {
            "rc": 0,
            "data": {
                "klines": [
                    # date, main, small, medium, large, super_large,
                    # main_pct, small_pct, medium_pct, large_pct, super_large_pct
                    "2025-01-01,100000.0,-20000.0,-10000.0,30000.0,80000.0,0.5,-0.1,-0.05,0.15,0.4",
                ]
            },
        }
        klines = MoneyFlowFetcher._extract_klines(payload)
        self.assertEqual(len(klines), 1)
        entry = klines[0]
        self.assertEqual(entry["date"], "2025-01-01")
        self.assertEqual(entry["main_net_inflow"], 100000.0)
        self.assertEqual(entry["small_net_inflow"], -20000.0)
        self.assertEqual(entry["medium_net_inflow"], -10000.0)
        self.assertEqual(entry["large_net_inflow"], 30000.0)
        self.assertEqual(entry["super_large_net_inflow"], 80000.0)
        self.assertEqual(entry["main_net_inflow_pct"], 0.5)
        self.assertEqual(entry["small_net_inflow_pct"], -0.1)
        self.assertEqual(entry["medium_net_inflow_pct"], -0.05)
        self.assertEqual(entry["large_net_inflow_pct"], 0.15)
        self.assertEqual(entry["super_large_net_inflow_pct"], 0.4)

    def test_extract_klines_handles_short_string_without_pct(self):
        """_extract_klines 对仅有 6 字段（无占比）的旧数据保持向后兼容。"""
        payload = {
            "rc": 0,
            "data": {
                "klines": [
                    "2025-01-01,100000.0,-20000.0,-10000.0,30000.0,80000.0",
                ]
            },
        }
        klines = MoneyFlowFetcher._extract_klines(payload)
        self.assertEqual(len(klines), 1)
        entry = klines[0]
        self.assertEqual(entry["main_net_inflow"], 100000.0)
        self.assertNotIn("main_net_inflow_pct", entry)  # 旧数据无占比字段

    def test_main_net_inflow_marker_value_falls_back_to_kline(self):
        """realtime f62 返回 1 元占位符时，main_net_inflow 用 kline 最新日兜底。

        复现真实 API 行为：EastMoney stock/get 在缺数据时 f62=1 而非 None，
        与 main_net_inflow_pct（如 6.33%）严重不一致。
        """
        fetcher = MoneyFlowFetcher(enabled=True)
        # realtime 返回 f62=1.0（占位符）+ f184=6.33（占比正常）
        rt_resp = self._make_realtime_resp({"f62": 1.0, "f184": 6.33})
        # kline 最新日 main=2.5亿元（真实主力净流入）+ 完整占比字段
        klines = [
            f"2025-01-{i:02d},250000000.0,-30000000.0,-10000000.0,80000000.0,210000000.0,6.33,-0.8,-0.3,2.1,5.3"
            for i in range(1, 4)
        ]
        kl_resp = self._make_kline_resp(klines)

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, kl_resp]
            result = fetcher.get_stock_flow("600519")

        sf = result["stock_flow"]
        # main_net_inflow 应被 kline 兜底为 2.5亿元，而非保留 1.0
        self.assertEqual(sf["main_net_inflow"], 250000000.0)
        # main_net_inflow_pct 应保留 realtime 的 6.33
        self.assertEqual(sf["main_net_inflow_pct"], 6.33)

    def test_pct_fields_fall_back_to_kline_latest(self):
        """realtime *_pct 为 None 时，从 kline 最新日 f57-f61 派生占比。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        # realtime 仅返回金额字段，占比全为 None（缺数据）
        rt_resp = self._make_realtime_resp({
            "f62": 1000000.0,
            # f184/f69/f75/f81/f87 缺失
            "f66": 800000.0,
            "f72": 200000.0,
            "f78": -300000.0,
            "f84": -700000.0,
        })
        # kline 最新日：金额 + 占比都有
        klines = [
            f"2025-01-{i:02d},1000000.0,-700000.0,-300000.0,200000.0,800000.0,5.0,-3.5,-1.5,1.0,4.0"
            for i in range(1, 4)
        ]
        kl_resp = self._make_kline_resp(klines)

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, kl_resp]
            result = fetcher.get_stock_flow("600519")

        sf = result["stock_flow"]
        # *_pct 应从 kline 最新日派生
        self.assertEqual(sf["main_net_inflow_pct"], 5.0)
        self.assertEqual(sf["super_large_net_inflow_pct"], 4.0)
        self.assertEqual(sf["large_net_inflow_pct"], 1.0)
        self.assertEqual(sf["medium_net_inflow_pct"], -1.5)
        self.assertEqual(sf["small_net_inflow_pct"], -3.5)
        # 金额字段保留 realtime（realtime 已返回非 None 值）
        self.assertEqual(sf["main_net_inflow"], 1000000.0)
        self.assertEqual(sf["super_large_net_inflow"], 800000.0)


class TestConnectionErrorRetry(unittest.TestCase):
    """_request_json 对 ConnectionError 单次重试。"""

    def test_connection_error_retried_once_then_succeeds(self):
        """首次 ConnectionError 后重试一次成功。"""
        import requests
        session = MagicMock()
        success_resp = MagicMock()
        success_resp.json.return_value = {"rc": 0, "data": {"foo": "bar"}}
        success_resp.raise_for_status.return_value = None
        session.get.side_effect = [
            requests.ConnectionError("conn refused"),
            success_resp,
        ]

        with patch("data_provider.moneyflow_fetcher.time.sleep") as mock_sleep:
            data, err = _request_json(session, "http://test", {}, 1.0, "test_block")

        self.assertIsNone(err)
        self.assertEqual(data, {"rc": 0, "data": {"foo": "bar"}})
        # 应该 sleep 一次（重试间隔）
        mock_sleep.assert_called_once()
        # 应该调用两次（首次失败 + 重试成功）
        self.assertEqual(session.get.call_count, 2)

    def test_connection_error_retried_once_still_fails(self):
        """两次都 ConnectionError 时返回错误，不继续重试。"""
        import requests
        session = MagicMock()
        session.get.side_effect = [
            requests.ConnectionError("conn refused 1"),
            requests.ConnectionError("conn refused 2"),
        ]

        with patch("data_provider.moneyflow_fetcher.time.sleep"):
            data, err = _request_json(session, "http://test", {}, 1.0, "test_block")

        self.assertIsNone(data)
        self.assertIn("test_block:request_error:ConnectionError", err)
        self.assertEqual(session.get.call_count, 2)

    def test_timeout_not_retried(self):
        """Timeout 不触发重试，避免超时分支放大延迟。"""
        import requests
        session = MagicMock()
        session.get.side_effect = requests.Timeout("slow")

        with patch("data_provider.moneyflow_fetcher.time.sleep") as mock_sleep:
            data, err = _request_json(session, "http://test", {}, 1.0, "test_block")

        self.assertIsNone(data)
        self.assertEqual(err, "test_block:timeout")
        mock_sleep.assert_not_called()
        self.assertEqual(session.get.call_count, 1)

    def test_retry_disabled_via_param(self):
        """retry_on_conn_error=False 时不重试。"""
        import requests
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("conn refused")

        with patch("data_provider.moneyflow_fetcher.time.sleep") as mock_sleep:
            data, err = _request_json(
                session, "http://test", {}, 1.0, "test_block",
                retry_on_conn_error=False,
            )

        self.assertIsNone(data)
        self.assertIn("test_block:request_error:ConnectionError", err)
        mock_sleep.assert_not_called()
        self.assertEqual(session.get.call_count, 1)


class TestSectorFlowDiagnostics(unittest.TestCase):
    """sector_rankings 为空时应有诊断错误。"""

    def test_empty_data_returns_diagnostic_error(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        ranking_resp = MagicMock()
        # rc=0 但 data=None（API 业务级无数据）
        ranking_resp.json.return_value = {"rc": 0, "data": None}
        ranking_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.return_value = ranking_resp
            result = fetcher.get_sector_flow("600519")

        self.assertEqual(result["status"], "not_supported")
        self.assertTrue(any("sector_flow_ranking:empty_data" in e for e in result["errors"]))

    def test_empty_diff_returns_diagnostic_error(self):
        fetcher = MoneyFlowFetcher(enabled=True)
        ranking_resp = MagicMock()
        # rc=0，data 存在但 diff 为空列表
        ranking_resp.json.return_value = {"rc": 0, "data": {"diff": []}}
        ranking_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.return_value = ranking_resp
            result = fetcher.get_sector_flow("600519")

        self.assertEqual(result["status"], "not_supported")
        self.assertTrue(any("sector_flow_ranking:empty_diff" in e for e in result["errors"]))


class TestCapitalFlowSequential(unittest.TestCase):
    """get_capital_flow 默认串行路径（moneyflow_parallel_enabled=False）。"""

    def test_sequential_aggregates_three_submethods(self):
        """3 个子方法均正常返回时 merged 完整聚合。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch.object(fetcher, "get_stock_flow") as mock_stock, \
             patch.object(fetcher, "get_sector_flow") as mock_sector, \
             patch.object(fetcher, "get_northbound_flow") as mock_nb, \
             patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg:
            mock_cfg.return_value.moneyflow_parallel_enabled = False
            mock_stock.return_value = {
                "status": "ok",
                "stock_flow": {"main_net_inflow": 100, "super_large_net_inflow": 50},
                "source_chain": ["a"],
                "errors": [],
            }
            mock_sector.return_value = {
                "status": "ok",
                "sector_rankings": {"top": [{"name": "半导体"}], "bottom": []},
                "stock_industry": {"name": "酿酒", "rank_in_flow": 5, "total_industries": 90},
                "source_chain": ["b"],
                "errors": [],
            }
            mock_nb.return_value = {
                "status": "partial",
                "total_flow": {"total_net_buy": 200},
                "stock_holding": {},
                "source_chain": ["c"],
                "errors": [],
            }

            result = fetcher.get_capital_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stock_flow"]["main_net_inflow"], 100)
        self.assertEqual(result["stock_industry"]["name"], "酿酒")
        self.assertEqual(result["northbound"]["total_flow"]["total_net_buy"], 200)
        self.assertIn("a", result["source_chain"])
        self.assertIn("b", result["source_chain"])
        self.assertIn("c", result["source_chain"])
        self.assertGreaterEqual(result["duration_ms"], 0)
        mock_stock.assert_called_once_with("600519")
        mock_sector.assert_called_once()
        mock_nb.assert_called_once_with("600519")

    def test_sequential_one_submethod_exception_does_not_break_others(self):
        """子方法抛异常时通过 try/except 转为 not_supported，不影响其他子方法。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch.object(fetcher, "get_stock_flow") as mock_stock, \
             patch.object(fetcher, "get_sector_flow") as mock_sector, \
             patch.object(fetcher, "get_northbound_flow") as mock_nb, \
             patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg:
            mock_cfg.return_value.moneyflow_parallel_enabled = False
            mock_stock.side_effect = RuntimeError("unexpected boom")
            mock_sector.return_value = {
                "status": "ok",
                "sector_rankings": {"top": [], "bottom": []},
                "stock_industry": {},
                "source_chain": ["b"],
                "errors": [],
            }
            mock_nb.return_value = {
                "status": "not_supported",
                "total_flow": {},
                "stock_holding": {},
                "source_chain": [],
                "errors": ["e"],
            }

            result = fetcher.get_capital_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("uncaught" in e for e in result["errors"]))
        self.assertTrue(any("e" == e for e in result["errors"]))


class TestCapitalFlowParallel(unittest.TestCase):
    """get_capital_flow opt-in 并行路径（moneyflow_parallel_enabled=True）。"""

    def test_parallel_aggregates_three_submethods(self):
        """并行模式下 3 个子方法均正常返回时 merged 完整聚合。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch.object(fetcher, "get_stock_flow") as mock_stock, \
             patch.object(fetcher, "get_sector_flow") as mock_sector, \
             patch.object(fetcher, "get_northbound_flow") as mock_nb, \
             patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg:
            mock_cfg.return_value.moneyflow_parallel_enabled = True
            mock_stock.return_value = {
                "status": "ok",
                "stock_flow": {"main_net_inflow": 100},
                "source_chain": ["a"],
                "errors": [],
            }
            mock_sector.return_value = {
                "status": "partial",
                "sector_rankings": {"top": [], "bottom": []},
                "stock_industry": {},
                "source_chain": ["b"],
                "errors": [],
            }
            mock_nb.return_value = {
                "status": "not_supported",
                "total_flow": {},
                "stock_holding": {},
                "source_chain": [],
                "errors": [],
            }

            result = fetcher.get_capital_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stock_flow"]["main_net_inflow"], 100)
        # source_chain 顺序在并行模式下也是 stock + sector + nb
        self.assertEqual(result["source_chain"], ["a", "b"])

    def test_parallel_one_submethod_exception_does_not_break_others(self):
        """并行模式下子方法抛异常时通过 _safe_call 转为 not_supported。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch.object(fetcher, "get_stock_flow") as mock_stock, \
             patch.object(fetcher, "get_sector_flow") as mock_sector, \
             patch.object(fetcher, "get_northbound_flow") as mock_nb, \
             patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg:
            mock_cfg.return_value.moneyflow_parallel_enabled = True
            mock_stock.side_effect = RuntimeError("unexpected boom")
            mock_sector.return_value = {
                "status": "ok",
                "sector_rankings": {"top": [], "bottom": []},
                "stock_industry": {},
                "source_chain": ["b"],
                "errors": [],
            }
            mock_nb.return_value = {
                "status": "not_supported",
                "total_flow": {},
                "stock_holding": {},
                "source_chain": [],
                "errors": ["e"],
            }

            result = fetcher.get_capital_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("uncaught" in e for e in result["errors"]))


class TestSecidEdgeCases(unittest.TestCase):
    """_to_eastmoney_secid 默认 fallback 与边界条件。"""

    def test_default_fallback_for_unknown_prefix(self):
        """未匹配已知前缀的 6 位数字代码默认按深交所处理。"""
        # 5xx 开头不在 SH 主板/科创板/B 股/可转债规则内，也不在 SZ 已知前缀内
        self.assertEqual(_to_eastmoney_secid("510300"), "0.510300")  # 沪深 300 ETF
        self.assertEqual(_to_eastmoney_secid("159915"), "0.159915")  # 创业板 ETF

    def test_b_share_shanghai(self):
        """上交所 B 股（90x）应映射为 1.code。"""
        self.assertEqual(_to_eastmoney_secid("900901"), "1.900901")


class TestBuildSessionEastMoneyPatch(unittest.TestCase):
    """_build_session 对 eastmoney_patch 的处理。"""

    def test_applies_patch_when_enabled(self):
        """config.enable_eastmoney_patch=True 时调用 eastmoney_patch。"""
        with patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg, \
             patch("src.patches.eastmoney_patch.eastmoney_patch") as mock_patch:
            mock_cfg.return_value.enable_eastmoney_patch = True
            session = __import__(
                "data_provider.moneyflow_fetcher", fromlist=["_build_session"]
            )._build_session()
            mock_patch.assert_called_once()
            self.assertIsNotNone(session.headers.get("User-Agent"))

    def test_swallows_patch_import_error(self):
        """eastmoney_patch 导入异常被吞掉，session 仍返回。"""
        with patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg:
            mock_cfg.return_value.enable_eastmoney_patch = True
            # 触发 ImportError：sys.modules 中 eastmoney_patch 指向一个会抛异常的模块
            import sys
            original = sys.modules.get("src.patches.eastmoney_patch")
            try:
                sys.modules["src.patches.eastmoney_patch"] = None  # 触发 ImportError
                session = __import__(
                    "data_provider.moneyflow_fetcher", fromlist=["_build_session"]
                )._build_session()
                self.assertIsNotNone(session)
            finally:
                if original is not None:
                    sys.modules["src.patches.eastmoney_patch"] = original
                else:
                    sys.modules.pop("src.patches.eastmoney_patch", None)

    def test_skip_patch_when_disabled(self):
        """config.enable_eastmoney_patch=False 时不调用 eastmoney_patch。"""
        with patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg, \
             patch("src.patches.eastmoney_patch.eastmoney_patch") as mock_patch:
            mock_cfg.return_value.enable_eastmoney_patch = False
            session = __import__(
                "data_provider.moneyflow_fetcher", fromlist=["_build_session"]
            )._build_session()
            mock_patch.assert_not_called()
            self.assertIsNotNone(session)


class TestEnabledProperty(unittest.TestCase):
    """MoneyFlowFetcher.enabled 属性的 config 驱动路径。"""

    def test_explicit_enabled_takes_precedence(self):
        fetcher = MoneyFlowFetcher(enabled=False)
        self.assertFalse(fetcher.enabled)
        fetcher2 = MoneyFlowFetcher(enabled=True)
        self.assertTrue(fetcher2.enabled)

    def test_uses_config_when_not_explicit(self):
        """_enabled=None 时从 config.moneyflow_api_enabled 读取。"""
        fetcher = MoneyFlowFetcher()  # _enabled=None
        with patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg:
            mock_cfg.return_value.moneyflow_api_enabled = False
            self.assertFalse(fetcher.enabled)
            mock_cfg.return_value.moneyflow_api_enabled = True
            self.assertTrue(fetcher.enabled)

    def test_defaults_to_true_on_config_exception(self):
        """get_config 抛异常时默认返回 True。"""
        fetcher = MoneyFlowFetcher()
        with patch("data_provider.moneyflow_fetcher.get_config", side_effect=RuntimeError("no config")):
            self.assertTrue(fetcher.enabled)

    def test_defaults_to_true_when_attr_missing(self):
        """config 无 moneyflow_api_enabled 属性时默认 True。"""
        fetcher = MoneyFlowFetcher()
        cfg = MagicMock()
        del cfg.moneyflow_api_enabled  # 确保 getattr 走 default
        with patch("data_provider.moneyflow_fetcher.get_config", return_value=cfg):
            self.assertTrue(fetcher.enabled)


class TestExtractKlinesEdgeCases(unittest.TestCase):
    """_extract_klines 边界条件。"""

    def test_none_payload_returns_empty(self):
        self.assertEqual(MoneyFlowFetcher._extract_klines(None), [])

    def test_missing_data_returns_empty(self):
        """payload 中 data 字段缺失时返回空。"""
        self.assertEqual(MoneyFlowFetcher._extract_klines({"rc": 0}), [])
        self.assertEqual(MoneyFlowFetcher._extract_klines({"rc": 0, "data": None}), [])

    def test_non_list_klines_returns_empty(self):
        """klines 不是 list 时返回空。"""
        payload = {"rc": 0, "data": {"klines": "not-a-list"}}
        self.assertEqual(MoneyFlowFetcher._extract_klines(payload), [])

    def test_skips_non_string_items(self):
        """klines 中非字符串项被跳过。"""
        payload = {
            "rc": 0,
            "data": {"klines": [123, None, {"x": 1}, "2025-01-01,100.0,200.0,300.0,400.0,500.0"]},
        }
        result = MoneyFlowFetcher._extract_klines(payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], "2025-01-01")

    def test_skips_short_strings(self):
        """逗号分隔后不足 6 字段的字符串被跳过。"""
        payload = {
            "rc": 0,
            "data": {"klines": ["2025-01-01,100.0", "2025-01-02,100.0,200.0,300.0,400.0,500.0"]},
        }
        result = MoneyFlowFetcher._extract_klines(payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], "2025-01-02")


class TestSumMainInflowEdgeCases(unittest.TestCase):
    """_sum_main_inflow 边界条件。"""

    def test_empty_klines_returns_none(self):
        self.assertIsNone(MoneyFlowFetcher._sum_main_inflow([], days=5))

    def test_all_none_values_returns_none(self):
        """所有 main_net_inflow 都是 None 时返回 None。"""
        klines = [{"main_net_inflow": None}, {"main_net_inflow": None}]
        self.assertIsNone(MoneyFlowFetcher._sum_main_inflow(klines, days=5))

    def test_partial_none_values_sums_only_valid(self):
        """None 值被跳过，只累加有效值。"""
        klines = [
            {"main_net_inflow": 100.0},
            {"main_net_inflow": None},
            {"main_net_inflow": 200.0},
        ]
        # days=5 大于 klines 长度，取全部
        self.assertEqual(MoneyFlowFetcher._sum_main_inflow(klines, days=5), 300.0)


class TestConsecutiveInflowDaysEdgeCases(unittest.TestCase):
    """_consecutive_inflow_days 边界条件。"""

    def test_empty_klines_returns_none(self):
        self.assertIsNone(MoneyFlowFetcher._consecutive_inflow_days([]))

    def test_latest_none_returns_zero(self):
        """最新日 main_net_inflow 为 None 时返回 0。"""
        # klines[-1] 是 latest，None 在末尾
        klines = [{"main_net_inflow": 100.0}, {"main_net_inflow": None}]
        self.assertEqual(MoneyFlowFetcher._consecutive_inflow_days(klines), 0)

    def test_latest_zero_returns_zero(self):
        """最新日 main_net_inflow 为 0 时返回 0。"""
        klines = [{"main_net_inflow": 100.0}, {"main_net_inflow": 0}]
        self.assertEqual(MoneyFlowFetcher._consecutive_inflow_days(klines), 0)

    def test_breaks_on_none_value_in_sequence(self):
        """序列中出现 None 时中断计数。"""
        klines = [
            {"main_net_inflow": 100.0},
            {"main_net_inflow": None},
            {"main_net_inflow": 200.0},  # 这一天是 latest
        ]
        # latest=200.0 (正), 倒序遍历: 200(+) -> None(break) → count=1
        self.assertEqual(MoneyFlowFetcher._consecutive_inflow_days(klines), 1)

    def test_breaks_on_direction_change(self):
        """方向变化时中断计数。"""
        klines = [
            {"main_net_inflow": -100.0},  # 负
            {"main_net_inflow": 100.0},   # 正 (latest)
        ]
        # latest=100.0(正), 倒序: 100(+) -> -100(方向变化 break) → count=1
        self.assertEqual(MoneyFlowFetcher._consecutive_inflow_days(klines), 1)

    def test_negative_direction_counts_negative_days(self):
        """连续净流出为负数。"""
        klines = [
            {"main_net_inflow": -100.0},
            {"main_net_inflow": -200.0},  # latest
        ]
        self.assertEqual(MoneyFlowFetcher._consecutive_inflow_days(klines), -2)


class TestGetSectorFlowEdgeCases(unittest.TestCase):
    """get_sector_flow 边界条件。"""

    def test_invalid_code_returns_not_supported(self):
        """非 A 股代码返回 not_supported 并记录错误。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        result = fetcher.get_sector_flow("AAPL")
        self.assertEqual(result["status"], "not_supported")
        self.assertTrue(any("invalid_a_share_code" in e for e in result["errors"]))

    def test_no_valid_rows_after_filtering(self):
        """所有 row 都不是 dict 时记录 no_valid_rows 错误。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        ranking_resp = MagicMock()
        ranking_resp.json.return_value = {
            "rc": 0,
            "data": {"diff": ["not-a-dict", 123, None]},
        }
        ranking_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.return_value = ranking_resp
            result = fetcher.get_sector_flow("600519")

        self.assertEqual(result["status"], "not_supported")
        self.assertTrue(any("no_valid_rows" in e for e in result["errors"]))

    def test_industry_lookup_error_recorded(self):
        """行业查找接口失败时错误被记录但不影响 sector_rankings。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        ranking_resp = MagicMock()
        ranking_resp.json.return_value = {
            "rc": 0,
            "data": {"diff": [
                {"f12": "BK0475", "f14": "半导体", "f62": 1000000.0},
            ]},
        }
        ranking_resp.raise_for_status.return_value = None
        industry_resp = MagicMock()
        industry_resp.json.side_effect = ValueError("invalid json")
        industry_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [ranking_resp, industry_resp]
            result = fetcher.get_sector_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["sector_rankings"]["top"]), 1)
        self.assertTrue(any("stock_industry_lookup" in e for e in result["errors"]))

    def test_industry_code_and_name_both_empty_skips_stock_industry(self):
        """行业代码与名称均空时不填充 stock_industry。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        ranking_resp = MagicMock()
        ranking_resp.json.return_value = {
            "rc": 0,
            "data": {"diff": [
                {"f12": "BK0475", "f14": "半导体", "f62": 1000000.0},
            ]},
        }
        ranking_resp.raise_for_status.return_value = None
        industry_resp = MagicMock()
        industry_resp.json.return_value = {
            "rc": 0,
            "data": {"f127": "", "f128": ""},  # 均空
        }
        industry_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [ranking_resp, industry_resp]
            result = fetcher.get_sector_flow("600519")

        self.assertEqual(result["status"], "ok")
        # stock_industry 应保持初始空 dict
        self.assertEqual(result["stock_industry"], {})
        # 不应添加 stock_industry_lookup source_chain
        self.assertNotIn("moneyflow:stock_industry_lookup", result["source_chain"])


class TestGetNorthboundFlowEdgeCases(unittest.TestCase):
    """get_northbound_flow 边界条件。"""

    def test_invalid_code_returns_not_supported(self):
        """非 A 股代码返回 not_supported。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        result = fetcher.get_northbound_flow("AAPL")
        self.assertEqual(result["status"], "not_supported")
        self.assertTrue(any("invalid_a_share_code" in e for e in result["errors"]))

    def test_s2n_empty_list_returns_partial_with_holding(self):
        """s2n 为空列表时 total_flow 为空，但 holding 仍可填充。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        rt_resp = MagicMock()
        rt_resp.json.return_value = {"rc": 0, "data": {"s2n": []}}
        rt_resp.raise_for_status.return_value = None
        holding_resp = MagicMock()
        holding_resp.json.return_value = {
            "rc": 0,
            "data": {"f100": 1000.0, "f101": 2000.0, "f102": 3.5},
        }
        holding_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, holding_resp]
            result = fetcher.get_northbound_flow("600519")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total_flow"], {})
        self.assertEqual(result["stock_holding"]["shares"], 1000.0)

    def test_s2n_non_list_returns_partial_with_holding(self):
        """s2n 不是 list 时 total_flow 为空。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        rt_resp = MagicMock()
        rt_resp.json.return_value = {"rc": 0, "data": {"s2n": "not-a-list"}}
        rt_resp.raise_for_status.return_value = None
        holding_resp = MagicMock()
        holding_resp.json.return_value = {
            "rc": 0,
            "data": {"f100": 1000.0, "f101": 2000.0, "f102": 3.5},
        }
        holding_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, holding_resp]
            result = fetcher.get_northbound_flow("600519")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total_flow"], {})

    def test_latest_non_string_skipped(self):
        """s2n 最新项不是字符串时跳过 total_flow。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        rt_resp = MagicMock()
        rt_resp.json.return_value = {"rc": 0, "data": {"s2n": [123, None]}}
        rt_resp.raise_for_status.return_value = None
        holding_resp = MagicMock()
        holding_resp.json.return_value = {
            "rc": 0,
            "data": {"f100": 1000.0, "f101": 2000.0, "f102": 3.5},
        }
        holding_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, holding_resp]
            result = fetcher.get_northbound_flow("600519")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total_flow"], {})

    def test_parts_too_short_skipped(self):
        """s2n 最新项拆分后不足 4 段时 total_flow 为空。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        rt_resp = MagicMock()
        rt_resp.json.return_value = {
            "rc": 0,
            "data": {"s2n": ["2025-01-01,100.0,200.0"]},  # 只有 3 段
        }
        rt_resp.raise_for_status.return_value = None
        holding_resp = MagicMock()
        holding_resp.json.return_value = {
            "rc": 0,
            "data": {"f100": 1000.0, "f101": 2000.0, "f102": 3.5},
        }
        holding_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, holding_resp]
            result = fetcher.get_northbound_flow("600519")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total_flow"], {})

    def test_holding_error_recorded(self):
        """holding 接口失败时错误被记录但不影响 total_flow。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        rt_resp = MagicMock()
        rt_resp.json.return_value = {
            "rc": 0,
            "data": {"s2n": ["2025-01-01 15:00,100.0,200.0,300.0"]},
        }
        rt_resp.raise_for_status.return_value = None
        holding_resp = MagicMock()
        holding_resp.json.side_effect = ValueError("invalid json")
        holding_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, holding_resp]
            result = fetcher.get_northbound_flow("600519")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total_flow"]["total_net_buy"], 300.0)
        self.assertEqual(result["stock_holding"], {})
        self.assertTrue(any("northbound_holding" in e for e in result["errors"]))

    def test_all_holding_fields_none_skipped(self):
        """holding 字段全为 None 时不填充 stock_holding。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        rt_resp = MagicMock()
        rt_resp.json.return_value = {
            "rc": 0,
            "data": {"s2n": ["2025-01-01 15:00,100.0,200.0,300.0"]},
        }
        rt_resp.raise_for_status.return_value = None
        holding_resp = MagicMock()
        holding_resp.json.return_value = {
            "rc": 0,
            "data": {"f100": None, "f101": None, "f102": None},
        }
        holding_resp.raise_for_status.return_value = None

        with patch("data_provider.moneyflow_fetcher._build_session") as mock_sess:
            mock_sess.return_value.get.side_effect = [rt_resp, holding_resp]
            result = fetcher.get_northbound_flow("600519")

        # 只有 total_flow，无 holding → partial
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stock_holding"], {})
        self.assertEqual(result["total_flow"]["total_net_buy"], 300.0)


class TestRunSubmethodsSequentialExceptionIsolation(unittest.TestCase):
    """_run_submethods_sequential 中 sector/nb 异常被独立捕获。"""

    def test_sector_exception_does_not_break_stock_and_nb(self):
        """sector 抛异常时 stock 和 nb 仍正常返回。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch.object(fetcher, "get_stock_flow") as mock_stock, \
             patch.object(fetcher, "get_sector_flow") as mock_sector, \
             patch.object(fetcher, "get_northbound_flow") as mock_nb, \
             patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg:
            mock_cfg.return_value.moneyflow_parallel_enabled = False
            mock_stock.return_value = {
                "status": "ok", "stock_flow": {"main_net_inflow": 1},
                "source_chain": ["a"], "errors": [],
            }
            mock_sector.side_effect = RuntimeError("sector boom")
            mock_nb.return_value = {
                "status": "ok", "total_flow": {"total_net_buy": 1},
                "stock_holding": {}, "source_chain": ["c"], "errors": [],
            }

            result = fetcher.get_capital_flow("600519")

        # stock + nb 均 ok → merged=ok
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("sector_flow:uncaught" in e for e in result["errors"]))

    def test_nb_exception_does_not_break_stock_and_sector(self):
        """nb 抛异常时 stock 和 sector 仍正常返回。"""
        fetcher = MoneyFlowFetcher(enabled=True)
        with patch.object(fetcher, "get_stock_flow") as mock_stock, \
             patch.object(fetcher, "get_sector_flow") as mock_sector, \
             patch.object(fetcher, "get_northbound_flow") as mock_nb, \
             patch("data_provider.moneyflow_fetcher.get_config") as mock_cfg:
            mock_cfg.return_value.moneyflow_parallel_enabled = False
            mock_stock.return_value = {
                "status": "ok", "stock_flow": {"main_net_inflow": 1},
                "source_chain": ["a"], "errors": [],
            }
            mock_sector.return_value = {
                "status": "ok",
                "sector_rankings": {"top": [{"name": "x"}], "bottom": []},
                "stock_industry": {}, "source_chain": ["b"], "errors": [],
            }
            mock_nb.side_effect = RuntimeError("nb boom")

            result = fetcher.get_capital_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("northbound:uncaught" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()

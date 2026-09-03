# -*- coding: utf-8 -*-
"""网络冒烟测试（E4）：marker=network，默认排除（-m "not network"）

用途：CI network-smoke / 手动 `pytest -m network` 观测数据源连通性。
每个用例只做一次最小请求；数据源故障时在此暴露，不拖垮离线套件。
"""
from __future__ import annotations

import pytest

from quant_research.data.clients.akshare_client import AkshareClient


pytestmark = pytest.mark.network


def test_akshare_etf_daily_smoke():
    """ETF 日线单标的 5 个交易日：连通性 + 列契约"""
    client = AkshareClient()
    df = client.get_etf_daily("510300.SH", "2026-08-20", "2026-08-31")
    assert not df.empty
    for col in ("date", "open", "high", "low", "close", "volume"):
        assert col in df.columns
    assert (df["close"] > 0).all()


def test_akshare_convertible_value_analysis_smoke():
    """转债价值分析（PIT 增量真源，update_pit_tail 同源）：连通性 + 列契约

    注意：AkshareClient.get_convertible_bond_daily 委托给股票 API，对
    转债代码不对口（已知局限）；本用例直接对准 PIT 增量实际消费的
    bond_zh_cov_value_analysis 接口。
    """
    import akshare as ak

    df = ak.bond_zh_cov_value_analysis(symbol="113062")
    assert df is not None and not df.empty
    for col in ("日期", "收盘价", "转股溢价率"):
        assert col in df.columns

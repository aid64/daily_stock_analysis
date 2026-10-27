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


def test_eastmoney_convertible_value_analysis_smoke():
    """转债价值分析（PIT 增量真源）：连通性 + 字段契约。

    走 update_pit_tail._fetch_history 的东财直连路径——akshare 的
    bond_zh_cov_value_analysis 按固定列位重命名，东财加列后会直接
    ValueError（2026-10 实际故障）。
    """
    from scripts.update_pit_tail import _fetch_history

    df = _fetch_history("113062")
    assert df is not None and not df.empty
    for col in ("date", "close", "premium_rate"):
        assert col in df.columns
    assert (df["close"] > 0).all()

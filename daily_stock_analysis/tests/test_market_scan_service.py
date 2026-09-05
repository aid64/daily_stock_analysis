# -*- coding: utf-8 -*-
"""② 数据地基测试：mandate 读取 / 快照落库幂等 / 规则漏斗各层"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.services import market_scan_service as mss
from src.services.market_scan_service import (
    fetch_and_store_snapshot,
    load_mandate,
    load_snapshot,
    run_funnel,
    snapshot_path,
)


def _snapshot(n_st: int = 3, n_low_liq: int = 2, n_bad_pe: int = 2):
    """合成全市场快照：500 有效 + ST + 低流动 + 极端 PE"""
    rows = []
    for i in range(500):
        rows.append({
            "code": f"{600000 + i}.SH", "name": f"正常股{i}",
            "close": 10.0 + i * 0.1, "volume": 1e6, "amount": 5e7 + i * 1e6,
            "pe": 10.0 + (i % 40), "pb": 1.5,
            "market_cap": 5e9, "turnover_rate": 2.0, "volume_ratio": 1.0,
        })
    for i in range(n_st):
        rows.append({"code": f"{900000 + i}.SZ", "name": f"ST板块{i}",
                     "close": 5.0, "volume": 1e6, "amount": 9e7,
                     "pe": 20.0, "pb": 1.0, "market_cap": 8e9})
    for i in range(n_low_liq):
        rows.append({"code": f"{800000 + i}.BJ", "name": f"低流动{i}",
                     "close": 3.0, "volume": 1e3, "amount": 5e5,
                     "pe": 15.0, "pb": 0.8, "market_cap": 3e9})
    for i in range(n_bad_pe):
        rows.append({"code": f"{700000 + i}.SH", "name": f"极端估值{i}",
                     "close": 20.0, "volume": 1e6, "amount": 8e7,
                     "pe": 300.0 if i == 0 else -5.0, "pb": 2.0,
                     "market_cap": 6e9})
    return pd.DataFrame(rows)


@pytest.fixture()
def scan_dir(tmp_path, monkeypatch):
    d = tmp_path / "scan"
    monkeypatch.setattr(mss, "_PROJECT_SCAN_DIR", d)
    return d


class TestMandate:
    def test_load_repo_mandate(self):
        mandate = load_mandate()
        assert mandate["capital"]["base"] == 10000
        assert mandate["filters"]["min_amount"] == 30000000
        assert mandate["risk"]["allocation"]["stock"] == 0.4

    def test_missing_mandate_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mss, "_MANDATE_PATH", tmp_path / "nope.yaml")
        mandate = load_mandate()
        assert mandate["filters"]["min_amount"] == mss.DEFAULT_FUNNEL_PARAMS["min_amount"]


class TestSnapshotStore:
    def test_store_and_load_idempotent(self, scan_dir, monkeypatch):
        df = _snapshot()
        fetcher = type("F", (), {"get_full_market_snapshot": lambda self: df})()
        day = date(2026, 9, 7)

        out1 = fetch_and_store_snapshot(day, fetcher=fetcher)
        assert len(out1) == len(df)
        # 幂等：当日已存在不再调 fetcher（第二次用会抛的假 fetcher 验证）
        calls = {"n": 0}

        class _Counting:
            def get_full_market_snapshot(self):
                calls["n"] += 1
                raise AssertionError("should not fetch again")

        out2 = fetch_and_store_snapshot(day, fetcher=_Counting())
        assert len(out2) == len(df) and calls["n"] == 0
        assert load_snapshot(day) is not None
        assert snapshot_path(day).exists()


class TestFunnel:
    def test_funnel_layers(self, scan_dir):
        df = _snapshot()
        result = run_funnel(df)
        names = [s["name"] for s in result.steps]
        assert names[0] == "全市场" and "剔除ST/退市" in names
        # ST 与低流动被剔除
        codes = set(result.candidates["code"])
        assert not any(c.startswith("9000") for c in codes)
        assert not any(c.startswith("8000") for c in codes)
        # 极端 PE（300/-5）被剔除，PE 缺失保留
        assert not any(c.startswith("7000") for c in codes)
        # 截断至目标规模
        assert len(result.candidates) <= 300
        assert result.step_log.startswith("全市场: ")

    def test_funnel_requires_snapshot(self, scan_dir):
        with pytest.raises(ValueError, match="无可用快照"):
            run_funnel(None)

    def test_pe_missing_rows_kept(self, scan_dir):
        df = _snapshot(n_bad_pe=0)
        df.loc[df.index[:10], "pe"] = np.nan
        df.loc[df.index[:10], "amount"] = 9e7 + np.arange(10) * 1e6  # 高成交额避开截断
        mandate = {"filters": {**mss.DEFAULT_FUNNEL_PARAMS, "target_pool_size": 1000}}
        result = run_funnel(df, mandate=mandate)
        # 500 只正常股全保留（含 10 只 NaN PE；无 ST/低流动/极端 PE 样本）
        assert len(result.candidates) == 500

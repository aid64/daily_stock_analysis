# -*- coding: utf-8 -*-
"""F5 核心层单测：runner（增量）/ backtest.adapter / core.panel / core.factors

与 test_walk_forward.py（脚本级）、test_two_stage.py（Panel 冒烟）互补；
全部合成数据，不依赖网络。
"""
from __future__ import annotations

import datetime
import hashlib
import types

import numpy as np
import pandas as pd
import pytest

from quant_research import runner
from quant_research.backtest.adapter import BTMarketView
from quant_research.core.factors import (
    FACTOR_REGISTRY,
    compute_factor,
    evaluate_factor,
    ic_series,
    register_factor,
)
from quant_research.core.panel import Panel


# ── runner（增量：test_walk_forward 已覆盖 parse_grid_spec/folds 基础面）──


class _StubResult:
    annual_return = 0.15
    max_drawdown = 0.10
    sharpe = 1.2
    calmar = 1.5
    total_return = 0.30
    total_trades = 12
    closed_trades = 9
    completed_orders = 15
    turnover = 3.4
    final_value = 115000.0


class TestRunnerHelpers:
    def test_grid_combinations_budget(self):
        grid = {"lookback": [10, 20, 30], "top_k": [1, 2, 3]}
        combos = runner.grid_combinations(grid, max_combos=9)
        assert len(combos) == 9
        assert combos[0] == {"lookback": 10, "top_k": 1}
        assert combos[-1] == {"lookback": 30, "top_k": 3}
        with pytest.raises(ValueError, match="超出预算"):
            runner.grid_combinations(grid, max_combos=8)

    def test_warmup_days_windows_and_ma(self):
        # ma_filter 默认下限 250（动量最长窗口）→ 最短 warmup 为 425
        assert runner.warmup_days({"lookback": 60}) == int(250 * 1.7)
        assert runner.warmup_days({"mom_windows": (60, 120)}) == int(250 * 1.7)
        assert runner.warmup_days({"mom_windows": 120}) == int(250 * 1.7)  # 标量也接受
        assert runner.warmup_days({"lookback": 60, "ma_filter": 300}) == int(300 * 1.7)
        assert runner.warmup_days({"mom_windows": (60, 120), "ma_filter": 100}) == int(120 * 1.7)

    def test_warmup_days_mwu_floor_and_experts(self):
        # 250 为内置下限（动量最长窗口）
        assert runner.warmup_days_mwu({}) == int(250 * 1.7)
        spec = {"momentum": {"ma_filter": 300}, "mean_revert": {"lookback": 200}}
        assert runner.warmup_days_mwu(spec) == int(300 * 1.7)
        spec_none = {"momentum": None}  # 非法专家配置不参与 max
        assert runner.warmup_days_mwu(spec_none) == int(250 * 1.7)

    def test_metric_row_reports_trade_scope_triple(self):
        """F3 契约：metric_row 同时携带 total_trades/closed_trades/completed_orders"""
        row = runner.metric_row(_StubResult(), fold=2, segment="test", params={"lookback": 60})
        assert row["fold"] == 2 and row["segment"] == "test"
        assert row["total_trades"] == 12
        assert row["closed_trades"] == 9
        assert row["completed_orders"] == 15
        assert row["turnover"] == 3.4
        assert row["final_value"] == 115000.0

    def test_select_training_result_drawdown_gate_and_fallback(self):
        good = {"annual_return": 0.10, "max_drawdown": 0.20, "sharpe": 1.0}
        wild = {"annual_return": 0.90, "max_drawdown": 0.60, "sharpe": 2.0}
        assert runner.select_training_result([good, wild]) is good  # 回撤门排除 wild
        assert runner.select_training_result([wild]) is wild        # 全超时回退全集

    def test_select_training_result_tiebreak_by_sharpe(self):
        a = {"annual_return": 0.10, "max_drawdown": 0.20, "sharpe": 1.0}
        b = {"annual_return": 0.10, "max_drawdown": 0.20, "sharpe": 1.5}
        assert runner.select_training_result([a, b]) is b

    def test_slice_feeds_keeps_boundary_and_drops_empty(self):
        dates = pd.bdate_range("2024-01-01", periods=10)
        df = pd.DataFrame({"date": dates, "close": np.arange(10.0)})
        out = runner.slice_feeds({"A.SH": df}, "2024-01-03", "2024-01-09")
        assert len(out["A.SH"]) == 5
        assert out["A.SH"]["date"].iloc[0] == pd.Timestamp("2024-01-03")
        assert out["A.SH"]["date"].iloc[-1] == pd.Timestamp("2024-01-09")
        assert runner.slice_feeds({"B.SH": df}, "2030-01-01", "2030-12-31") == {}

    def test_sha256_file_matches_hashlib(self, tmp_path):
        payload = b"quant_research" * 4096  # >1KB，验证分块读取
        f1 = tmp_path / "a.bin"
        f1.write_bytes(payload)
        assert runner.sha256_file(f1) == hashlib.sha256(payload).hexdigest()

    def test_latest_snapshot_date_takes_max_across_symbols(self):
        feeds = {
            "A.SH": pd.DataFrame({"date": pd.to_datetime(["2026-08-28", "2026-09-01"])}),
            "B.SH": pd.DataFrame({"date": pd.to_datetime(["2026-08-31"])}),
        }
        assert runner.latest_snapshot_date(feeds) == pd.Timestamp("2026-09-01")
        assert runner.latest_snapshot_date({}) is None


# ── backtest.adapter（duck-typing 契约）──


class _FakeLine:
    def __init__(self, values):
        self._v = list(values)

    def get(self, size=1):
        return self._v[-max(1, int(size)):]

    def __getitem__(self, i):
        return self._v[-1 + i]  # backtrader 语义：[0] 为当前 bar（最后一个）


class _FakeData:
    def __init__(self, name, closes, amounts):
        self._name = name
        self.close = _FakeLine(closes)
        self.amount = _FakeLine(amounts)
        self.datetime = types.SimpleNamespace(date=lambda ago: datetime.date(2026, 9, 1))

    def __len__(self):
        return len(self.close._v)


class TestAdapter:
    def _view(self):
        return BTMarketView([
            _FakeData("A.SH", [10.0, 11.0, 12.0], [100.0, 200.0, 300.0]),
            _FakeData("B.SH", [5.0, 4.0], [10.0, 20.0]),
        ])

    def test_series_lookup_and_closes_window(self):
        view = self._view()
        s = view.series("A.SH")
        assert s is not None and s.name == "A.SH"
        np.testing.assert_allclose(s.closes(2), [11.0, 12.0])
        np.testing.assert_allclose(s.amounts(2), [200.0, 300.0])
        np.testing.assert_allclose(s.closes(99), [10.0, 11.0, 12.0])  # 超长取全部
        np.testing.assert_allclose(s.closes(0), [12.0])  # 钳制到 ≥1

    def test_last_close_is_current_bar(self):
        assert self._view().series("A.SH").last_close() == 12.0

    def test_series_missing_returns_none(self):
        assert self._view().series("NOPE.SH") is None

    def test_current_date_and_all_series(self):
        view = self._view()
        assert view.current_date() == datetime.date(2026, 9, 1)
        assert [s.name for s in view.all_series()] == ["A.SH", "B.SH"]

    def test_empty_view_current_date_none(self):
        assert BTMarketView([]).current_date() is None


# ── core.panel ──


def _long_table(days=8, start="2024-01-02", base=100.0, premium=8.0,
                symbols_returns=None, extra_cols=True):
    symbols_returns = symbols_returns or {"A.SH": 0.001, "B.SH": 0.002}
    dates = pd.bdate_range(start, periods=days)
    rows = []
    for i, (sym, ret) in enumerate(symbols_returns.items()):
        price = base * (1.0 + ret) ** np.arange(days)
        row = {"as_of": dates, "symbol": sym, "price": price}
        if extra_cols:
            row["premium_rate"] = np.full(days, premium + i)
            row["rating"] = "AA"
        rows.append(pd.DataFrame(row))
    return pd.concat(rows, ignore_index=True)


class TestPanel:
    def test_missing_required_columns_raise(self):
        with pytest.raises(ValueError, match="缺少字段"):
            Panel(pd.DataFrame({"as_of": pd.bdate_range("2024-01-01", periods=3)}))
        with pytest.raises(ValueError, match="price"):
            Panel(pd.DataFrame({
                "as_of": pd.bdate_range("2024-01-01", periods=3),
                "symbol": ["A"] * 3,
            }))

    def test_duplicate_rows_keep_last(self):
        table = _long_table(days=2, extra_cols=False)
        dup = table.iloc[[0]].copy()
        dup["price"] = 999.0
        panel = Panel(pd.concat([table, dup], ignore_index=True))
        assert panel.price.iloc[0]["A.SH"] == 999.0

    def test_missing_combos_stay_nan(self):
        table = _long_table(days=4, extra_cols=False)
        table = table[~((table["symbol"] == "B.SH") & (table["as_of"] == table["as_of"].min()))]
        panel = Panel(table)
        assert np.isnan(panel.price.iloc[0]["B.SH"])
        assert panel.price.iloc[1]["B.SH"] == pytest.approx(100.0 * 1.002)

    def test_get_panel_unknown_field_and_slicing(self):
        panel = Panel(_long_table(days=6))
        with pytest.raises(KeyError, match="volume"):
            panel.get_panel("volume")
        sub = panel.get_panel("price", start="2024-01-03", end="2024-01-05")
        assert list(sub.index) == [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04"),
                                   pd.Timestamp("2024-01-05")]

    def test_premium_rate_defaults_empty(self):
        panel = Panel(_long_table(extra_cols=False))
        empty = panel.premium_rate
        assert empty.empty and list(empty.index) == list(panel.dates)

    def test_snapshot_merges_static_fields(self):
        panel = Panel(_long_table(days=3))
        snap = panel.snapshot("2024-01-03")
        assert set(snap["symbol"]) == {"A.SH", "B.SH"}
        assert (snap["rating"] == "AA").all()
        assert snap["premium_rate"].tolist() == [8.0, 9.0]
        assert panel.snapshot("1999-01-01").empty  # 无该日截面

    def test_forward_returns_no_lookahead(self):
        panel = Panel(_long_table(days=5, extra_cols=False))
        fwd = panel.forward_returns(periods=2)
        p = panel.price
        expected = p.shift(-2) / p - 1.0
        pd.testing.assert_frame_equal(fwd, expected)
        assert fwd.iloc[-2:].isna().all().all()  # 尾部无未来价
        assert fwd.iloc[:-2].notna().all().all()


# ── core.factors ──


@pytest.fixture()
def factor_panel():
    # 6 只：min_symbols=5 门槛可满足；收益逐只递减 + 溢价逐只递增
    # → 双低值序与未来收益序恒定反向（RankIC < 0）
    syms = {f"S{k}.SH": 0.001 - 0.0001 * k for k in range(6)}
    return Panel(_long_table(days=30, symbols_returns=syms))


class TestFactors:
    def test_duplicate_registration_rejected(self):
        def _f(panel):
            return panel.price

        register_factor("_test_dup", "tmp")(  _f)
        with pytest.raises(ValueError, match="重复注册"):
            register_factor("_test_dup", "tmp")(_f)
        FACTOR_REGISTRY.pop("_test_dup", None)  # 清理全局注册表

    def test_unknown_factor_raises(self, factor_panel):
        with pytest.raises(KeyError, match="no_such_factor"):
            compute_factor("no_such_factor", factor_panel)

    def test_builtin_factor_math(self, factor_panel):
        dl = compute_factor("double_low", factor_panel)
        pd.testing.assert_frame_equal(dl, factor_panel.price + factor_panel.premium_rate)
        cv = compute_factor("conv_value", factor_panel)
        pd.testing.assert_frame_equal(
            cv, factor_panel.price / (1.0 + factor_panel.premium_rate / 100.0))

    def test_ic_series_rank_and_pearson(self):
        # IC 是逐日截面相关：每日内因子序与未来收益序恒定反向 → IC ≡ -1
        idx = pd.bdate_range("2024-01-01", periods=3)
        factor = pd.DataFrame({"A": [1.0, 1.0, 1.0], "B": [3.0, 3.0, 3.0]}, index=idx)
        fwd = pd.DataFrame({"A": [0.3, 0.3, 0.3], "B": [-0.3, -0.3, -0.3]}, index=idx)
        rank_ic = ic_series(factor, fwd, method="rank", min_symbols=2)
        pearson_ic = ic_series(factor, fwd, method="pearson", min_symbols=2)
        assert np.allclose(rank_ic, -1.0)
        assert np.allclose(pearson_ic, -1.0)

    def test_ic_series_min_symbols_gate(self):
        idx = pd.bdate_range("2024-01-01", periods=2)
        factor = pd.DataFrame({"A": [1.0, 2.0], "B": [2.0, 1.0], "C": [3.0, 3.0]}, index=idx)
        fwd = pd.DataFrame({"A": [0.1, 0.2], "B": [-0.1, -0.2],
                            "C": [np.nan, np.nan]}, index=idx)
        assert ic_series(factor, fwd, min_symbols=3).empty  # 每日有效样本 2 < 3
        assert len(ic_series(factor, fwd, min_symbols=2)) == 2

    def test_evaluate_factor_report_shape(self, factor_panel):
        report = evaluate_factor(factor_panel, "double_low", periods=(5, 10))
        assert set(report["method"]) == {"rank", "pearson"}
        assert set(report["periods"]) == {5, 10}
        assert (report["n_days"] > 0).all()
        assert report["higher_is_better"].eq(False).all()
        # 溢价率恒定 + 价格单调 → 双低与未来收益的 RankIC 应高度为负方向一致
        rank_row = report[(report["method"] == "rank") & (report["periods"] == 5)]
        assert rank_row["mean_ic"].iloc[0] < 0

    def test_evaluate_factor_universe_filter(self, factor_panel):
        syms = [f"S{k}.SH" for k in range(5)]
        report = evaluate_factor(factor_panel, "double_low", periods=(5,),
                                 universe=syms)
        assert (report["coverage"] == 5).all()  # universe 限 5 只 → 每日覆盖 5


class TestF7Factors:
    """F7：ytm_proxy（零息近似）与 premium_momentum_20（联动差值代理）"""

    def _panel_with_maturity(self, prices, maturities, days=30):
        dates = pd.bdate_range("2024-01-02", periods=days)
        rows = []
        for sym, price in prices.items():
            rows.append(pd.DataFrame({
                "as_of": dates, "symbol": sym,
                "price": np.full(days, price),
                "premium_rate": np.full(days, 10.0),
                "rating": "AA", "list_date": dates[0],
                "maturity_date": pd.Timestamp(maturities[sym]),
            }))
        return Panel(pd.concat(rows, ignore_index=True))

    def test_ytm_proxy_zero_coupon_math(self):
        panel = self._panel_with_maturity(
            prices={"A.SH": 110.0, "B.SH": 90.0},
            maturities={"A.SH": "2025-01-01", "B.SH": "2025-01-01"},
            days=5,
        )
        y = compute_factor("ytm_proxy", panel)
        # 一年整到期：ytm = 100/price - 1
        assert y.iloc[0]["A.SH"] == pytest.approx(100.0 / 110.0 - 1.0)
        assert y.iloc[0]["B.SH"] == pytest.approx(100.0 / 90.0 - 1.0)
        # 越接近到期，|ytm| 越大（复利放大）
        assert abs(y.iloc[-1]["A.SH"]) > abs(y.iloc[0]["A.SH"])

    def test_ytm_proxy_expired_is_nan(self):
        panel = self._panel_with_maturity(
            prices={"A.SH": 110.0}, maturities={"A.SH": "2024-01-05"}, days=10)
        y = compute_factor("ytm_proxy", panel)
        # 期初未到期 → 有限值；到期后 → NaN
        years0 = (pd.Timestamp("2024-01-05") - panel.dates[0]).days / 365
        assert y.iloc[0]["A.SH"] == pytest.approx(
            (100.0 / 110.0) ** (1.0 / years0) - 1.0)
        assert np.isnan(y.iloc[-1]["A.SH"])

    def test_ytm_proxy_without_maturity_column_all_nan(self):
        panel = Panel(_long_table(days=5, extra_cols=False))
        y = compute_factor("ytm_proxy", panel)
        assert y.isna().all().all()

    def test_premium_momentum_is_diff20(self):
        panel = Panel(_long_table(days=30))
        m = compute_factor("premium_momentum_20", panel)
        expected = panel.premium_rate.diff(20)
        pd.testing.assert_frame_equal(m, expected)
        assert m.iloc[:20].isna().all().all()  # 前二十日无窗口
        assert (m.iloc[20:] == 0).all().all()  # 恒定溢价 → 变化为 0

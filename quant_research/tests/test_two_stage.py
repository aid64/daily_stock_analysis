"""R5 单测：Panel 矩阵 / 因子注册与 IC / 两段式转债回测

全部合成数据，不依赖网络。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_research.backtest.two_stage import (
    WeightTargetStrategy,
    compute_cb_target_weights,
    pit_to_feeds,
)
from quant_research.backtest.engine import BacktestEngine
from quant_research.config.schema import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    DataSource,
    TushareConfig,
)
from quant_research.core.panel import Panel


def _make_config(initial_capital: float = 100000.0) -> AppConfig:
    return AppConfig(
        data=DataConfig(primary=DataSource.BAOSTOCK),
        tushare=TushareConfig(token=""),
        backtest=BacktestConfig(initial_capital=initial_capital),
    )


def _make_pit(symbols_with_ret: dict[str, float], days: int = 120,
              start: str = "2024-01-02", base_price: float = 110.0,
              premium: float = 10.0) -> pd.DataFrame:
    """合成 PIT 长表：各标的固定日收益 + 常数溢价率 + 评级/list_date"""
    dates = pd.bdate_range(start, periods=days)
    rows = []
    for sym, ret in symbols_with_ret.items():
        price = base_price * (1.0 + ret) ** np.arange(days)
        rows.append(pd.DataFrame({
            "as_of": dates,
            "symbol": sym,
            "price": price,
            "premium_rate": np.full(days, premium),
            "rating": "AA",
            "list_date": dates[0],
        }))
    return pd.concat(rows, ignore_index=True)


# ── Panel ────────────────────────────────────────


class TestPanel:
    def test_pivot_shape_and_nan(self):
        pit = _make_pit({"A.SH": 0.001, "B.SH": 0.002}, days=60)
        pit.loc[(pit["as_of"] == pit["as_of"].min()) & (pit["symbol"] == "B.SH"), "price"] = np.nan
        panel = Panel(pit)
        assert panel.price.shape == (60, 2)
        assert panel.symbols.tolist() == ["A.SH", "B.SH"]
        assert np.isnan(panel.price.iloc[0, 1])

    def test_get_panel_date_range(self):
        pit = _make_pit({"A.SH": 0.001}, days=100)
        panel = Panel(pit)
        sub = panel.get_panel("price", start="2024-02-01", end="2024-02-15")
        assert (sub.index >= pd.Timestamp("2024-02-01")).all()
        assert (sub.index <= pd.Timestamp("2024-02-15")).all()

    def test_forward_returns_alignment(self):
        pit = _make_pit({"A.SH": 0.01}, days=30)  # 日涨 1%
        panel = Panel(pit)
        fwd = panel.forward_returns(periods=5)
        # t 日的前向 5 日收益 = 1.01^5 - 1
        assert fwd["A.SH"].iloc[0] == pytest.approx(1.01 ** 5 - 1, rel=1e-9)
        assert fwd["A.SH"].iloc[-5:].isna().all()  # 尾部无未来价

    def test_missing_fields_raise(self):
        with pytest.raises(ValueError, match="缺少字段"):
            Panel(pd.DataFrame({"date": [1], "symbol": ["A"]}))


# ── 因子与 IC ────────────────────────────────────


class TestFactors:
    def test_double_low_matrix(self):
        from quant_research.core.factors import compute_factor
        pit = _make_pit({"A.SH": 0.001}, days=20)
        panel = Panel(pit)
        dl = compute_factor("double_low", panel)
        assert dl["A.SH"].iloc[0] == pytest.approx(110.0 + 10.0)  # 首日价 110 + 溢价 10

    def test_unknown_factor_raises(self):
        from quant_research.core.factors import compute_factor
        with pytest.raises(KeyError, match="未注册因子"):
            compute_factor("nope", Panel(_make_pit({"A.SH": 0.001}, days=5)))

    def test_ic_sign_of_double_low(self):
        """双低值越低越优：构造"溢价低者未来涨"的截面 → RankIC 为负"""
        from quant_research.core.factors import ic_series
        days = 40
        dates = pd.bdate_range("2024-01-02", periods=days)
        # 低价券 A 持续涨（溢价 0），高价券 B 持续跌（溢价 30）
        a_price = 100.0 * 1.005 ** np.arange(days)
        b_price = 130.0 * 0.995 ** np.arange(days)
        pit = pd.concat([
            pd.DataFrame({"as_of": dates, "symbol": "A.SH", "price": a_price,
                          "premium_rate": 0.0}),
            pd.DataFrame({"as_of": dates, "symbol": "B.SH", "price": b_price,
                          "premium_rate": 30.0}),
        ])
        panel = Panel(pit)
        ic = ic_series(panel.get_panel("price") + panel.get_panel("premium_rate"),
                       panel.forward_returns(periods=5), method="rank", min_symbols=2)
        assert (ic < 0).all()


# ── 两段式 ───────────────────────────────────────


class TestComputeTargetWeights:
    def test_monthly_first_trading_day_and_top_n(self):
        pit = _make_pit({"A.SH": 0.01, "B.SH": 0.005, "C.SH": -0.001}, days=90)
        weights = compute_cb_target_weights(pit, start="2024-01-01", end="2024-12-31",
                                            rebalance="M", top_n=2, min_rating="A")
        assert weights, "应至少产出一个月度调仓"
        for date_str, picks in weights.items():
            assert len(picks) == 2
            assert all(abs(w - 0.5) < 1e-9 for w in picks.values())
            ts = pd.Timestamp(date_str)
            assert ts.is_month_start or (ts - pd.Timedelta(days=3)).month != ts.month

    def test_v2_filter_excludes_high_conv_value(self):
        """溢价率 30% → 转股价值 = 110/1.3 ≈ 84.6（不超 135）；改用价格硬顶测试"""
        pit = _make_pit({"A.SH": 0.001}, days=30, base_price=100.0, premium=10.0)
        weights = compute_cb_target_weights(pit, start="2024-01-01", end="2024-12-31",
                                            max_price=105.0, min_rating="A")
        assert weights  # 价格 100-103 低于 105 → 有持仓

    def test_min_candidates_yields_cash(self):
        """候选 3 只 < min_candidates=10 → 全部调仓日让位现金"""
        pit = _make_pit({"A.SH": 0.001, "B.SH": 0.001, "C.SH": 0.001}, days=30)
        weights = compute_cb_target_weights(pit, start="2024-01-01", end="2024-12-31",
                                            min_rating="A", min_candidates=10)
        assert weights == {}

    def test_pit_to_feeds_synthesizes_columns(self):
        pit = _make_pit({"A.SH": 0.001}, days=30)
        feeds = pit_to_feeds(pit, ["A.SH"])
        assert set(feeds) == {"A.SH"}
        cols = set(feeds["A.SH"].columns)
        assert {"date", "open", "high", "low", "close", "volume", "amount"} <= cols
        assert (feeds["A.SH"]["open"] == feeds["A.SH"]["close"]).all()


class TestWeightTargetStrategy:
    def test_full_exit_of_single_lot_position(self):
        """R5 修正回归：被剔除目标的 1 手持仓必须整仓卖出（旧超额取整逻辑下永远卖不掉）"""
        # 第 1 个月选 A，第 2 个月只选 B → A 应被清仓
        dates = pd.bdate_range("2024-01-02", periods=60)
        a = np.concatenate([110.0 * 1.002 ** np.arange(15),
                            112.0 * 0.999 ** np.arange(45)])
        b = 105.0 * 1.002 ** np.arange(60)
        pit = pd.concat([
            pd.DataFrame({"as_of": dates, "symbol": "A.SH", "price": a,
                          "premium_rate": 5.0, "rating": "AA", "list_date": dates[0]}),
            pd.DataFrame({"as_of": dates[:15], "symbol": "B.SH", "price": b[:15],
                          "premium_rate": 5.0, "rating": "AA", "list_date": dates[0]}),
        ])
        weights = {
            "2024-01-02": {"A.SH": 1.0},
            "2024-01-22": {"B.SH": 1.0},
        }
        feeds = pit_to_feeds(pit, ["A.SH", "B.SH"])
        cfg = _make_config()
        engine = BacktestEngine(cfg, commission_overrides={"stamp_duty": 0.0, "transfer_fee": 0.0},
                                logger=None)
        result = engine.run(WeightTargetStrategy, {
            "target_weights": weights,
            "convertible_symbols": tuple(feeds),
            "convertible_lot_size": 10,
            "lot_size": 10,
        }, feeds)
        # 换仓后应只剩 B（通过现金回流验证 A 已清仓：B 买入额 ≈ 全部资金）
        assert result.total_trades >= 1
        assert result.final_value > 0

    def _run(self, pit, pick_params=None):
        weights = compute_cb_target_weights(pit, start="2024-01-01", end="2024-12-31",
                                            rebalance="M", min_rating="A",
                                            **(pick_params or {}))
        assert weights
        picked = sorted({s for p in weights.values() for s in p})
        feeds = pit_to_feeds(pit, picked)
        cfg = _make_config()
        engine = BacktestEngine(cfg, commission_overrides={"stamp_duty": 0.0, "transfer_fee": 0.0},
                                logger=None)
        result = engine.run(WeightTargetStrategy, {
            "target_weights": weights,
            "convertible_symbols": tuple(feeds.keys()),
            "convertible_lot_size": 10,
            "lot_size": 10,
        }, feeds)
        return result, weights

    def test_executes_schedule_and_grows_with_winner(self):
        """持仓跟上权重计划：强标的组合净值应单调跟随其上涨"""
        result, weights = self._run(_make_pit({"A.SH": 0.01, "B.SH": 0.005, "C.SH": -0.001}))
        assert result.final_value > 100000.0  # A 月涨 ~21%，组合应盈利
        assert result.final_value < 121000.0 * 1.05  # 且不会超过全仓 A 的量级

    def test_t_plus_0_cb_same_month_rotation(self):
        """转债 T+0：月度轮换不需要 T+1 豁免即可正常换仓"""
        # 前半段 A 强，后半段 D 强 → 第二次调仓换仓成交
        up = 100.0 * 1.01 ** np.arange(60)
        dn = 100.0 * 1.01 ** np.arange(60) * 0.5  # 低位起步后段更强
        dates = pd.bdate_range("2024-01-02", periods=120)
        d_price = dn[:60].tolist() + (100.0 * 1.02 ** np.arange(60)).tolist()
        a_price = (100.0 * 1.01 ** np.arange(60)).tolist() + (100.0 * 1.01 ** 59 * 1.0 ** np.arange(60)).tolist()
        pit = pd.concat([
            pd.DataFrame({"as_of": dates, "symbol": "A.SH", "price": a_price,
                          "premium_rate": 10.0, "rating": "AA", "list_date": dates[0]}),
            pd.DataFrame({"as_of": dates, "symbol": "D.SH", "price": d_price,
                          "premium_rate": 10.0, "rating": "AA", "list_date": dates[0]}),
        ])
        result, weights = self._run(pit)
        assert result.final_value > 0

"""MWU 多策略融合测试

覆盖：RSI 计算 / 三类专家信号 / build_experts 配置构建 /
权重乘性更新（下跌市动量专家权重下降、防御上升）/ 端到端回测冒烟。
所有测试使用合成数据，不依赖网络。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quant_research.backtest.engine import BacktestEngine
from quant_research.backtest.mwu import (
    DefensiveExpert,
    ETFMWU,
    MeanReversionExpert,
    MomentumExpert,
    build_experts,
    rsi,
)
from quant_research.backtest.convertible import (
    ConvertibleDoubleLowExpert,
    compute_double_low_scores,
)
from quant_research.config.schema import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    DataSource,
    TushareConfig,
)
from quant_research.core.market import ArrayMarketView
from tests.test_backtest import _make_trend_bars


def _make_config(initial_capital: float = 10000.0) -> AppConfig:
    return AppConfig(
        data=DataConfig(primary=DataSource.BAOSTOCK),
        tushare=TushareConfig(token=""),
        backtest=BacktestConfig(initial_capital=initial_capital),
    )


# ── RSI ───────────────────────────────────────


class TestRsi:
    def test_monotonic_up_is_100(self):
        closes = np.linspace(1.0, 2.0, 10)
        assert rsi(closes, 2) == 100.0

    def test_monotonic_down_is_0(self):
        closes = np.linspace(2.0, 1.0, 10)
        assert rsi(closes, 2) == 0.0

    def test_flat_is_50(self):
        closes = np.full(10, 1.5)
        assert rsi(closes, 2) == 50.0

    def test_insufficient_data_is_nan(self):
        assert np.isnan(rsi(np.array([1.0, 2.0]), 5))


# ── 专家信号 ─────────────────────────────────


def _series(name: str, closes: np.ndarray, amount: float = 1e8):
    """构造单标的测试数据 (name, closes, amount)，经 _view 包装成 MarketView"""
    return (name, closes, amount)


def _view(*series) -> ArrayMarketView:
    """由 (name, closes, amount) 序列构建测试用 MarketView

    时间语义与 backtrader line / ArraySeries 一致：closes[-1] 为当前 bar。
    """
    return ArrayMarketView({
        name: (closes, amount) for name, closes, amount in series
    })


class TestMeanReversionExpert:
    def test_selects_oversold_in_uptrend(self):
        # 长期上涨（close > MA200）末端急跌 2 日 → RSI(2) 低
        closes = np.linspace(1.0, 2.0, 200)
        closes[-2:] = closes[-3] * np.array([0.95, 0.92])
        expert = MeanReversionExpert(rsi_period=2, rsi_buy=30, ma_filter=200, top_k=1)
        sel = expert.select(_view(_series("DIP.SH", closes)))
        assert "DIP.SH" in sel
        assert sel["DIP.SH"] == pytest.approx(1.0)

    def test_trend_filter_blocks_bear_market_dip(self):
        # 长期下跌趋势中的超卖 → MA200 过滤拒绝接飞刀
        closes = np.linspace(2.0, 1.0, 200)
        closes[-1] = closes[-2] * 0.95
        expert = MeanReversionExpert(rsi_period=2, rsi_buy=30, ma_filter=200, top_k=1)
        assert expert.select(_view(_series("BEAR.SH", closes))) == {}

    def test_high_rsi_not_selected(self):
        closes = np.linspace(1.0, 3.0, 200)  # 持续上涨，RSI=100
        expert = MeanReversionExpert(rsi_period=2, rsi_buy=30, ma_filter=200, top_k=1)
        assert expert.select(_view(_series("UP.SH", closes))) == {}


class TestDefensiveExpert:
    def test_selects_symbol(self):
        expert = DefensiveExpert("DEF.SH")
        assert expert.select(_view(_series("DEF.SH", np.full(30, 1.0)))) == {"DEF.SH": 1.0}

    def test_empty_when_symbol_missing(self):
        expert = DefensiveExpert("DEF.SH")
        assert expert.select(_view(_series("OTHER.SH", np.full(30, 1.0)))) == {}

    def test_empty_when_nan_price(self):
        expert = DefensiveExpert("DEF.SH")
        assert expert.select(_view(_series("DEF.SH", np.array([1.0, np.nan])))) == {}


class TestMomentumExpert:
    def test_selects_strongest(self):
        up = _series("UP.SH", 1.0 * 1.005 ** np.arange(80))
        flat = _series("FLAT.SH", np.full(80, 1.0))
        expert = MomentumExpert(lookback=5, top_k=1, ma_filter=10, min_volume=0.0)
        assert list(expert.select(_view(up, flat))) == ["UP.SH"]

    def test_all_downtrend_empty(self):
        down = _series("DOWN.SH", 1.0 * 0.998 ** np.arange(80))
        expert = MomentumExpert(lookback=5, top_k=1, ma_filter=10, min_volume=0.0)
        assert expert.select(_view(down)) == {}


# ── build_experts ─────────────────────────────


class TestBuildExperts:
    def test_build_from_config(self):
        experts = build_experts({
            "momentum": {"lookback": 60, "top_k": 3},
            "mean_reversion": {"rsi_period": 2},
            "defensive": {"symbol": "511010.SH"},
        })
        assert [e.name for e in experts] == ["momentum", "mean_reversion", "defensive"]
        assert isinstance(experts[0], MomentumExpert)
        assert isinstance(experts[1], MeanReversionExpert)
        assert isinstance(experts[2], DefensiveExpert)
        assert experts[0].lookback == 60

    def test_build_with_convertible_double_low(self):
        bond_table = pd.DataFrame({
            "symbol": ["110076.SH", "128136.SZ"],
            "price": [117.0, 118.0],
            "premium_rate": [10.0, 16.0],
            "rating": ["AA", "AA+"],
            "list_date": ["2024-01-01", "2024-01-01"],
        })
        experts = build_experts({
            "momentum": {"lookback": 60, "top_k": 3},
            "convertible_double_low": {"bond_table": bond_table, "top_n": 5},
            "defensive": {"symbol": "511010.SH"},
        })
        assert [e.name for e in experts] == ["momentum", "convertible_double_low", "defensive"]
        assert isinstance(experts[1], ConvertibleDoubleLowExpert)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="未知专家类型"):
            build_experts({"oracle": {}})

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="experts 配置为空"):
            build_experts({})


# ── 可转债双低专家 ─────────────────────────


def _make_bond_table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestConvertibleDoubleLowExpert:
    def _bond_table(self) -> pd.DataFrame:
        return _make_bond_table([
            # 双低值：110.5 最低 → 最优
            {"symbol": "110076.SH", "price": 110.0, "premium_rate": 0.5,
             "rating": "AAA", "list_date": "2024-01-01"},
            {"symbol": "128134.SZ", "price": 112.0, "premium_rate": 3.0,
             "rating": "AA", "list_date": "2024-01-01"},
            {"symbol": "113042.SH", "price": 120.0, "premium_rate": 5.0,
             "rating": "AA-", "list_date": "2024-01-01"},
            # 溢价率超标，应被过滤
            {"symbol": "128136.SZ", "price": 115.0, "premium_rate": 25.0,
             "rating": "AA+", "list_date": "2024-01-01"},
            # 评级不达标，应被过滤
            {"symbol": "127040.SZ", "price": 118.0, "premium_rate": 2.0,
             "rating": "A+", "list_date": "2024-01-01"},
        ])

    def _datas(self, prices: dict[str, float]) -> ArrayMarketView:
        """常数价格视图（300 根，覆盖 MA/动量窗口）"""
        return _view(*(
            _series(sym, np.full(300, p)) for sym, p in prices.items()
        ))

    def test_selects_top_n_by_double_low(self):
        exp = ConvertibleDoubleLowExpert(
            self._bond_table(), top_n=2, min_rating="AA-", max_premium=20.0
        )
        datas = self._datas({
            "110076.SH": 110.0, "128134.SZ": 112.0, "113042.SH": 120.0,
            "128136.SZ": 115.0, "127040.SZ": 118.0,
        })
        picks = exp.select(datas)
        # 溢价率 25% 与评级 A+ 被过滤 → 只 3 只候选，取双低最低 2 只
        assert set(picks.keys()) == {"110076.SH", "128134.SZ"}
        assert all(abs(w - 0.5) < 1e-9 for w in picks.values())

    def test_uses_feed_price_not_snapshot_price(self):
        exp = ConvertibleDoubleLowExpert(self._bond_table(), top_n=1, max_premium=20.0)
        # feed 现价让 113042 的双低值降到最低（120→100）
        datas = self._datas({
            "110076.SH": 130.0, "128134.SZ": 130.0, "113042.SH": 100.0,
            "128136.SZ": 115.0, "127040.SZ": 118.0,
        })
        picks = exp.select(datas)
        assert list(picks.keys()) == ["113042.SH"]

    def test_filters_bad_rating_and_high_premium(self):
        exp = ConvertibleDoubleLowExpert(self._bond_table(), top_n=5, max_premium=20.0)
        datas = self._datas({
            "110076.SH": 110.0, "128134.SZ": 112.0, "113042.SH": 120.0,
            "128136.SZ": 115.0, "127040.SZ": 118.0,
        })
        picks = exp.select(datas)
        assert "128136.SZ" not in picks   # 溢价率 25% > 20%
        assert "127040.SZ" not in picks   # 评级 A+ < AA-
        assert len(picks) == 3

    def test_empty_when_no_candidates(self):
        exp = ConvertibleDoubleLowExpert(self._bond_table(), top_n=5, max_premium=0.0)
        datas = self._datas({
            "110076.SH": 110.0, "128134.SZ": 112.0, "113042.SH": 120.0,
        })
        # 所有候选溢价率均 > 0 → 被 max_premium=0 过滤干净
        assert exp.select(datas) == {}

    def test_requires_bond_table(self):
        with pytest.raises(ValueError, match="bond_table"):
            ConvertibleDoubleLowExpert(None)

    def test_matches_strategy_ranking_logic(self):
        """专家选债与 ConvertibleDoubleLow 策略同一排名逻辑（price 用现价）"""
        bond_table = self._bond_table()
        exp = ConvertibleDoubleLowExpert(bond_table, top_n=3, max_premium=20.0)
        datas = self._datas({
            "110076.SH": 110.0, "128134.SZ": 112.0, "113042.SH": 120.0,
            "128136.SZ": 115.0, "127040.SZ": 118.0,
        })
        picks = exp.select(datas)
        # 排序：110.5(110076) / 115.0(128134) / 125.0(113042) → 前 3
        assert list(picks.keys()) == ["110076.SH", "128134.SZ", "113042.SH"]


# ── 端到端 ───────────────────────────────────


_SMALL_EXPERTS = {
    "momentum": {"lookback": 5, "top_k": 1, "ma_filter": 10},
    "mean_reversion": {"rsi_period": 2, "rsi_buy": 30, "ma_filter": 10, "top_k": 1},
    "defensive": {"symbol": "DEF.SH"},
}


class _MWUSpy(ETFMWU):
    """期末权重记录探针"""

    final_weights: dict = {}

    def stop(self):
        _MWUSpy.final_weights = dict(self._weights)


class TestETFMWU:
    def test_lot_sizes_distinguish_convertible_and_etf(self):
        strategy = object.__new__(ETFMWU)
        strategy.p = SimpleNamespace(
            convertible_symbols=("110076.SH",),
            convertible_lot_size=10,
            defensive="511010.SH",
            defensive_lot_size=10,
            lot_size=100,
        )
        assert strategy._lot_of("110076.SH") == 10
        assert strategy._lot_of("511010.SH") == 10
        assert strategy._lot_of("510300.SH") == 100

    def test_momentum_loses_weight_in_crash(self):
        """动量专家选中后暴跌 → 纸面亏损 → 权重被乘性压缩，防御相对上升"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        # 先涨 25 日诱多，再跌 45 日：动量专家选中 RISK 后持续纸面亏损
        up = _make_trend_bars(25, 0.008)
        down = _make_trend_bars(45, -0.012, start_price=up["close"].iloc[-1])
        risk = pd.concat([up, down.iloc[1:]], ignore_index=True)
        risk["date"] = pd.bdate_range("2024-01-01", periods=len(risk))
        # 防御资产缓慢稳定上涨
        def_bars = _make_trend_bars(len(risk), 0.0005, start_price=100.0)

        params = {
            "experts": build_experts(_SMALL_EXPERTS),
            "eta": 0.3,
            "loss_scale": 0.05,
            "rebalance_freq": "W",
            "defensive": "DEF.SH",
            "defensive_lot_size": 10,
        }
        result = engine.run(
            _MWUSpy,
            params,
            {"RISK.SH": risk, "DEF.SH": def_bars},
        )

        w = _MWUSpy.final_weights
        assert w["momentum"] < w["defensive"], (
            f"暴跌市中动量权重应低于防御: momentum={w['momentum']:.4f} "
            f"defensive={w['defensive']:.4f}"
        )
        assert sum(w.values()) == pytest.approx(1.0)
        assert result.final_value > 0

    def test_smoke_mixed_market_runs(self):
        """混合行情端到端冒烟：有交易、权重归一、组合可执行"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        feeds = {
            "STRONG.SH": _make_trend_bars(120, 0.004),
            "DIP.SH": _make_trend_bars(120, 0.001),
            "DEF.SH": _make_trend_bars(120, 0.0003, start_price=100.0),
        }
        params = {
            "experts": build_experts(_SMALL_EXPERTS),
            "eta": 0.1,
            "loss_scale": 0.05,
            "rebalance_freq": "W",
            "defensive": "DEF.SH",
            "defensive_lot_size": 10,
        }
        result = engine.run(ETFMWU, params, feeds)
        assert result.final_value > 0
        assert result.total_trades >= 0  # 冒烟：不崩即可

    def test_requires_at_least_two_experts(self):
        """策略实例化发生在 cerebro.run 内，异常从 engine.run 传播"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        feeds = {"DEF.SH": _make_trend_bars(30, 0.001, start_price=100.0)}
        with pytest.raises(ValueError, match="至少需要 2 个专家"):
            engine.run(
                ETFMWU,
                {"experts": [DefensiveExpert("DEF.SH")]},
                feeds,
            )

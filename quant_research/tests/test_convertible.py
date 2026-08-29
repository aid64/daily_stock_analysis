"""可转债双低策略测试

覆盖：评级排序 / 代码归一化 / 双低评分过滤 / 回测引擎冒烟。
评分与回测均用合成数据，不依赖网络。
"""
from __future__ import annotations

import logging
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_research.backtest.convertible import (
    ConvertibleDoubleLow,
    PITConvertibleDoubleLowExpert,
    compute_double_low_scores,
    load_pit_snapshot,
    normalize_cb_symbol,
    rating_rank,
    snapshot_picks,
    validate_pit_snapshot,
)
from quant_research.backtest.engine import BacktestEngine
from quant_research.config.schema import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    DataSource,
    TushareConfig,
)

_BUILD_PIT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_pit_snapshot.py"
_BUILD_PIT_SPEC = importlib.util.spec_from_file_location("build_pit_snapshot", _BUILD_PIT_SCRIPT)
build_pit_snapshot = importlib.util.module_from_spec(_BUILD_PIT_SPEC)
assert _BUILD_PIT_SPEC.loader is not None
_BUILD_PIT_SPEC.loader.exec_module(build_pit_snapshot)


def _make_config(initial_capital: float = 10000.0) -> AppConfig:
    return AppConfig(
        data=DataConfig(primary=DataSource.BAOSTOCK),
        tushare=TushareConfig(token=""),
        backtest=BacktestConfig(initial_capital=initial_capital),
    )


def _make_bond_df() -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["123456.SZ", "110000.SH", "123457.SZ", "110001.SH", "128000.SZ"],
        "price": [100.0, 130.0, 95.0, 120.0, 108.0],
        "premium_rate": [5.0, 25.0, 3.0, 8.0, -2.0],
        "rating": ["AA", "AA", "A+", "AA+", "AA-"],
        "list_date": ["2024-06-01"] * 5,
    })


def _make_bond_bars(n: int = 60, start_price: float = 100.0, drift: float = 0.0) -> pd.DataFrame:
    dates = pd.bdate_range("2026-06-01", periods=n)
    close = start_price * (1.0 + drift) ** np.arange(n)
    return pd.DataFrame({
        "date": dates,
        "open": close,
        "high": close * 1.005,
        "low": close * 0.995,
        "close": close,
        "volume": np.full(n, 1e6),
        "amount": close * 1e6,
    })


class TestRatingRank:
    def test_order(self):
        assert rating_rank("AAA") < rating_rank("AA+") < rating_rank("AA") < rating_rank("AA-")
        assert rating_rank("AA-") < rating_rank("A+")

    def test_unknown_is_worst(self):
        assert rating_rank("NR") == len([ "AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
                                          "BBB+", "BBB", "BBB-", "BB+", "BB", "B+"])

    def test_case_insensitive(self):
        assert rating_rank("aa-") == rating_rank("AA-")


class TestNormalizeSymbol:
    def test_sh(self):
        assert normalize_cb_symbol("110000") == "110000.SH"
        assert normalize_cb_symbol("110076") == "110076.SH"

    def test_sz(self):
        assert normalize_cb_symbol("128134") == "128134.SZ"
        assert normalize_cb_symbol("123065") == "123065.SZ"

    def test_already_normalized(self):
        assert normalize_cb_symbol("123456.SZ") == "123456.SZ"


class TestDoubleLowScores:
    def test_ranking(self):
        df = _make_bond_df()
        picks = compute_double_low_scores(df, top_n=10, min_rating="AA-", max_premium=20.0)
        # 双低 = price + premium；123456(100+5=105) 最低排第一
        assert picks.iloc[0]["double_low"] == pytest.approx(105.0)
        # 110000（溢价 25 > 20）与 123457（评级 A+ < AA-）被过滤
        assert "110000.SH" not in picks["symbol"].values
        assert "123457.SZ" not in picks["symbol"].values

    def test_median_price_filter(self):
        df = _make_bond_df()
        # max_price_median=True → 价格 120 的 110001 被剔除（高于中位 108）
        picks = compute_double_low_scores(df, top_n=10, min_rating="AA-", max_premium=20.0,
                                          max_price_median=True)
        assert "110001.SH" not in picks["symbol"].values

    def test_new_bond_excluded(self):
        df = _make_bond_df()
        df.loc[0, "list_date"] = "2026-08-01"  # 次新债
        picks = compute_double_low_scores(df, top_n=10, min_rating="AA-", max_premium=20.0,
                                          exclude_new_days=30, as_of="2026-08-15")
        assert "123456.SZ" not in picks["symbol"].values

    def test_missing_columns_raise(self):
        with pytest.raises(ValueError, match="缺必要列"):
            compute_double_low_scores(pd.DataFrame({"symbol": ["1"], "price": [1.0]}))

    def test_snapshot_picks_returns_list(self):
        picks = snapshot_picks(_make_bond_df(), top_n=2, min_rating="AA-", max_premium=20.0)
        assert isinstance(picks, list)
        assert len(picks) == 2


class TestConvertibleDoubleLowEngine:
    def test_smoke_runs(self):
        """双低策略引擎冒烟：选 2 只、月调仓、不崩"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        bond_df = pd.DataFrame({
            "symbol": ["B1.SZ", "B2.SZ", "B3.SZ"],
            "price": [100.0, 105.0, 150.0],
            "premium_rate": [3.0, 6.0, 30.0],
            "rating": ["AA", "AA", "AA"],
            "list_date": ["2024-01-01"] * 3,
        })
        feeds = {
            "B1.SZ": _make_bond_bars(80, 100.0, 0.002),
            "B2.SZ": _make_bond_bars(80, 105.0, 0.001),
            "B3.SZ": _make_bond_bars(80, 150.0, 0.0),
        }
        params = {
            "bond_table": bond_df,
            "top_n": 2,
            "min_rating": "AA-",
            "max_premium": 20.0,
            "rebalance_freq": "M",
            "lot_size": 10,
        }
        result = engine.run(ConvertibleDoubleLow, params, feeds)
        assert result.final_value > 0
        assert result.total_trades >= 0  # 冒烟：不崩即可

    def test_requires_bond_table(self):
        """缺 bond_table 参数应报错"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        feeds = {"B1.SZ": _make_bond_bars(30)}
        with pytest.raises(ValueError, match="需要 bond_table"):
            engine.run(ConvertibleDoubleLow, {"top_n": 1}, feeds)


class TestPITConvertibleDoubleLow:
    def _pit(self):
        return pd.DataFrame({
            "as_of": ["2026-06-01", "2026-06-01", "2026-06-08"],
            "symbol": ["110076", "128134.SZ", "110076.SH"],
            "price": [110.0, 112.0, 120.0],
            "premium_rate": [5.0, 3.0, 5.0],
            "rating": ["AA", "AA+", "AA"],
            "list_date": ["2024-01-01"] * 3,
        })

    def test_requires_complete_pit_columns(self):
        with pytest.raises(ValueError, match="缺少字段"):
            PITConvertibleDoubleLowExpert(self._pit().drop(columns=["rating"]))

    def test_normalizes_symbols_and_keeps_as_of(self):
        expert = PITConvertibleDoubleLowExpert(self._pit(), top_n=5)
        assert set(expert._pit_table["symbol"]) == {"110076.SH", "128134.SZ"}
        assert expert._pit_table["as_of"].dt.strftime("%Y-%m-%d").unique().tolist() == [
            "2026-06-01", "2026-06-08"
        ]

    def test_load_pit_snapshot_rejects_unknown_extension(self, tmp_path):
        path = tmp_path / "pit.txt"
        path.write_text("invalid", encoding="utf-8")
        with pytest.raises(ValueError, match="仅支持"):
            load_pit_snapshot(path)

    def test_load_pit_snapshot_requires_columns(self, tmp_path):
        path = tmp_path / "pit.csv"
        pd.DataFrame({"as_of": ["2026-06-01"], "symbol": ["110076.SH"]}).to_csv(
            path, index=False
        )
        with pytest.raises(ValueError, match="缺少字段"):
            load_pit_snapshot(path)

    def test_validate_pit_snapshot_reports_requested_universe_gap(self):
        report = validate_pit_snapshot(
            self._pit(),
            start="2026-01-01",
            end="2026-12-31",
            requested_symbols=["110076.SH", "128134.SZ", "123456.SZ", "999999.SZ"],
        )
        assert report["requested_symbols"] == 4
        assert report["distinct_symbols"] == 2
        assert report["universe_coverage"] == pytest.approx(2 / 4)
        assert report["missing_symbols"] == ["123456.SZ", "999999.SZ"]

    def test_collection_audit_classifies_missing_raw_data(self, tmp_path):
        raw_dir = tmp_path / "raw"
        cached = raw_dir / "110076.SH"
        cached.mkdir(parents=True)
        (cached / "meta.json").write_text("{}", encoding="utf-8")
        (cached / "2026.csv").write_text("date,close,premium_rate\n", encoding="utf-8")
        no_pit = raw_dir / "128134.SZ"
        no_pit.mkdir()
        (no_pit / "meta.json").write_text("{}", encoding="utf-8")
        (no_pit / "2026.csv").write_text("date,close,premium_rate\n", encoding="utf-8")

        audit = build_pit_snapshot._collection_audit(
            ["110076.SH", "128134.SZ", "123456.SZ"], raw_dir, self._pit()
        )

        assert audit["missing_meta_symbols"] == ["123456.SZ"]
        assert audit["missing_daily_symbols"] == []
        assert audit["no_usable_pit_symbols"] == []
        assert audit["observed_lifecycle"]["110076.SH"] == {
            "first_observed": "2026-06-01",
            "last_observed": "2026-06-08",
        }

    def _pit_v2(self):
        """v2 过滤测试数据：3 只券覆盖三类剔除场景 + 1 只正常保留。"""
        return pd.DataFrame({
            "as_of": ["2026-06-01"] * 4,
            "symbol": ["110076.SH", "128134.SZ", "113042.SH", "113524.SH"],
            "price": [150.0, 112.0, 105.0, 110.0],
            "premium_rate": [5.0, 3.0, 30.0, 8.0],
            "rating": ["AA", "AA+", "AAA", "AA"],
            "list_date": ["2024-01-01"] * 4,
            "maturity_date": ["2026-09-01", "2030-01-01", "2030-01-01", "2030-01-01"],
        })

    def test_v2_filter_excludes_high_conv_value(self):
        # 110076: conv_value = 150/(1.05) = 142.9 > 135 → 强赎风险区剔除
        expert = PITConvertibleDoubleLowExpert(
            self._pit_v2(), top_n=5, max_conv_value=135.0
        )
        snapshot = expert._pit_table[expert._pit_table["as_of"] == pd.Timestamp("2026-06-01")]
        filtered = expert._apply_v2_filters(snapshot, pd.Timestamp("2026-06-01"))
        assert "110076.SH" not in set(filtered["symbol"])
        assert {"128134.SZ", "113524.SH"} <= set(filtered["symbol"])

    def test_v2_filter_excludes_near_maturity(self):
        # 110076 maturity=2026-09-01，as_of=2026-06-01 剩余 92 天 < 365 → 临期剔除
        expert = PITConvertibleDoubleLowExpert(
            self._pit_v2(), top_n=5, min_maturity_days=365
        )
        snapshot = expert._pit_table[expert._pit_table["as_of"] == pd.Timestamp("2026-06-01")]
        filtered = expert._apply_v2_filters(snapshot, pd.Timestamp("2026-06-01"))
        assert "110076.SH" not in set(filtered["symbol"])
        assert "113524.SH" in set(filtered["symbol"])

    def test_v2_filter_excludes_high_price(self):
        expert = PITConvertibleDoubleLowExpert(self._pit_v2(), top_n=5, max_price=120.0)
        snapshot = expert._pit_table[expert._pit_table["as_of"] == pd.Timestamp("2026-06-01")]
        filtered = expert._apply_v2_filters(snapshot, pd.Timestamp("2026-06-01"))
        assert "110076.SH" not in set(filtered["symbol"])

    def test_v2_filters_default_off_backward_compatible(self):
        # 默认 None 时不过滤（向后兼容）：高转股价值/临期/高价全部保留
        expert = PITConvertibleDoubleLowExpert(self._pit_v2(), top_n=5)
        snapshot = expert._pit_table[expert._pit_table["as_of"] == pd.Timestamp("2026-06-01")]
        filtered = expert._apply_v2_filters(snapshot, pd.Timestamp("2026-06-01"))
        assert len(filtered) == 4

    def test_v2_filter_tolerates_missing_maturity_column(self):
        # 无 maturity_date 列时 min_maturity_days 静默跳过（宽容）
        pit = self._pit_v2().drop(columns=["maturity_date"])
        expert = PITConvertibleDoubleLowExpert(pit, top_n=5, min_maturity_days=365)
        snapshot = expert._pit_table[expert._pit_table["as_of"] == pd.Timestamp("2026-06-01")]
        filtered = expert._apply_v2_filters(snapshot, pd.Timestamp("2026-06-01"))
        assert len(filtered) == 4

    def test_min_candidates_default_off(self):
        # 默认 None：不启用池规模下限（向后兼容）
        expert = PITConvertibleDoubleLowExpert(self._pit_v2(), top_n=5)
        assert expert.min_candidates is None

    def test_min_candidates_pool_semantics(self):
        # 池语义：阈值比较的是完整过滤链后的候选数（评级→溢价→中位→新债），
        # 不是 v2 过滤后的 snapshot 行数。4 只样本经 compute 同口径过滤后
        # （中位数过滤约砍半）剩余 < 4，min_candidates=4 应触发退出。
        expert = PITConvertibleDoubleLowExpert(
            self._pit_v2(), top_n=5, min_candidates=4
        )
        # 模拟 _snapshot 输出：as_of 当日行
        snapshot = expert._pit_table[expert._pit_table["as_of"] == pd.Timestamp("2026-06-01")].copy()
        from quant_research.backtest.convertible import _filter_double_low_pool
        pool = _filter_double_low_pool(
            snapshot,
            min_rating=expert.min_rating,
            max_premium=expert.max_premium,
            exclude_new_days=expert.exclude_new_days,
            max_price_median=True,
            as_of="2026-06-01",
        )
        assert len(pool) < 4  # 中位数过滤后不足 4 只
        assert expert.min_candidates is not None and len(pool) < expert.min_candidates

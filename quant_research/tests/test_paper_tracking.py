# -*- coding: utf-8 -*-
"""F1 单测：C2 paper tracking 季度回放三表产出

全部合成数据，不依赖网络、不依赖真实 PIT 快照。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from quant_research.config.schema import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    DataSource,
    TushareConfig,
)
from scripts.run_paper_tracking import (
    COST_CSV,
    EQUITY_CSV,
    FILLS_CSV,
    _quarter_bounds,
    record_quarter,
)


def _make_config(initial_capital: float = 100000.0) -> AppConfig:
    return AppConfig(
        data=DataConfig(primary=DataSource.BAOSTOCK),
        tushare=TushareConfig(token=""),
        backtest=BacktestConfig(initial_capital=initial_capital),
    )


def _make_pit(symbols_premium: dict[str, float], days: int = 64,
              start: str = "2024-01-02", base_price: float = 110.0) -> pd.DataFrame:
    """合成 PIT 长表：各标的固定日收益 + 差异化溢价率（双低排序可分辨）"""
    dates = pd.bdate_range(start, periods=days)
    rows = []
    for i, (sym, premium) in enumerate(symbols_premium.items()):
        ret = 0.001 - i * 0.0001  # 收益逐只递减，价格路径彼此不同
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


_PICK_PARAMS = dict(
    top_n=5, min_rating="A", max_premium=100.0,
    exclude_new_days=0, max_price_median=False,
    # 显式关闭生产闸（PICK_PARAMS 默认开启）：合成券池无 maturity 数据且仅 6 只
    max_conv_value=None, min_maturity_days=None, min_candidates=None,
)


class TestQuarterBounds:
    def test_bounds(self):
        start, end = _quarter_bounds("2026Q3")
        assert start == pd.Timestamp("2026-07-01")
        assert end == pd.Timestamp("2026-09-30")

    def test_bad_format(self):
        with pytest.raises(ValueError, match="YYYYQn"):
            _quarter_bounds("2026-Q3")
        with pytest.raises(ValueError, match="YYYYQn"):
            _quarter_bounds("2026Q5")


class TestRecordQuarter:
    def test_three_tables_written(self, tmp_path):
        pit = _make_pit({f"S{i}.SH": 10.0 + i for i in range(6)})
        summary = record_quarter(
            pit, "2024Q1", 100000.0, _make_config(), tmp_path,
            pick_params=_PICK_PARAMS)

        # 汇总：5 只等权目标
        assert summary["quarter"] == "2024Q1"
        assert len(summary["targets"]) == 5
        assert abs(sum(summary["targets"].values()) - 1.0) < 1e-9
        assert summary["n_fills"] >= 5

        # 表 1：equity_curve 覆盖全季逐日
        eq = pd.read_csv(tmp_path / EQUITY_CSV)
        assert (eq["quarter"] == "2024Q1").all()
        assert len(eq) >= 60
        assert (eq["value"] > 0).all()

        # 表 2：fills_vs_plan 逐单带计划权重与角色
        fills = pd.read_csv(tmp_path / FILLS_CSV)
        assert {"date", "symbol", "side", "value", "commission",
                "planned_weight", "role"} <= set(fills.columns)
        assert (fills["role"] == "planned").all()
        assert fills["planned_weight"].round(6).eq(0.2).all()
        assert len(fills) == summary["n_fills"]

        # 表 3：cost_vs_model 实际 = 模型（同一公式逐单复算）
        cost = pd.read_csv(tmp_path / COST_CSV)
        assert len(cost) == 1
        row = cost.iloc[0]
        assert int(row["completed_orders"]) == len(fills)
        assert abs(row["commission_diff"]) < 0.02
        # 10 万本金单笔 ~2 万 < 59,172 盈亏平衡点 → 佣金全部触 5 元地板
        assert row["actual_commission"] >= row["completed_orders"] * 5.0 - 0.01
        assert 0 < row["effective_rate_bp"] < 10

    def test_idempotent_rerun_no_duplicate(self, tmp_path):
        pit = _make_pit({f"S{i}.SH": 10.0 + i for i in range(6)})
        common = dict(pit_table=pit, quarter="2024Q1", capital=100000.0,
                      cfg=_make_config(), out_dir=tmp_path,
                      pick_params=_PICK_PARAMS)
        record_quarter(**common)
        record_quarter(**common)

        eq = pd.read_csv(tmp_path / EQUITY_CSV)
        assert len(eq) == eq["date"].nunique()  # 同季度重跑不产生重复行
        assert len(pd.read_csv(tmp_path / COST_CSV)) == 1

    def test_rejects_uncovered_quarter(self, tmp_path):
        """PIT 末端在季中 → 拒绝 record（季末才产出）"""
        pit = _make_pit({"S0.SH": 10.0, "S1.SH": 11.0}, days=20)  # ~2024-01-30
        with pytest.raises(ValueError, match="未覆盖全季"):
            record_quarter(pit, "2024Q1", 100000.0, _make_config(),
                           tmp_path, pick_params=_PICK_PARAMS)

    def test_rejects_future_quarter(self, tmp_path):
        pit = _make_pit({"S0.SH": 10.0, "S1.SH": 11.0}, days=20)
        with pytest.raises(ValueError, match="尚无 PIT 数据"):
            record_quarter(pit, "2030Q1", 100000.0, _make_config(),
                           tmp_path, pick_params=_PICK_PARAMS)


class TestExportPlan:
    """①-C：current_plan.json 信号契约导出"""

    def test_export_writes_contract_with_t1_close(self, tmp_path):
        from scripts.run_paper_tracking import export_current_plan

        pit = _make_pit({"SH113062": 0.001, "SZ123089": 0.002}, days=5)
        picks = {"SH113062": 0.5, "SZ123089": 0.5}
        out = export_current_plan(pit, picks, "2024-01-08", 10000.0,
                                  out_dir=tmp_path)
        plan = json.loads(out.read_text(encoding="utf-8"))
        assert plan["schema"] == "cb_current_plan_v2"
        assert plan["capital_base"] == 10000.0
        assert plan["latest_rebalance"] == "2024-01-08"
        assert abs(plan["targets"]["SH113062"]["weight"] - 0.5) < 1e-9
        assert plan["targets"]["SH113062"]["t1_close"] > 0  # PIT 末端价
        assert set(plan["targets"]) == {"SH113062", "SZ123089"}
        # E3：信号状态体系（executable/blocked/manual_review 分层）
        assert plan["targets"]["SH113062"]["signal_state"] == "executable"
        assert plan["targets"]["SH113062"]["price_date"] is not None
        assert plan["blocked"] == {} and plan["manual_review"] == {}

    def test_export_idempotent_overwrite(self, tmp_path):
        from scripts.run_paper_tracking import export_current_plan

        pit = _make_pit({"SH113062": 0.001}, days=5)
        picks = {"SH113062": 1.0}
        out1 = export_current_plan(pit, picks, "2024-01-08", 10000.0,
                                   out_dir=tmp_path)
        out2 = export_current_plan(pit, picks, "2024-01-08", 20000.0,
                                   out_dir=tmp_path)
        assert out1 == out2
        plan = json.loads(out2.read_text(encoding="utf-8"))
        assert plan["capital_base"] == 20000.0  # 覆盖而非追加

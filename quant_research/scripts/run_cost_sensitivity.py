# -*- coding: utf-8 -*-
"""A1：两段式转债双低基线的成本敏感性（1x/2x/4x）

ROADMAP §3.3 A1：判断"毛 alpha 能否转净 alpha"的唯一硬指标。
方法：同一选债计划（冻结的 target_weights，1x 基线产物）在三档
成本乘数下分别执行模拟——选债不受成本影响（研究计算与执行模拟
分离正是两段式的设计目的），只有成交费用变化。

成本乘数作用于：佣金率、最低佣金、印花税、过户费（转债实际只有
佣金+最低佣金两项生效，但乘数统一施加保持口径一致）。

输出：output/a1_cost_sensitivity.json + _table.csv，并写入实验注册。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest.convertible import load_pit_snapshot
from quant_research.backtest.engine import BacktestEngine
from quant_research.backtest.two_stage import (
    WeightTargetStrategy,
    compute_cb_target_weights,
    pit_to_feeds,
)
from quant_research.config import load_config
from quant_research.core.experiments import record_run
from quant_research.runner import sha256_file

logger = logging.getLogger("a1_cost_sensitivity")

# 与基线一致的选债参数（etf_mwu_cb_monthly.yaml v2 过滤口径）
PICK_PARAMS = dict(
    top_n=5,
    min_rating="AA-",
    max_premium=20.0,
    exclude_new_days=30,
    max_conv_value=135.0,
    min_maturity_days=365,
    min_candidates=15,
)

COST_MULTIPLIERS = (1, 2, 4)


def run_with_cost_multiplier(
    cfg,
    feeds: dict,
    target_weights: dict,
    multiplier: int,
    logger=None,
):
    """在指定成本乘数下对冻结的选债计划执行模拟。

    乘数施加于佣金率/最低佣金/印花税/过户费；转债现货免印花税与
    过户费（覆盖为 0 后乘 0 仍为 0），实际生效的是佣金与最低佣金。
    """
    bt_cfg = cfg.backtest
    overrides = {
        "commission": bt_cfg.commission_rate * multiplier,
        "min_commission": bt_cfg.min_commission * multiplier,
        "stamp_duty": bt_cfg.stamp_duty * multiplier,
        "transfer_fee": bt_cfg.transfer_fee * multiplier,
    }
    engine = BacktestEngine(cfg, commission_overrides=overrides, logger=logger)
    params = {
        "target_weights": target_weights,
        "convertible_symbols": tuple(feeds.keys()),
        "convertible_lot_size": 10,
        "lot_size": 10,
    }
    return engine.run(WeightTargetStrategy, params, feeds), overrides


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    cfg = load_config()
    pit_table = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")

    start, end = "2020-01-01", "2026-08-08"
    target_weights = compute_cb_target_weights(
        pit_table, start=start, end=end, rebalance="M", **PICK_PARAMS
    )
    picked = sorted({sym for picks in target_weights.values() for sym in picks})
    feeds = pit_to_feeds(pit_table, picked)
    logger.info("选债计划冻结: %d 次调仓, 并集 %d 只", len(target_weights), len(feeds))

    rows = []
    for multiplier in COST_MULTIPLIERS:
        result, overrides = run_with_cost_multiplier(cfg, feeds, target_weights, multiplier)
        rows.append({
            "cost_multiplier": multiplier,
            "commission_rate": round(overrides["commission"], 6),
            "min_commission": round(overrides["min_commission"], 2),
            "total_return": round(result.total_return, 6),
            "annual_return": round(result.annual_return, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "sharpe": round(result.sharpe, 6),
            "calmar": round(result.calmar, 6),
            "total_trades": result.total_trades,
            "completed_orders": result.completed_orders,
            "traded_value": round(result.traded_value, 2),
            "commission_paid": round(getattr(result, "commission_paid", float("nan")), 2),
            "turnover": round(result.turnover, 6),
            "final_value": round(result.final_value, 2),
        })
        logger.info(
            "成本 %dx: 总收益 %.2f%%, 回撤 %.2f%%, 换手 %.2fx, 手续费 %.2f",
            multiplier, result.total_return * 100, result.max_drawdown * 100,
            result.turnover, rows[-1]["commission_paid"],
        )

    # 衰减分析
    base = next(r for r in rows if r["cost_multiplier"] == 1)
    for row in rows:
        row["return_decay_vs_1x_pp"] = round(
            (row["total_return"] - base["total_return"]) * 100, 3
        )

    out_json = _PROJECT_ROOT / "output" / "a1_cost_sensitivity.json"
    out_csv = out_json.with_name("a1_cost_sensitivity_table.csv")
    report = {
        "task": "A1 cost sensitivity (ROADMAP §3.3 A1)",
        "start": start,
        "end": end,
        "pick_params": PICK_PARAMS,
        "rebalances": len(target_weights),
        "picked_union": len(feeds),
        "pit_sha256": sha256_file(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"),
        "rows": rows,
    }
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(rows).to_csv(out_csv, index=False, encoding="utf-8-sig")

    print(json.dumps(report, ensure_ascii=False, indent=1))

    record_run(
        params={
            "kind": "a1_cost_sensitivity",
            "start": start,
            "end": end,
            "multipliers": list(COST_MULTIPLIERS),
            **PICK_PARAMS,
            "pit_sha256": report["pit_sha256"],
        },
        metrics={
            f"total_return_{m}x": r["total_return"]
            for r, m in zip(rows, COST_MULTIPLIERS)
        },
        artifacts={"report": str(out_json), "table": str(out_csv)},
        tags={"kind": "a1_cost_sensitivity"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

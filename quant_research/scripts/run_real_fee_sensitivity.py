# -*- coding: utf-8 -*-
"""真实费率敏感性：用户的实际券商费率（华泰 万0.845，最低 5 元，不免五）

对月频/季频双低两段式回测各跑三档：
- "real"：真实费率（万0.845 + 最低 5 元）
- "1x"：原假设（万1 + 最低 5 元）——对照
- "2x"：原悲观假设（万2 + 最低 10 元）——对照
输出 output/real_fee_sensitivity.json
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

logger = logging.getLogger("real_fee_sensitivity")

PICK_PARAMS = dict(
    top_n=5,
    min_rating="AA-",
    max_premium=20.0,
    exclude_new_days=30,
    max_conv_value=135.0,
    min_maturity_days=365,
    min_candidates=15,
)
START, END = "2020-01-01", "2025-12-31"

FEE_SCENARIOS = {
    "real_huatai": {"commission": 0.0000845, "min_commission": 5.0},
    "assume_1x": {"commission": 0.0001, "min_commission": 5.0},
    "assume_2x": {"commission": 0.0002, "min_commission": 10.0},
}


def _run(cfg, feeds, weights, fee_overrides: dict, logger=None):
    bt_cfg = cfg.backtest
    overrides = {
        "commission": fee_overrides["commission"],
        "min_commission": fee_overrides["min_commission"],
        "stamp_duty": bt_cfg.stamp_duty,
        "transfer_fee": bt_cfg.transfer_fee,
    }
    engine = BacktestEngine(cfg, commission_overrides=overrides, logger=logger)
    params = {
        "target_weights": weights,
        "convertible_symbols": tuple(feeds.keys()),
        "convertible_lot_size": 10,
        "lot_size": 10,
    }
    return engine.run(WeightTargetStrategy, params, feeds)


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", force=True)
    cfg = load_config()
    pit_table = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")

    monthly = compute_cb_target_weights(
        pit_table, start=START, end=END, rebalance="M", **PICK_PARAMS)
    quarterly = compute_cb_target_weights(
        pit_table, start=START, end=END, rebalance="Q", **PICK_PARAMS)
    picked_m = sorted({s for p in monthly.values() for s in p})
    picked_q = sorted({s for p in quarterly.values() for s in p})
    feeds_m = pit_to_feeds(pit_table, picked_m)
    feeds_q = pit_to_feeds(pit_table, picked_q)

    rows = []
    for rebalance, weights, feeds in (("monthly", monthly, feeds_m),
                                      ("quarterly", quarterly, feeds_q)):
        for fee_name, fee_overrides in FEE_SCENARIOS.items():
            result = _run(cfg, feeds, weights, fee_overrides)
            avg_order_value = (result.traded_value / result.completed_orders
                               if result.completed_orders else 0.0)
            eff_rate = (result.commission_paid / result.traded_value * 10000
                        if result.traded_value else 0.0)
            rows.append({
                "rebalance": rebalance,
                "fee_scenario": fee_name,
                "commission_rate": fee_overrides["commission"],
                "min_commission": fee_overrides["min_commission"],
                "total_return": round(result.total_return, 6),
                "annual_return": round(result.annual_return, 6),
                "max_drawdown": round(result.max_drawdown, 6),
                "sharpe": round(result.sharpe, 6),
                "total_trades": result.total_trades,
                "completed_orders": result.completed_orders,
                "avg_order_value": round(avg_order_value, 0),
                "commission_paid": round(getattr(result, "commission_paid", float("nan")), 2),
                "effective_rate_bp": round(eff_rate, 2),
                "traded_value": round(result.traded_value, 2),
                "turnover": round(result.turnover, 6),
                "final_value": round(result.final_value, 2),
            })
            logger.info("%s @ %s: ret %.2f%%, commission %.0f (%.2f bp), orders %d",
                        rebalance, fee_name, result.total_return * 100,
                        result.commission_paid, eff_rate, result.completed_orders)

    df = pd.DataFrame(rows)
    report = {
        "task": "Real fee sensitivity (Huatai 0.00845% + min 5 CNY, no min-waiver)",
        "window": f"{START}..{END}",
        "pick_params": PICK_PARAMS,
        "breakeven_order_value": round(5.0 / 0.0000845, 0),
        "pit_sha256": sha256_file(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"),
        "rows": rows,
    }
    out_json = _PROJECT_ROOT / "output" / "real_fee_sensitivity.json"
    out_csv = out_json.with_name("real_fee_sensitivity_table.csv")
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"},
                     ensure_ascii=False, indent=1))
    print(df[["rebalance", "fee_scenario", "total_return", "max_drawdown",
              "completed_orders", "commission_paid", "effective_rate_bp"]].to_string(index=False))

    record_run(
        params={"kind": "real_fee_sensitivity", "window": f"{START}..{END}",
                "fee_scenarios": list(FEE_SCENARIOS),
                "pit_sha256": report["pit_sha256"]},
        metrics={f'{r["rebalance"]}_{r["fee_scenario"]}': r["total_return"] for r in rows},
        artifacts={"report": str(out_json), "table": str(out_csv)},
        tags={"kind": "real_fee_sensitivity"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

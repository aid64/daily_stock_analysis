# -*- coding: utf-8 -*-
"""H2：转债季频调仓（预注册实验，见 docs/h2-preregistration.md）

单参数改动：rebalance 月→季。2 次回测（1x/2x），月频基线引用 A1/A2 产物。
验收：2x 净收益 ≥0、年换仓 ≤6、换手降 ≥50%、在市月回撤不劣于基线 +5pp。
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
from quant_research.runner import ETF_COMMISSION, sha256_file

logger = logging.getLogger("h2_quarterly")

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
COST_MULTIPLIERS = (1, 2)

# A1/A2 产物的月频基线（1x/2x，2020-2025 全窗口径需重跑对齐——A1 是 2020-2026 窗，
# 为公平对比这里月频基线在同窗重跑）


def _run(cfg, feeds, weights, cost_multiplier: int, logger=None):
    bt_cfg = cfg.backtest
    overrides = {
        "commission": bt_cfg.commission_rate * cost_multiplier,
        "min_commission": bt_cfg.min_commission * cost_multiplier,
        "stamp_duty": bt_cfg.stamp_duty * cost_multiplier,
        "transfer_fee": bt_cfg.transfer_fee * cost_multiplier,
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
    logger.info("月频 %d 调仓 / 季频 %d 调仓（%d 并集 / %d 并集）",
                len(monthly), len(quarterly), len(feeds_m), len(feeds_q))

    rows = []
    for label, weights, feeds in (("monthly", monthly, feeds_m), ("quarterly", quarterly, feeds_q)):
        for cm in COST_MULTIPLIERS:
            result = _run(cfg, feeds, weights, cm)
            rows.append({
                "rebalance": label,
                "cost_multiplier": cm,
                "rebalance_events": len(weights),
                "total_return": round(result.total_return, 6),
                "annual_return": round(result.annual_return, 6),
                "max_drawdown": round(result.max_drawdown, 6),
                "sharpe": round(result.sharpe, 6),
                "total_trades": result.total_trades,
                "completed_orders": result.completed_orders,
                "traded_value": round(result.traded_value, 2),
                "commission_paid": round(getattr(result, "commission_paid", float("nan")), 2),
                "turnover": round(result.turnover, 6),
                "final_value": round(result.final_value, 2),
            })
            logger.info("%s @ %dx: ret %.2f%%, events %d, turnover %.2fx",
                        label, cm, result.total_return * 100, len(weights), result.turnover)

    df = pd.DataFrame(rows)
    m1 = df[(df.rebalance == "monthly") & (df.cost_multiplier == 1)].iloc[0]
    q_rows = df[df.rebalance == "quarterly"]
    q2 = q_rows[q_rows.cost_multiplier == 2].iloc[0]
    m2 = df[(df.rebalance == "monthly") & (df.cost_multiplier == 2)].iloc[0]

    turnover_drop = round((m1.turnover - q_rows[q_rows.cost_multiplier == 1].iloc[0].turnover)
                          / m1.turnover, 4)
    events_per_year_q = round(len(quarterly) / 6, 2)
    # 在市月回撤对比（双方都按各自 equity 的月回撤最大值，口径一致）
    verdict = {
        "net_2x_positive": bool(q2.total_return >= 0),
        "annual_events_le_6": bool(events_per_year_q <= 6),
        "turnover_drop_ge_50pct": bool(turnover_drop >= 0.50),
        "max_drawdown_not_worse_by_5pp": bool(
            q_rows[q_rows.cost_multiplier == 1].iloc[0].max_drawdown
            <= m1.max_drawdown + 0.05),
        "detail": {
            "monthly_1x": {k: m1[k] for k in ("total_return", "max_drawdown", "turnover")},
            "monthly_2x": {k: m2[k] for k in ("total_return",)},
            "quarterly_1x": {k: q_rows[q_rows.cost_multiplier == 1].iloc[0][k]
                             for k in ("total_return", "max_drawdown", "turnover")},
            "quarterly_2x": {k: q2[k] for k in ("total_return", "max_drawdown", "turnover")},
            "turnover_drop": turnover_drop,
            "quarterly_events_per_year": events_per_year_q,
        },
    }
    gates = [verdict["net_2x_positive"], verdict["annual_events_le_6"],
             verdict["turnover_drop_ge_50pct"], verdict["max_drawdown_not_worse_by_5pp"]]
    verdict["h2_pass"] = bool(all(gates))

    report = {
        "task": "H2 quarterly rebalance (preregistered: docs/h2-preregistration.md)",
        "window": f"{START}..{END}",
        "pick_params": PICK_PARAMS,
        "pit_sha256": sha256_file(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"),
        "verdict": verdict,
        "rows": rows,
    }
    out_json = _PROJECT_ROOT / "output" / "h2_quarterly_rebalance.json"
    out_csv = out_json.with_name("h2_quarterly_rebalance_table.csv")
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"},
                     ensure_ascii=False, indent=1))

    record_run(
        params={"kind": "h2_quarterly", "window": f"{START}..{END}",
                "pit_sha256": report["pit_sha256"]},
        metrics={"h2_pass": bool(verdict["h2_pass"]),
                 "quarterly_turnover_1x": verdict["detail"]["quarterly_1x"]["turnover"],
                 "quarterly_2x_return": verdict["detail"]["quarterly_2x"]["total_return"]},
        artifacts={"report": str(out_json), "table": str(out_csv)},
        tags={"kind": "h2_quarterly"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

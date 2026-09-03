# -*- coding: utf-8 -*-
"""F8 季频止损带实验（预注册 docs/f8-stoploss-preregistration.md）

对照：stop_loss_pct=None vs -0.08，其余与真实费率季频基线全同参。
输出 output/f8_stoploss.json，结果回填预注册文档第 5 节。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest.convertible import load_pit_snapshot
from quant_research.backtest.two_stage import (
    WeightTargetStrategy,
    compute_cb_target_weights,
    pit_to_feeds,
)
from quant_research.config import load_config
from quant_research.core.experiments import record_run
from quant_research.runner import sha256_file
from scripts.run_real_fee_sensitivity import END, FEE_SCENARIOS, PICK_PARAMS, START, _run

logger = logging.getLogger("f8_stoploss")

VARIANTS = (
    ("baseline", None),
    ("stop_8pct", -0.08),
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    from quant_research.core.holdout import assert_holdout_clean
    assert_holdout_clean(END, task="f8_stoploss")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", force=True)
    cfg = load_config()
    pit_table = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")

    weights = compute_cb_target_weights(
        pit_table, start=START, end=END, rebalance="Q", **PICK_PARAMS)
    picked = sorted({s for p in weights.values() for s in p})
    feeds = pit_to_feeds(pit_table, picked)
    real_fee = FEE_SCENARIOS["real_huatai"]

    rows = []
    for name, stop in VARIANTS:
        params_extra = {"stop_loss_pct": stop} if stop is not None else {}
        params = {
            "target_weights": weights,
            "convertible_symbols": tuple(feeds.keys()),
            "convertible_lot_size": 10,
            "lot_size": 10,
            **params_extra,
        }
        bt_cfg = cfg.backtest
        engine = BacktestEngineWith(cfg, real_fee)
        result = engine.run(WeightTargetStrategy, params, feeds)
        rows.append({
            "variant": name,
            "stop_loss_pct": stop,
            "total_return": round(result.total_return, 6),
            "annual_return": round(result.annual_return, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "sharpe": round(result.sharpe, 6),
            "turnover": round(result.turnover, 6),
            "completed_orders": result.completed_orders,
            "commission_paid": round(result.commission_paid, 2),
            "final_value": round(result.final_value, 2),
        })
        logger.info("%s: ret %.2f%%, dd %.2f%%, turnover %.2fx",
                    name, result.total_return * 100,
                    result.max_drawdown * 100, result.turnover)

    by = {r["variant"]: r for r in rows}
    base, stop = by["baseline"], by["stop_8pct"]
    gates = {
        "dd_improvement_pp": round((base["max_drawdown"] - stop["max_drawdown"]) * 100, 2),
        "gate1_dd_ge_2pp": (base["max_drawdown"] - stop["max_drawdown"]) >= 0.020,
        "annual_loss_pp": round((base["annual_return"] - stop["annual_return"]) * 100, 2),
        "gate2_annual_loss_le_1_5pp": (base["annual_return"] - stop["annual_return"]) <= 0.015,
        "turnover_increase_pct": round((stop["turnover"] / base["turnover"] - 1) * 100, 2),
        "gate3_turnover_le_50pct": stop["turnover"] / base["turnover"] - 1 <= 0.50,
    }
    gates["verdict"] = ("pass" if gates["gate1_dd_ge_2pp"] and gates["gate2_annual_loss_le_1_5pp"]
                        and gates["gate3_turnover_le_50pct"] else "fail")

    report = {
        "task": "F8 quarterly stop-loss band (preregistered)",
        "preregistration": "docs/f8-stoploss-preregistration.md",
        "window": f"{START}..{END}",
        "fee_scenario": "real_huatai",
        "pit_sha256": sha256_file(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"),
        "rows": rows,
        "gates": gates,
    }
    out = _PROJECT_ROOT / "output" / "f8_stoploss.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))

    record_run(
        params={"kind": "f8_stoploss", "window": f"{START}..{END}",
                "variants": [v[0] for v in VARIANTS], "fee": real_fee},
        metrics={"gate1_dd_improvement_pp": gates["dd_improvement_pp"],
                 "gate2_annual_loss_pp": gates["annual_loss_pp"],
                 "gate3_turnover_increase_pct": gates["turnover_increase_pct"],
                 "verdict_pass": gates["verdict"] == "pass"},
        artifacts={"report": str(out)},
        tags={"kind": "f8_stoploss"},
    )
    return 0


def BacktestEngineWith(cfg, fee):
    from quant_research.backtest.engine import BacktestEngine
    return BacktestEngine(
        cfg,
        commission_overrides={
            "commission": fee["commission"],
            "min_commission": fee["min_commission"],
        },
        logger=logger,
        record_fills=False,
    )


if __name__ == "__main__":
    sys.exit(main())

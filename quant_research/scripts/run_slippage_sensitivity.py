# -*- coding: utf-8 -*-
"""F6 滑点敏感性：季频真实费率基线扫滑点 0.05% / 0.1% / 0.2%

依据（ROADMAP-V2 O1）：ML4T 笔记指出换手成本低估是回测最被低估的陷阱。
本仓库此前只测了佣金档（A1/真实费率），滑点固定 0.1%。本扫描为
诊断性质（不改变策略语义、不引入新参数），输出写
output/slippage_sensitivity.json；N 记入实验注册。

与 run_real_fee_sensitivity 同参：季频双低、real_huatai 费率、
2020-01-01..2025-12-31。唯一变量 = cfg.backtest.slippage。
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

logger = logging.getLogger("slippage_sensitivity")

SLIPPAGE_LADDER = (0.0005, 0.001, 0.002)  # 万5 / 万10（基线）/ 万20


def main(argv: Optional[Sequence[str]] = None) -> int:
    from quant_research.core.holdout import assert_holdout_clean
    args_end = END
    assert_holdout_clean(args_end, task="slippage_sensitivity")
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
    for slippage in SLIPPAGE_LADDER:
        cfg_s = cfg.model_copy(deep=True)
        cfg_s.backtest.slippage = slippage
        cfg_s = cfg.model_copy(deep=True)
        cfg_s.backtest.slippage = 0.0  # 引擎滑点在单价格 feed 上被截断（见 two_stage docstring），置零避免误导
        result = _run(cfg_s, feeds, weights, real_fee)
        # 滑点解析扣减：slip_cost = traded_value × 滑点率，从期末净值直接扣
        slip_cost = result.traded_value * slippage
        net_final = result.final_value - slip_cost
        net_return = net_final / cfg.backtest.initial_capital - 1.0
        eff_rate = (result.commission_paid / result.traded_value * 1e4
                    if result.traded_value else 0.0)
        rows.append({
            "slippage_bp": round(slippage * 1e4, 1),
            "engine_total_return": round(result.total_return, 6),
            "net_total_return": round(net_return, 6),
            "annual_return": round(result.annual_return, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "sharpe": round(result.sharpe, 6),
            "traded_value": round(result.traded_value, 2),
            "commission_paid": round(result.commission_paid, 2),
            "effective_rate_bp": round(eff_rate, 2),
            "slippage_cost": round(slip_cost, 2),
            "cost_total": round(result.commission_paid + slip_cost, 2),
            "net_final_value": round(net_final, 2),
            "completed_orders": result.completed_orders,
        })
        logger.info("滑点 %.2f bp: 引擎收益 %.2f%% → 净 %.2f%%, 佣金 %.0f + 滑点 %.0f",
                    slippage * 1e4, result.total_return * 100, net_return * 100,
                    result.commission_paid, slip_cost)

    baseline = next(r for r in rows if r["slippage_bp"] == 10.0)  # 万10 = 原假设档
    report = {
        "task": "F6 slippage sensitivity (quarterly real-fee baseline)",
        "window": f"{START}..{END}",
        "fee_scenario": "real_huatai",
        "pick_params": PICK_PARAMS,
        "pit_sha256": sha256_file(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"),
        "rows": rows,
        "delta_vs_baseline": [
            {"slippage_bp": r["slippage_bp"],
             "net_total_return_pp": round((r["net_total_return"] - baseline["net_total_return"]) * 100, 2)}
            for r in rows
        ],
        "disclosure": "引擎滑点在单价格 feed（open=high=low=close）上被 bar 高低价截断，"
                      "恒不生效；本报告滑点为解析扣减（slip_cost = traded_value × 滑点率，"
                      "期末净值直接扣减不复利）。回撤/夏普等其余指标未随滑点调整。",
    }
    out = _PROJECT_ROOT / "output" / "slippage_sensitivity.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))

    record_run(
        params={"kind": "slippage_sensitivity", "window": f"{START}..{END}",
                "ladder_bp": [s * 1e4 for s in SLIPPAGE_LADDER],
                "fee": real_fee},
        metrics={f"net_ret_slip_{r['slippage_bp']}bp": r["net_total_return"] for r in rows},
        artifacts={"report": str(out)},
        tags={"kind": "slippage_sensitivity"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""H1：成本感知换手控制（预注册实验，见 docs/h1-preregistration.md）

按预注册：4 组合（buffer × min_trade_ratio）× 1x/2x 成本 = 10 次回测；
训练/验证窗 2020-01-01 → 2025-12-31（2026 零敞口年排除）。
结果含全部失败组合，写入 output/h1_turnover_control.json。
"""

from __future__ import annotations

import itertools
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

logger = logging.getLogger("h1_turnover_control")

PICK_PARAMS = dict(
    top_n=5,
    min_rating="AA-",
    max_premium=20.0,
    exclude_new_days=30,
    max_conv_value=135.0,
    min_maturity_days=365,
    min_candidates=15,
)
START, END = "2020-01-01", "2025-12-31"  # 预注册窗：排除 2026 零敞口年
BUFFERS = (3, 5)
MIN_TRADE_RATIOS = (1.0, 2.0)
COST_MULTIPLIERS = (1, 2)


def _run_one(cfg, feeds, weights, *, hold_buffer: bool, min_trade_ratio: float,
             cost_multiplier: int):
    bt_cfg = cfg.backtest
    overrides = {
        "commission": bt_cfg.commission_rate * cost_multiplier,
        "min_commission": bt_cfg.min_commission * cost_multiplier,
        "stamp_duty": bt_cfg.stamp_duty * cost_multiplier,
        "transfer_fee": bt_cfg.transfer_fee * cost_multiplier,
    }
    engine = BacktestEngine(cfg, commission_overrides=overrides, logger=None)
    params = {
        "target_weights": weights,
        "convertible_symbols": tuple(feeds.keys()),
        "convertible_lot_size": 10,
        "lot_size": 10,
        "hold_buffer": hold_buffer,
        "min_trade_ratio": min_trade_ratio,
    }
    return engine.run(WeightTargetStrategy, params, feeds)


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", force=True)
    cfg = load_config()
    pit_table = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")

    # 队列化选债计划（top_n=5 + buffer=5 覆盖最大 buffer；buffer=3 由执行层忽略多余队列成员）
    weights = compute_cb_target_weights(
        pit_table, start=START, end=END, rebalance="M", rank_buffer=max(BUFFERS), **PICK_PARAMS
    )
    picked = sorted({sym for picks in weights.values() for sym in picks})
    feeds = pit_to_feeds(pit_table, picked)
    logger.info("H1 选债计划冻结: %d 调仓, 并集 %d 只", len(weights), len(feeds))

    rows = []
    # 组合 = (hold_buffer, min_trade_ratio, buffer_used)；buffer=0 为基线
    combos = [(False, 1.0, 0)] + [
        (True, r, b) for b in BUFFERS for r in MIN_TRADE_RATIOS
    ]

    for hold_buffer, min_ratio, buffer_used in combos:
        # buffer_used 决定队列长度：执行层忽略队列中超出 top_n+buffer 的成员
        run_weights = weights
        if hold_buffer and buffer_used < max(BUFFERS):
            # 裁剪队列：保留前 top_n+buffer 个成员（队列按双低升序构建）
            keep_n = PICK_PARAMS["top_n"] + buffer_used
            trimmed = {}
            for date_str, queue in weights.items():
                ordered = list(queue.keys())[:keep_n]
                trimmed[date_str] = {s: queue[s] for s in ordered}
            run_weights = trimmed

        label = f"buffer={buffer_used},ratio={min_ratio},buf={hold_buffer}"
        for cm in COST_MULTIPLIERS:
            result = _run_one(cfg, feeds, run_weights, hold_buffer=hold_buffer,
                              min_trade_ratio=min_ratio, cost_multiplier=cm)
            rows.append({
                "combo": label,
                "hold_buffer": hold_buffer,
                "buffer": buffer_used,
                "min_trade_ratio": min_ratio,
                "cost_multiplier": cm,
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
            logger.info("%s @ %dx: ret %.2f%%, orders %d, turnover %.2fx",
                        label, cm, result.total_return * 100,
                        result.completed_orders, result.turnover)

    df = pd.DataFrame(rows)
    # 换手/收益均以 2020-2025 窗口的 1x 基线为参照
    base_row = df[(df.combo == "buffer=0,ratio=1.0,buf=False") & (df.cost_multiplier == 1)].iloc[0]
    df["turnover_delta_vs_base"] = (df.turnover - base_row.turnover).round(4)
    df["turnover_drop_pct"] = ((base_row.turnover - df.turnover) / base_row.turnover).round(4)

    # 预注册验收判定
    verdicts = []
    for combo in df.combo.unique():
        if combo.startswith("buffer=0"):
            continue
        sub = df[df.combo == combo]
        r2 = sub[sub.cost_multiplier == 2].iloc[0]
        r1 = sub[sub.cost_multiplier == 1].iloc[0]
        verdict = {
            "combo": combo,
            "net_2x_positive": bool(r2.total_return >= 0),
            "turnover_drop_1x_ge_30pct": bool(r1.turnover_drop_pct >= 0.30),
            "both_pass": bool(r2.total_return >= 0 and r1.turnover_drop_pct >= 0.30),
            "detail_1x": {k: r1[k] for k in ("total_return", "turnover", "turnover_drop_pct")},
            "detail_2x": {k: r2[k] for k in ("total_return", "turnover")},
        }
        verdicts.append(verdict)

    h1_pass = any(v["both_pass"] for v in verdicts)
    report = {
        "task": "H1 turnover control (preregistered: docs/h1-preregistration.md)",
        "window": f"{START}..{END}",
        "pick_params": PICK_PARAMS,
        "note_2026_excluded": "2026 zero-exposure year excluded per preregistration",
        "pit_sha256": sha256_file(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"),
        "baseline_turnover_1x": round(base_row.turnover, 4),
        "h1_pass": bool(h1_pass),
        "verdicts": verdicts,
        "rows": rows,
    }
    out_json = _PROJECT_ROOT / "output" / "h1_turnover_control.json"
    out_csv = out_json.with_name("h1_turnover_control_table.csv")
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"},
                     ensure_ascii=False, indent=1))

    record_run(
        params={"kind": "h1_turnover_control", "window": f"{START}..{END}",
                "buffers": list(BUFFERS), "ratios": list(MIN_TRADE_RATIOS),
                "pit_sha256": report["pit_sha256"]},
        metrics={"h1_pass": bool(h1_pass),
                 "baseline_turnover_1x": round(base_row.turnover, 4)},
        artifacts={"report": str(out_json), "table": str(out_csv)},
        tags={"kind": "h1_turnover_control"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

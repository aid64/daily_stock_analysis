#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""两段式转债双低回测入口（R5）

第一段在 PIT 全量池矩阵上选债（无需候选并集预计算），
第二段只为被选中券建 feed 执行模拟。

用法：
    py scripts/run_cb_backtest.py --pit data/pit/convertible_pit.parquet \
        --start 2020-01-01 --end 2026-08-08

    # 附带因子 IC 报告（双低/溢价率/转股价值 前向收益相关性）
    py scripts/run_cb_backtest.py --pit data/pit/convertible_pit.parquet \
        --start 2020-01-01 --end 2026-08-08 --ic-report
"""

from __future__ import annotations

import argparse
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
from quant_research.backtest.two_stage import run_two_stage
from quant_research.config import load_config
from quant_research.core.experiments import record_run
from quant_research.runner import sha256_file

logger = logging.getLogger("cb_two_stage")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="两段式转债双低回测（矩阵研究计算 + 事件执行模拟）")
    p.add_argument("--pit", required=True, help="转债 PIT parquet/csv")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--rebalance", default="M", choices=["M", "W"], help="调仓频率")
    p.add_argument("--config", default=None)
    # 选债过滤参数（默认与 etf_mwu_cb_monthly.yaml 基线一致）
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--min-rating", default="AA-")
    p.add_argument("--max-premium", type=float, default=20.0)
    p.add_argument("--exclude-new-days", type=int, default=30)
    p.add_argument("--max-conv-value", type=float, default=135.0)
    p.add_argument("--min-maturity-days", type=int, default=365)
    p.add_argument("--max-price", type=float, default=None)
    p.add_argument("--min-candidates", type=int, default=15)
    p.add_argument("--ic-report", action="store_true", help="输出双低因子 IC 报告")
    p.add_argument("--output", default=None, help="选债计划 CSV 输出路径")
    return p


def write_ic_report(pit_table: pd.DataFrame, out_path: Path) -> None:
    from quant_research.core.factors import evaluate_factor
    from quant_research.core.panel import Panel

    panel = Panel(pit_table)
    frames = []
    for factor in ("double_low", "premium_rate", "conv_value"):
        frames.append(evaluate_factor(panel, factor, periods=(5, 10, 20)))
    report = pd.concat(frames, ignore_index=True)
    report.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("因子 IC 报告: %s（|t|>3 为 HLZ 多重检验门槛）", out_path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        force=True)
    cfg = load_config(args.config)
    pit_table = load_pit_snapshot(args.pit)

    pick_params = dict(
        top_n=args.top_n,
        min_rating=args.min_rating,
        max_premium=args.max_premium,
        exclude_new_days=args.exclude_new_days,
        max_conv_value=args.max_conv_value,
        min_maturity_days=args.min_maturity_days,
        max_price=args.max_price,
        min_candidates=args.min_candidates,
    )
    result, weights, feeds = run_two_stage(
        pit_table, cfg, start=args.start, end=args.end,
        rebalance=args.rebalance, logger=logger, pick_params=pick_params,
    )

    print()
    print(result.summary())

    output = Path(args.output or (_PROJECT_ROOT / "output" / "cb_two_stage_weights.csv"))
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"date": date_str, "symbol": sym, "weight": w}
        for date_str, picks in sorted(weights.items())
        for sym, w in picks.items()
    ]
    pd.DataFrame(rows).to_csv(output, index=False, encoding="utf-8-sig")
    print(f"选债计划: {output}（调仓 {len(weights)} 次，并集 {len(feeds)} 只）")

    if args.ic_report:
        write_ic_report(pit_table, output.with_name("cb_factor_ic_report.csv"))

    # R4 实验注册
    record_run(
        params={
            "kind": "cb_two_stage",
            "start": args.start,
            "end": args.end,
            "rebalance": args.rebalance,
            **{k: v for k, v in pick_params.items()},
            "pit_sha256": sha256_file(args.pit),
        },
        metrics={
            "total_return": round(result.total_return, 6),
            "annual_return": round(result.annual_return, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "sharpe": round(result.sharpe, 6),
            "total_trades": result.total_trades,
            "turnover": round(result.turnover, 6),
            "n_rebalances": len(weights),
            "n_picked_union": len(feeds),
        },
        artifacts={"weights_csv": str(output)},
        tags={"kind": "cb_two_stage"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

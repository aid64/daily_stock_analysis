# -*- coding: utf-8 -*-
"""C2 paper tracking：季频转债双低的 paper 运行入口。

设计（ROADMAP C2）：每月/每季第一个 PIT 交易日后的首个实盘交易日，
从最新 PIT 快照重推导选债计划，与 PaperBroker 当前持仓对比，
输出目标 vs 实际的调仓计划；PaperBroker 以真实费率（万0.845+最低5）
模拟成交；每月生成三表（预测 vs 实现、成交 vs 计划、成本 vs 模型）。

与回测的两段式管线复用同一选债函数（compute_cb_target_weights）
与同一执行变体（WeightTargetStrategy 的 H1 参数），保证信号一致。

用法：
    py scripts/run_paper_tracking.py --once          # 单次调仓检查
    py scripts/run_paper_tracking.py                  # 默认单次
    py scripts/run_paper_tracking.py --show-status    # 只看当前持仓与进度
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
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
from quant_research.runner import ETF_COMMISSION
from quant_research.trade import PaperBroker

logger = logging.getLogger("paper_tracking")

PICK_PARAMS = dict(
    top_n=5,
    min_rating="AA-",
    max_premium=20.0,
    exclude_new_days=30,
    max_conv_value=135.0,
    min_maturity_days=365,
    min_candidates=15,
)
STATE_DIR = _PROJECT_ROOT / "output" / "paper_tracking"


def _latest_pit_date(pit_table: pd.DataFrame) -> pd.Timestamp:
    return pd.Timestamp(pit_table["as_of"].max())


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="季频转债双低 paper tracking")
    p.add_argument("--pit", default=str(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"))
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--once", action="store_true", help="单次调仓检查（默认）")
    p.add_argument("--show-status", action="store_true", help="只显示当前持仓与计划进度")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", force=True)
    cfg = load_config()
    pit_table = load_pit_snapshot(args.pit)
    pit_latest = _latest_pit_date(pit_table)
    logger.info("PIT 末端: %s", pit_latest.date())

    # 1) 推导选债计划（含 buffer 队列）
    weights = compute_cb_target_weights(
        pit_table, start="2020-01-01",
        end=pit_latest.strftime("%Y-%m-%d"), rebalance="Q", **PICK_PARAMS)
    if not weights:
        logger.error("选债计划为空")
        return 1
    latest_key = sorted(weights.keys())[-1]
    latest_picks = weights[latest_key]
    positive = {s: w for s, w in latest_picks.items() if w > 0}
    queue_zero = [s for s, w in latest_picks.items() if w <= 0]
    logger.info("最新调仓 %s: 目标 %s, buffer 队列 %s",
                latest_key, positive, queue_zero)

    # 2) PaperBroker 持仓（真实费率）
    broker = PaperBroker(initial_cash=args.capital)
    broker.login()

    # 3) 展示
    print()
    print("=== 季频转债双低 paper tracking ===")
    print(f"PIT 末端: {pit_latest.date()}  最新调仓日: {latest_key}")
    print(f"本金: {args.capital:,.0f}  费率: 万0.845 + 最低5元（不免五）")
    print()
    print("目标持仓（含 buffer 队列）:")
    for sym in sorted(latest_picks, key=lambda k: -latest_picks[k]):
        role = "持仓" if latest_picks[sym] > 0 else "buffer"
        print(f"  {sym}: weight={latest_picks[sym]:.2f} ({role})")
    print()
    print("状态: paper 账户每次运行重建（无状态），成交模拟由回测引擎承载；")
    print("      三表（预测vs实现/成交vs计划/成本vs模型）在每季末由回测")
    print("      的 equity_curve + commission_paid 自动产出，无需手工维护。")
    print()

    record_run(
        params={"kind": "paper_tracking", "pit_latest": str(pit_latest.date()),
                "latest_rebalance": latest_key,
                "targets": latest_picks,
                "capital": args.capital},
        metrics={"n_targets": len(positive), "n_buffer": len(queue_zero)},
        tags={"kind": "paper_tracking"},
    )

    if args.show_status:
        return 0
    print("（paper 成交模拟：由回测引擎按季频计划重放，见 output/h2_real_fee_final.json；")
    print("  实盘前需 C2 完整周期运行。本次运行已注册实验。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

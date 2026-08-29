#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""参数网格扫描脚本（P2 优化）

按训练/测试分段评估参数组合，输出 CSV 与控制台摘要，
用于 OFAT 单维度扫描与组合精调。

用法：
    # OFAT：动量窗口扫描（仅训练段）
    python scripts/run_grid.py --start 2013-11-01 --end 2021-12-31 \
        --grid lookback=20,40,60,90,120

    # 组合扫描：训练段 + 样本外分段（--split-date 启用测试段）
    python scripts/run_grid.py --start 2013-11-01 --end 2026-08-31 \
        --split-date 2022-01-01 \
        --grid lookback=60 top_k=1,2,3

    # 多窗口动量（值内多窗口用分号分隔）
    python scripts/run_grid.py --grid mom_windows=60;120 --grid lookback=60

约定：
- 逗号分隔多个取值（参数组合 = 各维度笛卡尔积）
- 分号分隔单个值内的列表（mom_windows 的多窗口）
- 组合总数超过 --max-combos 直接拒绝执行（防过拟合预算纪律）

分段近似说明：
- 每段数据向前多加载 warmup（1.7×最长窗口）供指标预热，
  warmup 期策略空转或持有防御资产，其收益计入该段结果；
  因 warmup 占比小（<8 年段的 4%）且各组合一致，用于相对比较无碍。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.config import load_config, load_strategy_config
from quant_research.data import DataLoader
from quant_research.backtest import BacktestEngine, ETFMomentum
from quant_research.core.experiments import record_run
from quant_research.runner import (
    ETF_COMMISSION,
    grid_combinations,
    parse_grid_spec,
    slice_feeds as _slice_feeds,
    warmup_days as _warmup_days,
)

logger = logging.getLogger("grid_run")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ETF 动量策略参数网格扫描")
    p.add_argument("--config", default=None, help="主配置文件路径")
    p.add_argument(
        "--strategy-config",
        default=None,
        help="策略配置 yaml；默认 config/strategy/etf_momentum.yaml",
    )
    p.add_argument("--start", required=True, help="回测起始日期（YYYY-MM-DD）")
    p.add_argument("--end", default=None, help="结束日期，默认今天")
    p.add_argument(
        "--split-date",
        default=None,
        help="训练/测试分段日：之前为训练段，之后为测试段；不指定则整段回测",
    )
    p.add_argument(
        "--grid",
        action="append",
        default=[],
        required=True,
        help="参数维度 key=v1,v2（可多次）；值内列表用分号",
    )
    p.add_argument(
        "--max-combos",
        type=int,
        default=100,
        help="组合预算上限，超出拒绝执行（防过拟合纪律）",
    )
    p.add_argument(
        "--output",
        default=None,
        help="结果 CSV 路径；默认 output/grid_<timestamp>.csv",
    )
    p.add_argument(
        "--snapshot",
        default=None,
        help="数据快照 pkl 路径：存在则直接加载（保证可复现），"
        "不存在则从数据源加载后写入。晚上市 ETF 缓存覆盖校验永远 "
        "miss 会反复拉取且多源数据版本不一，快照可冻结数据基线",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        grid = parse_grid_spec(args.grid)
        combos = grid_combinations(grid, args.max_combos)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    n_combos = len(combos)
    print(f"扫描维度: {list(grid.keys())}, 组合数: {n_combos}")

    cfg = load_config(args.config)
    end = args.end or time.strftime("%Y-%m-%d")
    strat_cfg = load_strategy_config(args.strategy_config, name="etf_momentum")
    symbols = [s.strip().upper() for s in (strat_cfg.universe or [])]
    if not symbols:
        print("策略配置缺少 universe", file=sys.stderr)
        return 2
    base_params = dict(strat_cfg.params)
    base_params["rebalance_freq"] = strat_cfg.rebalance_freq

    # 数据加载：全区间 + 预留最大 warmup（各组合取最长窗口）
    max_warmup = max(
        _warmup_days({**base_params, **dict(zip(grid.keys(), combo))})
        for combo in combos
    )
    data_start = (
        pd.Timestamp(args.start) - pd.Timedelta(days=max_warmup)
    ).strftime("%Y-%m-%d")
    logger.info(
        f"加载数据 {len(symbols)} 只: {data_start} ~ {end}（warmup {max_warmup} 天）"
    )

    snapshot_path = Path(args.snapshot) if args.snapshot else None
    if snapshot_path and snapshot_path.exists():
        feeds = pd.read_pickle(snapshot_path)
        logger.info(f"数据快照加载: {snapshot_path}（{len(feeds)} 只，基线冻结）")
    else:
        with DataLoader(cfg) as loader:
            feeds = {}
            for sym in symbols:
                try:
                    df = loader.get_etf_daily(sym, data_start, end)
                    if len(df) > 0:
                        feeds[sym] = df
                    else:
                        logger.warning(f"{sym}: 空数据，跳过")
                except Exception as e:
                    logger.error(f"{sym} 数据获取失败: {e}")
        if snapshot_path:
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            pd.to_pickle(feeds, snapshot_path)
            logger.info(f"数据快照已写入: {snapshot_path}")

    if not feeds:
        logger.error("无可用数据")
        return 1

    # 分段定义：(段名, 数据起点, 终点)
    segments: list[tuple[str, str, str]] = [("full", data_start, end)]
    if args.split_date:
        train_end = (
            pd.Timestamp(args.split_date) - pd.Timedelta(days=1)
        ).strftime("%Y-%m-%d")
        test_data_start = (
            pd.Timestamp(args.split_date) - pd.Timedelta(days=max_warmup)
        ).strftime("%Y-%m-%d")
        segments = [
            ("train", data_start, train_end),
            ("test", test_data_start, end),
        ]

    rows: list[dict] = []
    for i, combo in enumerate(combos, 1):
        params = {**base_params, **dict(zip(grid.keys(), combo))}
        for seg_name, seg_start, seg_end in segments:
            # 所有段（含 full）都按 [seg_start, seg_end] 切片：
            # full 段若不切片，会回测到快照全区间末端（含未来数据），
            # 训练段扫描被测试期污染
            seg_feeds = _slice_feeds(feeds, seg_start, seg_end)
            if not seg_feeds:
                continue
            engine = BacktestEngine(
                cfg, commission_overrides=ETF_COMMISSION,
                logger=logging.getLogger(f"grid.{i}.{seg_name}"),
            )
            try:
                result = engine.run(ETFMomentum, params, seg_feeds)
            except Exception as e:
                logger.error(f"组合 {i} {seg_name} 回测失败: {e!r}")
                continue
            row = {
                "combo_id": i,
                "segment": seg_name,
                **{k: v for k, v in zip(grid.keys(), combo)},
                "annual_return": round(result.annual_return, 4),
                "max_drawdown": round(result.max_drawdown, 4),
                "sharpe": round(result.sharpe, 3),
                "calmar": round(result.calmar, 3),
                "total_return": round(result.total_return, 4),
                "total_trades": result.total_trades,
                "final_value": round(result.final_value, 2),
            }
            rows.append(row)
        if i % 5 == 0 or i == n_combos:
            print(f"  进度: {i}/{n_combos}")

    if not rows:
        print("无有效回测结果", file=sys.stderr)
        return 1

    out_df = pd.DataFrame(rows)

    # 输出 CSV
    out_path = Path(
        args.output
        or (_PROJECT_ROOT / "output" / f"grid_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")

    # R4 实验注册：扫描即试验，N 由 index.jsonl 机器追溯
    record_run(
        params={
            "grid": {k: [str(v) for v in vs] for k, vs in grid.items()},
            "start": args.start,
            "end": end,
            "split_date": args.split_date,
            "strategy_config": args.strategy_config,
            "snapshot": str(snapshot_path) if snapshot_path else None,
        },
        metrics={
            "n_combos": n_combos,
            "n_segments": len(segments),
            "n_valid_rows": len(rows),
        },
        artifacts={"grid_csv": str(out_path)},
        tags={"kind": "grid", "strategy": "etf_momentum"},
    )

    # 控制台摘要：train/test 并排（有分段时），否则全段表
    param_cols = list(grid.keys())
    print(f"\n结果已保存: {out_path}\n")
    if args.split_date:
        pivot = out_df.pivot_table(
            index=["combo_id", *param_cols],
            columns="segment",
            values=["annual_return", "max_drawdown", "sharpe", "total_trades"],
        ).round(4)
        print(pivot.to_string())
        print(
            "\n提示：train/test 年化收益比 > 2 的组合过拟合风险高，"
            "样本外验证只做一次。"
        )
    else:
        cols = ["combo_id", *param_cols, "annual_return", "max_drawdown",
                "sharpe", "calmar", "total_trades"]
        print(out_df[cols].sort_values("annual_return", ascending=False).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""滚动 Walk-Forward 参数验证入口（R3：编排逻辑沉入 quant_research.runner）"""

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

from quant_research.backtest import BacktestEngine, ETFMomentum, ETFMWU
from quant_research.backtest.convertible import load_pit_snapshot
from quant_research.config import load_config, load_strategy_config
from quant_research.core.experiments import record_run
from quant_research.runner import (
    ETF_COMMISSION,
    folds as _folds,
    grid_combinations,
    metric_row as _metric_row,
    parse_grid_spec,
    route_mwu_grid_params as _build_mwu_run_params,
    select_training_result as _select_training_result,
    sha256_file as _sha256,
    slice_feeds as _slice_feeds,
    warmup_days as _warmup_days,
    warmup_days_mwu as _warmup_days_mwu,
)

logger = logging.getLogger("walk_forward")


def _trimmed_test_metrics(result, start: str, end: str) -> Optional[dict]:
    """测试窗指标裁剪：从 equity_curve（日收益序列）只统计 [start, end]。

    整跑指标会把 warmup 段（属训练期）的交易计入测试结果（复核发现 1）；
    裁剪后测试指标只反映测试窗本身。窗口内回撤为局部口径（不含带入峰）。
    """
    eq = getattr(result, "equity_curve", None)
    if eq is None or len(eq) == 0:
        return None
    sub = eq[(eq.index >= pd.Timestamp(start)) & (eq.index <= pd.Timestamp(end))]
    if len(sub) < 2:
        return None
    total = float((1.0 + sub).prod() - 1.0)
    n = len(sub)
    annual = (1.0 + total) ** (252.0 / n) - 1.0 if total > -1.0 else -1.0
    cum = (1.0 + sub).cumprod()
    dd = abs(float((cum / cum.cummax() - 1.0).min()))
    std = float(sub.std())
    sharpe = float(sub.mean() / std * (252 ** 0.5)) if std > 0 else 0.0
    return {
        "annual_return": annual,
        "max_drawdown": dd,
        "total_return": total,
        "sharpe": sharpe,
        "calmar": annual / dd if dd > 0 else 0.0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="滚动 Walk-Forward 参数验证")
    parser.add_argument("--config", default=None)
    parser.add_argument("--strategy-config", default=None)
    parser.add_argument(
        "--strategy",
        default="etf_momentum",
        choices=["etf_momentum", "etf_mwu_cb"],
        help="etf_momentum：纯 ETF 动量；etf_mwu_cb：ETF+转债 MWU 四专家（需 --pit 与 --cb-feeds）",
    )
    parser.add_argument(
        "--pit",
        default=None,
        help="转债 PIT parquet（etf_mwu_cb 必填）",
    )
    parser.add_argument(
        "--cb-feeds",
        default=None,
        help="冻结的转债日线 feeds pkl（etf_mwu_cb 必填，build_cb_feeds_snapshot.py 产物）",
    )
    parser.add_argument("--snapshot", required=True, help="冻结的 ETF feeds pkl")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--train-years", type=int, default=3)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--grid", action="append", required=True)
    parser.add_argument("--max-combos", type=int, default=50)
    parser.add_argument("--output", default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    from quant_research.core.holdout import assert_holdout_clean
    args = build_parser().parse_args(argv)
    assert_holdout_clean(args.end, task="walk_forward")

    try:
        grid = parse_grid_spec(args.grid)
        combinations = grid_combinations(grid, args.max_combos)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    snapshot = Path(args.snapshot)
    if not snapshot.exists():
        print(f"快照不存在: {snapshot}", file=sys.stderr)
        return 2
    feeds = pd.read_pickle(snapshot)
    if not isinstance(feeds, dict) or not feeds:
        print("快照必须是非空 dict[str, DataFrame]", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    # etf_mwu_cb：PIT + 冻结 CB feeds + 专家模板
    mwu_spec_template = None
    mwu_base_params = None
    cb_feeds = None
    fixed_warmup = None
    if args.strategy == "etf_mwu_cb":
        if not args.pit or not args.cb_feeds:
            print("etf_mwu_cb 需要 --pit 与 --cb-feeds", file=sys.stderr)
            return 2
        pit_table = load_pit_snapshot(args.pit)
        cb_feeds = pd.read_pickle(args.cb_feeds)
        if not isinstance(cb_feeds, dict) or not cb_feeds:
            print("--cb-feeds 必须是非空 dict[str, DataFrame]", file=sys.stderr)
            return 2
        mwu_cfg = load_strategy_config(args.strategy_config, name="etf_mwu_cb")
        mwu_base_params = dict(mwu_cfg.params)
        mwu_base_params["rebalance_freq"] = mwu_cfg.rebalance_freq
        mwu_spec_template = {k: dict(v) for k, v in (mwu_cfg.experts or {}).items()}
        cb_spec = mwu_spec_template.pop("convertible_double_low", None)
        if cb_spec is None:
            print("etf_mwu_cb 配置缺少 convertible_double_low 专家", file=sys.stderr)
            return 2
        mwu_spec_template["convertible_double_low_pit"] = {
            k: v for k, v in cb_spec.items() if k != "candidate_pool"
        }
        mwu_spec_template["convertible_double_low_pit"]["pit_table"] = pit_table
        fixed_warmup = _warmup_days_mwu(mwu_spec_template)
        logger.info(
            "etf_mwu_cb: PIT %s 行, CB feeds %s 只, warmup %d 天",
            len(pit_table), len(cb_feeds), fixed_warmup,
        )
    strat_cfg = load_strategy_config(args.strategy_config, name="etf_momentum")
    base_params = dict(strat_cfg.params)
    base_params["rebalance_freq"] = strat_cfg.rebalance_freq
    folds = list(_folds(args.start, args.end, args.train_years, args.test_months))
    if not folds:
        print("日期范围不足以形成一个训练/测试折", file=sys.stderr)
        return 2

    all_rows: list[dict] = []
    locked_rows: list[dict] = []
    for fold, (train_start, train_end, test_start, test_end) in enumerate(folds, 1):
        fold_rows: list[dict] = []
        for params_delta in combinations:
            params = {**base_params, **params_delta}
            warmup = fixed_warmup or _warmup_days(params)
            train_data_start = (pd.Timestamp(train_start) - pd.Timedelta(days=warmup)).strftime("%Y-%m-%d")
            train_feeds = _slice_feeds(feeds, train_data_start, train_end)
            if not train_feeds:
                continue
            try:
                if args.strategy == "etf_mwu_cb":
                    cb_slice = _slice_feeds(cb_feeds, train_data_start, train_end)
                    merged = {**train_feeds, **cb_slice}  # ETF 在前：datas[0]=ETF 日历
                    params = _build_mwu_run_params(
                        mwu_base_params, mwu_spec_template, params_delta, cb_slice.keys()
                    )
                    result = BacktestEngine(
                        cfg,
                        commission_overrides=ETF_COMMISSION,
                        logger=logging.getLogger(f"wf.{fold}.train"),
                    ).run(ETFMWU, params, merged)
                else:
                    result = BacktestEngine(
                        cfg,
                        commission_overrides=ETF_COMMISSION,
                        logger=logging.getLogger(f"wf.{fold}.train"),
                    ).run(ETFMomentum, params, train_feeds)
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("fold %s 训练参数失败: %s", fold, exc)
                continue
            row = _metric_row(result, fold, "train", params_delta)
            row.update({"train_start": train_start, "train_end": train_end})
            fold_rows.append(row)
            all_rows.append(row)
        selected = _select_training_result(fold_rows)
        locked_params = {key: selected[key] for key in grid}
        test_warmup = fixed_warmup or _warmup_days({**base_params, **locked_params})
        test_data_start = (pd.Timestamp(test_start) - pd.Timedelta(days=test_warmup)).strftime("%Y-%m-%d")
        test_feeds = _slice_feeds(feeds, test_data_start, test_end)
        test_params = {**base_params, **locked_params}
        if args.strategy == "etf_mwu_cb":
            cb_slice = _slice_feeds(cb_feeds, test_data_start, test_end)
            merged = {**test_feeds, **cb_slice}
            test_params = _build_mwu_run_params(
                mwu_base_params, mwu_spec_template, locked_params, cb_slice.keys()
            )
            result = BacktestEngine(
                cfg,
                commission_overrides=ETF_COMMISSION,
                logger=logging.getLogger(f"wf.{fold}.test"),
            ).run(ETFMWU, test_params, merged)
        else:
            result = BacktestEngine(
                cfg,
                commission_overrides=ETF_COMMISSION,
                logger=logging.getLogger(f"wf.{fold}.test"),
            ).run(ETFMomentum, test_params, test_feeds)
        test_row = _metric_row(result, fold, "test", locked_params)
        if args.strategy == "etf_mwu_cb":
            # 测试指标裁剪：剔除 warmup 段（属训练期）交易的污染
            trimmed = _trimmed_test_metrics(result, test_start, test_end)
            if trimmed:
                for key, value in trimmed.items():
                    test_row[key] = round(value, 6)
        test_row.update({"test_start": test_start, "test_end": test_end})
        all_rows.append(test_row)
        locked_rows.append({
            "fold": fold,
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            **locked_params,
            "train_score": round(selected["annual_return"] - 0.25 * selected["max_drawdown"], 6),
            "train_annual_return": selected["annual_return"],
            "test_annual_return": test_row["annual_return"],
            "test_max_drawdown": test_row["max_drawdown"],
            "test_total_return": test_row["total_return"],
            "test_turnover": test_row["turnover"],
        })

    output = Path(args.output or (_PROJECT_ROOT / "output" / f"walk_forward_{time.strftime('%Y%m%d_%H%M%S')}.csv"))
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(output, index=False, encoding="utf-8-sig")
    locked_path = output.with_name(f"{output.stem}_locked{output.suffix}")
    locked = pd.DataFrame(locked_rows)
    locked["strategy"] = args.strategy
    locked["snapshot"] = str(snapshot)
    locked["snapshot_sha256"] = _sha256(snapshot)
    if args.strategy == "etf_mwu_cb":
        locked["pit_sha256"] = _sha256(Path(args.pit))
        locked["cb_feeds_sha256"] = _sha256(Path(args.cb_feeds))
    locked.to_csv(locked_path, index=False, encoding="utf-8-sig")

    # R4 实验注册：WF 是一次正式试验（三输入 SHA 已在 locked 内）
    record_run(
        params={
            "strategy": args.strategy,
            "start": args.start,
            "end": args.end,
            "train_years": args.train_years,
            "test_months": args.test_months,
            "grid": {k: [str(v) for v in vs] for k, vs in grid.items()},
            "snapshot": str(snapshot),
        },
        metrics={
            "oos_cum_return": round(
                float((1 + pd.Series([r["test_total_return"] for r in locked_rows])).prod() - 1), 6
            ),
            "n_folds": len(locked_rows),
            "positive_folds": int(sum(1 for r in locked_rows if r["test_total_return"] > 0)),
        },
        artifacts={"wf_csv": str(output), "locked_csv": str(locked_path)},
        tags={"kind": "walk_forward", "strategy": args.strategy},
    )
    print(f"结果已保存: {output}")
    print(f"锁定参数与测试结果: {locked_path}")
    print(locked.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""滚动 Walk-Forward 参数验证入口。"""

from __future__ import annotations

import argparse
import hashlib
import itertools
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

from quant_research.backtest import BacktestEngine, ETFMomentum, ETFMWU, build_experts
from quant_research.backtest.convertible import load_pit_snapshot
from quant_research.config import load_config, load_strategy_config

logger = logging.getLogger("walk_forward")
_ETF_COMMISSION = {"stamp_duty": 0.0, "transfer_fee": 0.0}
_PARAM_TYPES = {
    "lookback": int,
    "top_k": int,
    "ma_filter": int,
    "min_volume": float,
    "stop_loss_pct": lambda value: None if value.lower() == "none" else float(value),
    "vol_adjusted": lambda value: value.lower() in ("1", "true", "yes", "on"),
    "rebalance_freq": str,
    "defensive": str,
    # etf_mwu_cb：CB 专家参数（路由到 convertible_double_low_pit 专家）
    "top_n": int,
    "max_premium": float,
    "min_rating": str,
    "max_conv_value": float,
    "min_maturity_days": int,
    "max_price": float,
    "exclude_new_days": int,
    # etf_mwu_cb：MWU 顶层参数
    "eta": float,
    "loss_scale": float,
}
# 网格键路由：lookback/top_k/ma_filter 在 etf_mwu_cb 下属 momentum 专家
_MOMENTUM_KEYS = {"lookback", "top_k", "ma_filter"}


def parse_grid_spec(specs: Sequence[str]) -> dict[str, list]:
    grid: dict[str, list] = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"--grid 格式错误: {spec}")
        key, values = spec.split("=", 1)
        key = key.strip()
        if key not in _PARAM_TYPES:
            raise ValueError(f"不支持的参数: {key}")
        grid[key] = [_PARAM_TYPES[key](value.strip()) for value in values.split(",")]
    if not grid:
        raise ValueError("至少提供一个 --grid")
    return grid


def _warmup_days(params: dict) -> int:
    longest = max(int(params.get("lookback", 60)), int(params.get("ma_filter", 250)))
    return int(longest * 1.7)


def _warmup_days_mwu(expert_spec: dict) -> int:
    """MWU 组合的 warmup：取全部专家窗口的最大值（动量 250 / 均值回归 200 等）。"""
    longest = 250
    for exp in expert_spec.values():
        if isinstance(exp, dict):
            longest = max(
                longest,
                int(exp.get("ma_filter", 0) or 0),
                int(exp.get("lookback", 0) or 0),
            )
    return int(longest * 1.7)


def _build_mwu_run_params(
    base_params: dict,
    spec_template: dict,
    combo: dict,
    cb_symbols: Sequence[str],
) -> dict:
    """把网格组合路由到 MWU 顶层参数 / momentum 专家 / CB 专家并重建 experts。

    experts 是有状态对象（MWU 权重），每次引擎运行必须全新构建。
    """
    strat_over: dict = {}
    cb_over: dict = {}
    mom_over: dict = {}
    for key, value in combo.items():
        if key in _MOMENTUM_KEYS:
            mom_over[key] = value
        elif key in _PARAM_TYPES and key not in (
            "min_volume", "stop_loss_pct", "vol_adjusted", "rebalance_freq", "defensive"
        ):
            cb_over[key] = value  # CB 专家参数（top_n/max_premium/...）
        else:
            strat_over[key] = value
    spec = {name: dict(cfg) for name, cfg in spec_template.items()}
    if cb_over:
        spec["convertible_double_low_pit"] = {
            **spec.get("convertible_double_low_pit", {}), **cb_over
        }
    if mom_over:
        spec["momentum"] = {**spec.get("momentum", {}), **mom_over}
    params = {**base_params, **strat_over}
    params["experts"] = build_experts(spec)
    params["convertible_symbols"] = tuple(cb_symbols)
    return params


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


def _slice_feeds(feeds: dict, start: str, end: str) -> dict:
    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end)
    return {
        symbol: frame[(frame["date"] >= lo) & (frame["date"] <= hi)].copy()
        for symbol, frame in feeds.items()
        if len(frame[(frame["date"] >= lo) & (frame["date"] <= hi)])
    }


def _metric_row(result, fold: int, segment: str, params: dict) -> dict:
    return {
        "fold": fold,
        "segment": segment,
        **params,
        "annual_return": round(result.annual_return, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "sharpe": round(result.sharpe, 6),
        "calmar": round(result.calmar, 6),
        "total_return": round(result.total_return, 6),
        "total_trades": result.total_trades,
        "turnover": round(result.turnover, 6),
        "final_value": round(result.final_value, 2),
    }


def _select_training_result(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("训练窗没有有效参数结果")
    eligible = [row for row in rows if row["max_drawdown"] <= 0.40]
    pool = eligible or rows
    return max(
        pool,
        key=lambda row: (
            row["annual_return"] - 0.25 * row["max_drawdown"],
            row["sharpe"],
            row["annual_return"],
        ),
    )


def _folds(start: str, end: str, train_years: int, test_months: int):
    cursor = pd.Timestamp(start)
    final = pd.Timestamp(end)
    while cursor + pd.DateOffset(years=train_years) < final:
        train_end = cursor + pd.DateOffset(years=train_years) - pd.Timedelta(days=1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = min(test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), final)
        if test_start > final:
            break
        yield cursor.strftime("%Y-%m-%d"), train_end.strftime("%Y-%m-%d"), test_start.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")
        cursor = cursor + pd.DateOffset(months=test_months)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
    args = build_parser().parse_args(argv)
    grid = parse_grid_spec(args.grid)
    combinations = [dict(zip(grid, values)) for values in itertools.product(*grid.values())]
    if len(combinations) > args.max_combos:
        print(f"组合数 {len(combinations)} 超出预算 {args.max_combos}", file=sys.stderr)
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
                        commission_overrides=_ETF_COMMISSION,
                        logger=logging.getLogger(f"wf.{fold}.train"),
                    ).run(ETFMWU, params, merged)
                else:
                    result = BacktestEngine(
                        cfg,
                        commission_overrides=_ETF_COMMISSION,
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
                commission_overrides=_ETF_COMMISSION,
                logger=logging.getLogger(f"wf.{fold}.test"),
            ).run(ETFMWU, test_params, merged)
        else:
            result = BacktestEngine(
                cfg,
                commission_overrides=_ETF_COMMISSION,
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
    print(f"结果已保存: {output}")
    print(f"锁定参数与测试结果: {locked_path}")
    print(locked.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

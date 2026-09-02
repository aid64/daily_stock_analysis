#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回测过拟合审计入口（P0-2）：宽配置矩阵逐期收益 → DSR / HLZ / PBO 诊断

预注册门槛（2026-08-29 修订，见架构文档 6.2.8）：
- 主门槛：所选配置 DSR < 0.5 → 无技能（噪声），路线关闭调参
- 辅助：HLZ |t| ≤ 3 的新配置不认为显著
- PBO 降级为诊断参考（固定矩阵互补恒等式诅咒的有限样本效应已实测，
  不作为自动门槛）

用法：
    py scripts/run_overfit_audit.py --snapshot output/feeds_snapshot.pkl \
        --start 2019-01-01 --end 2026-08-08 \
        --grid lookback=20,40,60,90,120,180 --grid top_k=1,2,3 --grid ma_filter=200 \
        --n-trials 44 --route etf_momentum
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest import BacktestEngine, ETFMomentum
from quant_research.config import load_config
from quant_research.core.experiments import count_trials, record_run, trial_status_breakdown
from quant_research.core.overfit_audit import cscv_pbo, deflated_sharpe, hlz_t_stat
from quant_research.runner import ETF_COMMISSION, parse_grid_spec, grid_combinations, sha256_file

logger = logging.getLogger("overfit_audit")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="P0-2 回测过拟合审计（DSR/HLZ 主门槛 + PBO 诊断）")
    p.add_argument("--snapshot", required=True, help="冻结的 ETF feeds pkl")
    p.add_argument("--start", required=True, help="数据起点（含 warmup）")
    p.add_argument("--end", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--strategy-config", default=None)
    p.add_argument("--grid", action="append", required=True)
    p.add_argument("--max-combos", type=int, default=50)
    p.add_argument("--n-trials", type=int, default=None,
                   help="诚实统计的历史试验数 N；省略时自动取 output/runs/index.jsonl "
                        "中 status=completed 的行数（F4），显式传入则覆盖")
    p.add_argument("--route", default="etf_momentum")
    p.add_argument("--output", default=None)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    from quant_research.core.holdout import assert_holdout_clean
    args = build_parser().parse_args(argv)
    assert_holdout_clean(args.end, task="overfit_audit")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        force=True)
    try:
        grid = parse_grid_spec(args.grid)
        combos = grid_combinations(grid, args.max_combos)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    strat_cfg_path = args.strategy_config
    from quant_research.config import load_strategy_config
    strat_cfg = load_strategy_config(strat_cfg_path, name="etf_momentum")
    base_params = dict(strat_cfg.params)
    base_params["rebalance_freq"] = strat_cfg.rebalance_freq

    # F4：N 自动来自实验注册（只数 completed），--n-trials 显式传入则覆盖。
    # N 来源与 status 明细一并写进报告，防止 N 被静默洗小。
    if args.n_trials is not None:
        n_trials, n_trials_source = args.n_trials, "cli_override"
    else:
        n_trials = count_trials()
        n_trials_source = "experiments_index_completed"
    status_breakdown = trial_status_breakdown()
    if n_trials < 2:
        print(f"试验数 N={n_trials} < 2，DSR 无意义——检查实验注册或显式传 --n-trials",
              file=sys.stderr)
        return 2
    logger.info("N = %d（来源 %s），status 明细 %s", n_trials, n_trials_source, status_breakdown)

    feeds = pd.read_pickle(Path(args.snapshot))
    if not isinstance(feeds, dict) or not feeds:
        print("快照必须是非空 dict[str, DataFrame]", file=sys.stderr)
        return 2

    # 逐配置整段回测，收集逐日收益
    return_cols: dict[str, pd.Series] = {}
    rows: list[dict] = []
    for i, combo in enumerate(combos, 1):
        params = {**base_params, **combo}
        label = "|".join(f"{k}={v}" for k, v in combo.items())
        try:
            result = BacktestEngine(
                cfg, commission_overrides=ETF_COMMISSION,
                logger=logging.getLogger(f"audit.{i}"),
            ).run(ETFMomentum, params, feeds)
        except Exception as exc:  # 单配置失败不拖垮审计
            logger.warning("配置 %s 失败: %r", label, exc)
            continue
        eq = result.equity_curve
        if eq is None or len(eq) == 0:
            continue
        label = "|".join(f"{k}={v}" for k, v in combo.items())
        return_cols[label] = eq
        rows.append({
            "config": label,
            **combo,
            "total_return": round(result.total_return, 6),
            "annual_return": round(result.annual_return, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "sharpe": round(result.sharpe, 6),
        })
        logger.info("[%d/%d] %s → 总收益 %.2f%%, Sharpe %.2f",
                    i, len(combos), label, result.total_return * 100, result.sharpe)

    if len(return_cols) < 2:
        print("有效配置不足 2 个，无法审计", file=sys.stderr)
        return 1

    matrix = pd.DataFrame(return_cols).dropna(axis=0, how="any")
    logger.info("收益矩阵: %d 期 × %d 配置（共同对齐）", *matrix.shape)

    # HLZ 逐配置 t 统计量
    for label in matrix.columns:
        t = hlz_t_stat(matrix[label].to_numpy())
        row = next(r for r in rows if r["config"] == label)
        row["hlz_t"] = round(t, 4)
        row["hlz_significant"] = abs(t) > 3.0

    # DSR（所选配置 = 日 Sharpe 最高者；N 含历史试验）
    daily_sharpes = {label: float(matrix[label].mean() / matrix[label].std(ddof=1))
                     for label in matrix.columns}
    best_label = max(daily_sharpes, key=daily_sharpes.get)
    best_ret = matrix[best_label]
    sr_variance = float(np.var(list(daily_sharpes.values()), ddof=1))
    dsr = deflated_sharpe(
        sharpe=daily_sharpes[best_label],
        n_periods=len(matrix),
        n_trials=n_trials,
        sr_variance=sr_variance,
        skewness=float(best_ret.skew()),
        kurtosis=float(best_ret.kurt()),  # 超额峰度
    )
    logger.info("DSR(所选 %s) = %.4f（N=%d，SR 方差 %.2e）→ %s",
                best_label, dsr, n_trials, sr_variance,
                "无技能（噪声），路线关闭调参" if dsr < 0.5 else "通过主门槛")

    # PBO 诊断（明确降级：有限样本互补恒等式诅咒已实测，仅供横向参考）
    try:
        pbo = cscv_pbo(matrix.to_numpy())
        pbo_diag = {"pbo": round(pbo["pbo"], 4), "n_combinations": pbo["n_combinations"],
                    "note": "诊断参考：固定矩阵互补恒等式诅咒使有限样本 PBO 系统性偏高，不作为门槛"}
    except ValueError as exc:
        pbo_diag = {"error": str(exc)}

    report = {
        "route": args.route,
        "n_trials_honest": n_trials,
        "n_trials_source": n_trials_source,
        "trial_status_breakdown": status_breakdown,
        "n_configs_in_matrix": int(matrix.shape[1]),
        "n_periods": int(matrix.shape[0]),
        "selected_config": best_label,
        "dsr": round(dsr, 4),
        "gate_dsr_pass": bool(dsr >= 0.5),
        "sr_variance_across_configs": sr_variance,
        "pbo_diagnostic": pbo_diag,
        "snapshot_sha256": sha256_file(args.snapshot),
    }

    output = Path(args.output or (_PROJECT_ROOT / "output" / f"overfit_audit_{args.route}.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(rows).to_csv(output.with_name(output.stem + "_configs.csv"),
                              index=False, encoding="utf-8-sig")
    print(json.dumps(report, ensure_ascii=False, indent=1))

    record_run(
        params={"kind": "overfit_audit", "route": args.route,
                "grid": {k: [str(v) for v in vs] for k, vs in grid.items()},
                "n_trials": n_trials, "n_trials_source": n_trials_source,
                "start": args.start, "end": args.end},
        metrics={"dsr": round(dsr, 4), "gate_pass": bool(dsr >= 0.5),
                 "n_configs": int(matrix.shape[1]), "n_periods": int(matrix.shape[0])},
        artifacts={"report": str(output)},
        tags={"kind": "overfit_audit", "strategy": args.route},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""C2 paper tracking：季频转债双低的 paper 运行入口。

设计（ROADMAP C2 / F1）：每月/每季第一个 PIT 交易日后的首个实盘交易日，
从最新 PIT 快照重推导选债计划，与 PaperBroker 当前持仓对比，
输出目标 vs 实际的调仓计划；PaperBroker 以真实费率（万0.845+最低5）
模拟成交；季末 `--record` 回放该季度并产出三表写入 output/paper_tracking/：

1. equity_curve.csv   —— 季内逐日权益（回放 equity_curve 累乘本金）
2. fills_vs_plan.csv  —— 逐单成交 vs 计划权重（planned/buffer/unplanned）
3. cost_vs_model.csv  —— 实际费用 vs 模型费用（逐单 max(5, v*万0.845)）

与回测的两段式管线复用同一选债函数（compute_cb_target_weights）
与同一执行变体（WeightTargetStrategy 的 H1 参数），保证信号一致。

用法：
    py scripts/run_paper_tracking.py --once          # 单次调仓检查
    py scripts/run_paper_tracking.py                  # 默认单次
    py scripts/run_paper_tracking.py --show-status    # 只看当前持仓与进度
    py scripts/run_paper_tracking.py --record         # 回放本季度，产出三表
    py scripts/run_paper_tracking.py --record --quarter 2026Q3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Sequence

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
# 真实费率（与 run_real_fee_sensitivity 的 real_huatai 档保持一致）
REAL_FEE = {"commission": 0.0000845, "min_commission": 5.0}
STATE_DIR = _PROJECT_ROOT / "output" / "paper_tracking"

EQUITY_CSV = "equity_curve.csv"
FILLS_CSV = "fills_vs_plan.csv"
COST_CSV = "cost_vs_model.csv"


def _latest_pit_date(pit_table: pd.DataFrame) -> pd.Timestamp:
    return pd.Timestamp(pit_table["as_of"].max())


def _quarter_of(d: date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _quarter_bounds(quarter: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """'2026Q3' -> (2026-01-01 样式的季度首日, 季度末日)"""
    try:
        year_s, q_s = quarter.upper().split("Q")
        year, q = int(year_s), int(q_s)
        if not 1 <= q <= 4:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError(f"季度格式应为 YYYYQn，收到 {quarter!r}") from None
    start = pd.Timestamp(year=year, month=3 * (q - 1) + 1, day=1)
    return start, start + pd.offsets.QuarterEnd(0)


def _merge_quarter_rows(csv_path: Path, quarter: str, new_rows: pd.DataFrame) -> pd.DataFrame:
    """按季度幂等合并：覆盖同季度旧行后整体排序重写。"""
    if csv_path.exists():
        old = pd.read_csv(csv_path)
        old = old[old["quarter"] != quarter]
    else:
        old = pd.DataFrame()
    merged = pd.concat([old, new_rows], ignore_index=True)
    merged.to_csv(csv_path, index=False)
    return merged


def record_quarter(
    pit_table: pd.DataFrame,
    quarter: str,
    capital: float,
    cfg,
    out_dir: Path,
    pick_params: Optional[dict] = None,
    fee: Optional[dict] = None,
) -> dict:
    """回放一个季度的 paper 组合并产出三表，返回期末汇总指标。

    回放口径：PIT 截取 [季度首日, min(季度末日, PIT 末端)]，选债/执行
    与 run_real_fee_sensitivity 的 real_huatai 档完全同参。
    """
    pick_params = {**PICK_PARAMS, **(pick_params or {})}
    fee = {**REAL_FEE, **(fee or {})}
    out_dir.mkdir(parents=True, exist_ok=True)

    q_start, q_end = _quarter_bounds(quarter)
    pit_latest = _latest_pit_date(pit_table)
    if q_start > pit_latest:
        raise ValueError(f"{quarter} 尚无 PIT 数据（PIT 末端 {pit_latest.date()}）")
    end_clip = min(q_end, pit_latest)
    required_end = pd.bdate_range(q_end - pd.Timedelta(days=7), q_end)[-1]  # 季度最后一个工作日
    if end_clip < required_end:
        # 季末才产出三表：半季回放会以不完整数据覆盖三表
        raise ValueError(
            f"{quarter} PIT 未覆盖全季（到 {end_clip.date()}，"
            f"需到 {required_end.date()}）——季末再 record")

    start_s, end_s = q_start.strftime("%Y-%m-%d"), end_clip.strftime("%Y-%m-%d")
    weights = compute_cb_target_weights(
        pit_table, start=start_s, end=end_s, rebalance="Q", **pick_params)
    if not weights:
        raise ValueError(f"{quarter} 内无调仓计划（{start_s}..{end_s}）")
    symbols = sorted({s for picks in weights.values() for s in picks})

    pit_q = pit_table[(pit_table["as_of"] >= q_start) & (pit_table["as_of"] <= end_clip)]
    feeds = pit_to_feeds(pit_q, symbols)

    bt_cfg = cfg.backtest
    overrides = {
        "commission": fee["commission"],
        "min_commission": fee["min_commission"],
        "stamp_duty": bt_cfg.stamp_duty,
        "transfer_fee": bt_cfg.transfer_fee,
    }
    engine = BacktestEngine(cfg, commission_overrides=overrides, logger=logger)
    params = {
        "target_weights": weights,
        "convertible_symbols": tuple(symbols),
        "convertible_lot_size": 10,
        "lot_size": 10,
    }
    result = engine.run(WeightTargetStrategy, params, feeds)
    if not result.fills:
        raise ValueError(f"{quarter} 回放无成交——检查 PIT 覆盖与选债参数")

    # 表 1：equity_curve（TimeReturn 逐 bar 收益 → 累乘本金）
    curve = result.equity_curve
    if curve is not None and len(curve):
        cum = capital * (1.0 + curve.astype(float)).cumprod()
        eq_rows = pd.DataFrame({
            "quarter": quarter,
            "date": [pd.Timestamp(i).date() for i in curve.index],
            "value": cum.round(2).values,
        })
    else:
        eq_rows = pd.DataFrame(columns=["quarter", "date", "value"])
    _merge_quarter_rows(out_dir / EQUITY_CSV, quarter, eq_rows)

    # 表 2：fills_vs_plan（逐单成交 vs 该季调仓计划权重）
    rebalance_date = sorted(weights.keys())[0]
    plan = weights[rebalance_date]
    fill_rows = pd.DataFrame(result.fills)
    fill_rows.insert(0, "quarter", quarter)
    fill_rows["planned_weight"] = fill_rows["symbol"].map(plan).fillna(0.0)
    fill_rows["role"] = fill_rows.apply(
        lambda r: ("planned" if r["planned_weight"] > 0
                   else "buffer" if r["symbol"] in plan else "unplanned"), axis=1)
    fill_rows = fill_rows.sort_values(["date", "symbol"]).reset_index(drop=True)
    _merge_quarter_rows(out_dir / FILLS_CSV, quarter, fill_rows)

    # 表 3：cost_vs_model（实际费用 vs 逐单成本模型）
    # 引擎 executed.comm = 佣金（max(v*rate, 最低5)）+ 卖出印花税 + 过户费，
    # 模型逐单复算同一公式——diff 非零即成本配置漂移的报警。
    modeled = sum(
        max(fee["min_commission"], f["value"] * fee["commission"])
        + (f["value"] * bt_cfg.stamp_duty if f["side"] == "sell" else 0.0)
        + f["value"] * bt_cfg.transfer_fee
        for f in result.fills
    )
    eff_bp = result.commission_paid / result.traded_value * 1e4 if result.traded_value else 0.0
    cost_rows = pd.DataFrame([{
        "quarter": quarter,
        "rebalance_date": rebalance_date,
        "capital": capital,
        "traded_value": round(result.traded_value, 2),
        "completed_orders": result.completed_orders,
        "actual_commission": round(result.commission_paid, 2),
        "modeled_commission": round(modeled, 2),
        "commission_diff": round(result.commission_paid - modeled, 2),
        "effective_rate_bp": round(eff_bp, 2),
        "slippage_model": round(result.traded_value * bt_cfg.slippage, 2),
        "final_value": round(result.final_value, 2),
        "pit_end": str(end_clip.date()),
    }])
    _merge_quarter_rows(out_dir / COST_CSV, quarter, cost_rows)

    summary = {
        "quarter": quarter,
        "rebalance_date": rebalance_date,
        "targets": {s: w for s, w in plan.items() if w > 0},
        "n_fills": len(result.fills),
        "traded_value": result.traded_value,
        "actual_commission": round(result.commission_paid, 2),
        "modeled_commission": round(modeled, 2),
        "final_value": round(result.final_value, 2),
        "total_return": round(result.total_return, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "pit_end": str(end_clip.date()),
    }
    return summary


def export_current_plan(
    pit_table: pd.DataFrame,
    latest_picks: Dict[str, float],
    latest_key: str,
    capital: float,
    out_dir: Optional[Path] = None,
) -> Path:
    """导出 current_plan.json 信号契约（①-C）：dsa 侧操作单生成器的唯一数据源。

    契约 schema=cb_current_plan_v1：符号/权重/T-1收盘价/PIT末端/调仓日。
    纯本地 PIT 读取，不发网络。跨项目只走此文件，不互相 import。
    """
    out = (out_dir or (STATE_DIR)) / "current_plan.json"
    pit_latest = _latest_pit_date(pit_table)
    latest_ts = pd.Timestamp(pit_latest)
    targets: Dict[str, Dict[str, float]] = {}
    for symbol in sorted(latest_picks):
        rows = pit_table[pit_table["symbol"] == symbol]
        rows = rows[rows["as_of"] == latest_ts]
        t1_close = float(rows["price"].iloc[-1]) if len(rows) else 0.0
        targets[symbol] = {
            "weight": float(latest_picks[symbol]),
            "t1_close": t1_close,
        }
    plan = {
        "schema": "cb_current_plan_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pit_end": str(pit_latest.date()),
        "latest_rebalance": latest_key,
        "capital_base": float(capital),
        "targets": targets,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("信号契约已导出: %s（%d 个目标）", out, len(targets))
    return out


def _print_plan(pit_table: pd.DataFrame, capital: float,
                quiet: bool = False) -> int:
    """单次调仓检查（原 --once / 默认 / --show-status 行为）"""
    pit_latest = _latest_pit_date(pit_table)
    logger.info("PIT 末端: %s", pit_latest.date())

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

    # 诚实展示：调仓日缺席（候选不足 min_candidates）≠ 清仓决策，
    # 执行层语义为"沿用上季持仓"（H2 预注册口径，2020Q2 曾实际发生）。
    dates = pd.Series(pd.to_datetime(pit_table["as_of"].unique()))
    qkey = dates.dt.year * 10 + ((dates.dt.month - 1) // 3 + 1)
    all_q = sorted(set(dates.groupby(qkey).min().dt.strftime("%Y-%m-%d")))
    year_ago = (pit_latest - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    recent_absent = [d for d in all_q if d not in weights and d > year_ago]

    if not quiet:
        broker = PaperBroker(initial_cash=capital)
        broker.login()
        print()
        print("=== 季频转债双低 paper tracking ===")
        print(f"PIT 末端: {pit_latest.date()}  最新调仓日: {latest_key}")
        print(f"本金: {capital:,.0f}  费率: 万0.845 + 最低5元（不免五）")
        print()
        print("目标持仓（含 buffer 队列）:")
        for sym in sorted(latest_picks, key=lambda k: -latest_picks[k]):
            role = "持仓" if latest_picks[sym] > 0 else "buffer"
            print(f"  {sym}: weight={latest_picks[sym]:.2f} ({role})")
        if recent_absent:
            print()
            print(f"注意: 近一年 {len(recent_absent)} 个调仓日无选债计划"
                  f"（过滤链后候选不足 min_candidates={PICK_PARAMS['min_candidates']}）:")
            print(f"  {recent_absent}")
            print("  口径: 这些季度未记录调仓 → 执行层沿用上季持仓，并非清仓信号。")
        print()

    try:
        export_current_plan(pit_table, latest_picks, latest_key, capital)
    except Exception as exc:  # noqa: BLE001 - 导出失败不影响计划展示
        logger.warning("current_plan.json 导出失败: %r", exc)

    record_run(
        params={"kind": "paper_tracking", "pit_latest": str(pit_latest.date()),
                "latest_rebalance": latest_key,
                "targets": latest_picks,
                "capital": capital,
                "recent_absent_rebalances": recent_absent},
        metrics={"n_targets": len(positive), "n_buffer": len(queue_zero)},
        tags={"kind": "paper_tracking"},
    )
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="季频转债双低 paper tracking")
    p.add_argument("--pit", default=str(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"))
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--once", action="store_true", help="单次调仓检查（默认）")
    p.add_argument("--show-status", action="store_true", help="只显示当前持仓与计划进度")
    p.add_argument("--record", action="store_true",
                   help="回放季度并产出三表（equity_curve / fills_vs_plan / cost_vs_model）")
    p.add_argument("--quarter", default=None,
                   help="--record 的季度，格式 YYYYQn（默认：今天所在季度）")
    p.add_argument("--out-dir", default=str(STATE_DIR), help="三表输出目录")
    p.add_argument("--export-plan", action="store_true",
                   help="只导出 current_plan.json 信号契约（供 dsa 操作单生成器消费）")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", force=True)
    cfg = load_config()
    pit_table = load_pit_snapshot(args.pit)

    if args.export_plan:
        return _print_plan(pit_table, args.capital, quiet=True)

    if args.record:
        quarter = args.quarter or _quarter_of(date.today())
        try:
            summary = record_quarter(
                pit_table, quarter, args.capital, cfg, Path(args.out_dir))
        except ValueError as e:
            logger.error("%s", e)
            return 1
        print()
        print(f"=== {quarter} paper 季度回放汇总 ===")
        print(f"调仓日: {summary['rebalance_date']}  PIT 末端: {summary['pit_end']}")
        print(f"目标: {summary['targets']}")
        print(f"成交 {summary['n_fills']} 单, 成交额 {summary['traded_value']:,.0f}")
        print(f"佣金 实际 {summary['actual_commission']:.2f} vs 模型 {summary['modeled_commission']:.2f}")
        print(f"期末 {summary['final_value']:,.2f}（收益 {summary['total_return']:.2%}, "
              f"回撤 {summary['max_drawdown']:.2%}）")
        print(f"三表已写入: {args.out_dir}")
        record_run(
            params={"kind": "paper_tracking_record", "quarter": quarter,
                    "capital": args.capital, "pick_params": PICK_PARAMS,
                    "fee": REAL_FEE, "targets": summary["targets"]},
            metrics={"n_fills": summary["n_fills"],
                     "total_return": summary["total_return"],
                     "max_drawdown": summary["max_drawdown"],
                     "actual_commission": summary["actual_commission"],
                     "modeled_commission": summary["modeled_commission"],
                     "final_value": summary["final_value"]},
            tags={"kind": "paper_tracking_record", "quarter": quarter},
        )
        return 0

    return _print_plan(pit_table, args.capital)


if __name__ == "__main__":
    sys.exit(main())

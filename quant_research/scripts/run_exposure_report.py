# -*- coding: utf-8 -*-
"""A2：两段式新基线的有效暴露口径报告

ROADMAP §3.3 A2：回答"+15.28% 里，在市月份贡献了多少、2026 空仓月份
贡献了多少"，使新基线可作为 H1 的诚实比较基准。

方法：读取冻结的选债计划（cb_two_stage_weights.csv / 重新推导），
按调仓月标记 在市（有持仓）/ 让位现金（空仓）；再用 A1 1x 基线的
equity_curve 按月切分收益，与暴露标记对齐。
"""

from __future__ import annotations

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
from quant_research.backtest.two_stage import compute_cb_target_weights
from quant_research.config import load_config
from quant_research.core.experiments import record_run

logger = logging.getLogger("a2_exposure_report")

PICK_PARAMS = dict(
    top_n=5,
    min_rating="AA-",
    max_premium=20.0,
    exclude_new_days=30,
    max_conv_value=135.0,
    min_maturity_days=365,
    min_candidates=15,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", force=True)
    cfg = load_config()
    pit_table = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")

    # 1) 暴露标记：每月调仓日有持仓 = 在市
    weights = compute_cb_target_weights(
        pit_table, start="2020-01-01", end="2026-08-08", rebalance="M", **PICK_PARAMS
    )
    exposure = {}
    for date_str, picks in weights.items():
        ts = pd.Timestamp(date_str)
        exposure[(ts.year, ts.month)] = {
            "rebalance_date": date_str,
            "exposed": bool(picks),
            "n_picks": len(picks),
            "symbols": sorted(picks),
        }

    # 2) 月度收益：从 equity_curve（1x 基线）切分
    weights_csv = _PROJECT_ROOT / "output" / "cb_two_stage_weights.csv"
    equity = None
    # 用 A1 1x 的运行结果：直接重放一次（成本 1x）拿到 equity_curve
    from quant_research.backtest.engine import BacktestEngine
    from quant_research.backtest.two_stage import pit_to_feeds, WeightTargetStrategy
    from quant_research.runner import ETF_COMMISSION

    picked = sorted({sym for picks in weights.values() for sym in picks})
    feeds = pit_to_feeds(pit_table, picked)

    class _Curve:
        curve: list = []

    spy = type("Spy", (WeightTargetStrategy,), {
        "next": lambda self: (
            _Curve.curve.append(float(self.broker.getvalue())),
            WeightTargetStrategy.next(self),
        )[1],
    })
    _Curve.curve = []
    engine = BacktestEngine(cfg, commission_overrides=ETF_COMMISSION, logger=None)
    engine.run(spy, {
        "target_weights": weights,
        "convertible_symbols": tuple(feeds.keys()),
        "convertible_lot_size": 10,
        "lot_size": 10,
    }, feeds)

    eq = pd.Series(_Curve.curve, name="equity")
    dates = None
    # equity_curve 的日期序列与 feeds 的 datas[0] 对齐——从 weights_csv 恢复不可靠，
    # 直接从 feed 日历取
    cal = feeds[picked[0]][["date"]].copy()
    # Spy.next 每个 bar 记录一次；对齐：取 feeds 并集后的第一个 feed 日历
    # （BacktestEngine 按.datas[0]时钟）。稳妥做法：用行数对齐（末尾对齐）
    n = min(len(cal), len(eq))
    cal = cal.tail(n).reset_index(drop=True)
    eq_aligned = eq.tail(n).reset_index(drop=True)
    cal["equity"] = eq_aligned
    cal["date"] = pd.to_datetime(cal["date"])
    cal["ret"] = cal["equity"].pct_change().fillna(0.0)
    cal["ym"] = cal["date"].dt.to_period("M")

    monthly = cal.groupby("ym")["ret"].apply(lambda s: float((1 + s).prod() - 1))

    # 3) 合成报告
    rows = []
    for ym, mret in monthly.items():
        key = (ym.year, ym.month)
        info = exposure.get(key)
        rows.append({
            "month": str(ym),
            "exposed": bool(info and info["exposed"]),
            "n_picks": info["n_picks"] if info else 0,
            "monthly_return": round(mret, 6),
        })
    df = pd.DataFrame(rows)
    df["cum"] = (1 + df["monthly_return"]).cumprod() - 1

    exposed = df[df.exposed]
    flat = df[~df.exposed]
    summary = {
        "task": "A2 effective exposure report (ROADMAP §3.3 A2)",
        "months_total": int(len(df)),
        "months_exposed": int(len(exposed)),
        "months_flat": int(len(flat)),
        "return_total": round(float((1 + df["monthly_return"]).prod() - 1), 6),
        "return_from_exposed_months": round(float((1 + exposed["monthly_return"]).prod() - 1), 6)
        if len(exposed) else 0.0,
        "return_from_flat_months": round(float((1 + flat["monthly_return"]).prod() - 1), 6)
        if len(flat) else 0.0,
        "exposed_months_mean": round(float(exposed["monthly_return"].mean()), 6) if len(exposed) else 0.0,
        "exposed_months_win_rate": round(float((exposed["monthly_return"] > 0).mean()), 4) if len(exposed) else 0.0,
        "flat_months_mean": round(float(flat["monthly_return"].mean()), 6) if len(flat) else 0.0,
        "by_year": {},
    }
    for year, g in df.groupby(df["month"].str[:4]):
        ge = g[g.exposed]
        summary["by_year"][year] = {
            "exposed_months": int(len(ge)),
            "total_months": int(len(g)),
            "return": round(float((1 + g["monthly_return"]).prod() - 1), 6),
        }

    out_json = _PROJECT_ROOT / "output" / "a2_exposure_report.json"
    out_csv = out_json.with_name("a2_exposure_monthly.csv")
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(json.dumps(summary, ensure_ascii=False, indent=1))

    record_run(
        params={"kind": "a2_exposure_report", "start": "2020-01-01", "end": "2026-08-08", **PICK_PARAMS},
        metrics={k: v for k, v in summary.items() if isinstance(v, (int, float))},
        artifacts={"report": str(out_json), "monthly": str(out_csv)},
        tags={"kind": "a2_exposure_report"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

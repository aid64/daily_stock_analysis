# -*- coding: utf-8 -*-
"""E2：全历史分位择时回测（蓝图 CB-RESEARCH-PLAN 实验线）

预注册门槛（运行前写死）：
- 构造：转债价格中位数的全历史 expanding 分位数（不用绝对线——170/140 已失真 4 年）
- 三档：分位 >80% 减半仓 / 20-80% 标准仓 / <20% 满仓
- 对照：择时版 vs 不择时版（同池同费率同信号）
- 通过门：年化超额 ≥0 且最大回撤降幅 ≥20% 且回撤持续期缩短
- 关闭门：连续 8 季择时版超额 <0，或回撤改善 <20%
- 快速证伪：中位数自 135 回落 <110 而策略躲过 <一半跌幅 → 直接证伪
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest.convertible import load_pit_snapshot

logger = logging.getLogger("e2_timing")

OUT_JSON = _PROJECT_ROOT / "output" / "e2_valuation_timing.json"
WINDOW = ("2020-01-01", "2026-08-08")
FEES = {"commission": 0.0000845, "min_commission": 5.0,
        "stamp_duty": 0.0005, "transfer_fee": 0.00002}


def build_median_series(pit: pd.DataFrame) -> pd.Series:
    """转债价格中位数日频序列（全市场）"""
    pit = pit.copy()
    pit["as_of"] = pd.to_datetime(pit["as_of"])
    pit["price"] = pd.to_numeric(pit["price"], errors="coerce")
    valid = pit[pit["price"] > 0].dropna(subset=["price"])
    return valid.groupby("as_of")["price"].median()


def build_equity_index(price_median: pd.Series) -> pd.Series:
    """转债等权指数代理：日频价格中位数累乘（简化为每日收益 = 中位数变化率）"""
    daily_ret = price_median.pct_change().fillna(0)
    return (1 + daily_ret).cumprod()


def expanding_percentile(median: pd.Series) -> pd.Series:
    """全历史 expanding 分位数（0-1）：当前值在截至当日的历史中的分位"""
    ranked = median.expanding(min_periods=20).rank(pct=True)
    return ranked


def quarterly_timing_backtest(
    equity_index: pd.Series,
    percentile: pd.Series,
    timing_enabled: bool = True,
    initial_capital: float = 10000.0,
) -> Dict[str, Any]:
    """季度调仓 + 分位择时。

    每季度首日：查分位 → 确定仓位系数 → 持有等权组合 × 仓位系数
    分位 >80% → 0.5x；20-80% → 1.0x；<20% → 1.0x（满仓，可选 1.2x 加仓但保守默认 1.0）
    """
    # 季度首日的分位和等权收益
    quarters = []
    seen = set()
    for ts in equity_index.index:
        key = (ts.year, (ts.month - 1) // 3 + 1)
        if key not in seen:
            seen.add(key)
            quarters.append(ts)

    equity = initial_capital
    equity_curve = []
    position_multipliers = []
    total_cost = 0.0

    for qi in range(len(quarters)):
        ts = quarters[qi]
        # 找最近的分位读数
        pct_series = percentile[percentile.index <= ts]
        pct = pct_series.iloc[-1] if not pct_series.empty else 0.5

        if timing_enabled:
            if pct > 0.80:
                multiplier = 0.5
            elif pct < 0.20:
                multiplier = 1.0
            else:
                multiplier = 1.0
        else:
            multiplier = 1.0
        position_multipliers.append({"date": str(ts)[:10], "percentile": round(pct, 3),
                                     "multiplier": multiplier})

        # 本季度收益 = 等权指数的季度收益 × 仓位系数
        next_ts = quarters[qi + 1] if qi + 1 < len(quarters) else equity_index.index[-1]
        start_val = equity_index.asof(ts) if ts <= equity_index.index[-1] else np.nan
        end_val = equity_index.asof(next_ts) if next_ts <= equity_index.index[-1] else np.nan

        if pd.isna(start_val) or pd.isna(end_val) or start_val <= 0:
            equity_curve.append({"date": str(ts)[:10], "equity": round(equity, 2)})
            continue

        quarterly_return = (end_val / start_val) - 1
        # 费用：季调仓佣金（买卖各一次，简化）
        cost = equity * 0.0001 + max(equity * 0.0000845, 5.0)
        total_cost += cost

        # 择时：高估减仓 = 只有 multiplier 比例的仓位参与收益
        equity = equity * (1 + quarterly_return * multiplier) - cost
        equity = max(equity, 0)  # 不为负
        equity_curve.append({"date": str(ts)[:10], "equity": round(equity, 2)})

    final = equity_curve[-1]["equity"] if equity_curve else initial_capital
    n_years = max(1, len(quarters) / 4)
    total_return = (final - initial_capital) / initial_capital
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if total_return > -1 else -1.0

    values = [e["equity"] for e in equity_curve]
    peak = values[0] if values else initial_capital
    max_dd = 0.0
    max_dd_start = None
    max_dd_end = None
    for i, v in enumerate(values):
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_end = i

    # 回撤持续期（简算：从最高点到最低点的季度数）
    dd_duration = 0
    if max_dd > 0 and max_dd_end:
        peak_idx = values.index(max(values[:max_dd_end + 1]))
        dd_duration = max_dd_end - peak_idx

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(max_dd, 6),
        "max_dd_duration_quarters": dd_duration,
        "total_cost": round(total_cost, 2),
        "equity_curve": equity_curve,
        "position_multipliers": position_multipliers,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        force=True)

    pit = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")
    pit = pit[pit["as_of"] <= pd.Timestamp(WINDOW[1])]
    pit = pit[pit["as_of"] >= pd.Timestamp(WINDOW[0])]

    median = build_median_series(pit)
    percentile = expanding_percentile(median)
    equity_index = build_equity_index(median)

    # 择时版 vs 不择时版
    timed = quarterly_timing_backtest(equity_index, percentile, timing_enabled=True)
    untimed = quarterly_timing_backtest(equity_index, percentile, timing_enabled=False)

    # 预注册门槛
    excess = timed["annual_return"] - untimed["annual_return"]
    dd_improvement = (untimed["max_drawdown"] - timed["max_drawdown"]) / untimed["max_drawdown"] if untimed["max_drawdown"] > 0 else 0
    duration_shorter = timed["max_dd_duration_quarters"] < untimed["max_dd_duration_quarters"]

    gate_annual = excess >= 0
    gate_dd = dd_improvement >= 0.20
    gate_duration = duration_shorter
    verdict = "pass" if (gate_annual and gate_dd and gate_duration) else "fail"

    # 当前分位读数
    current_pct = percentile.iloc[-1] if not percentile.empty else None
    current_median = median.iloc[-1] if not median.empty else None

    report = {
        "task": "E2 valuation percentile timing (preregistered)",
        "window": f"{WINDOW[0]}..{WINDOW[1]}",
        "results": {
            "timed": {k: v for k, v in timed.items()
                      if k not in ("equity_curve", "position_multipliers")},
            "untimed": {k: v for k, v in untimed.items()
                        if k not in ("equity_curve", "position_multipliers")},
        },
        "gates": {
            "annual_excess": round(excess, 4),
            "gate_annual_ge_0": bool(gate_annual),
            "dd_improvement_pct": round(dd_improvement * 100, 1),
            "gate_dd_ge_20pct": bool(gate_dd),
            "duration_shorter": bool(duration_shorter),
            "verdict": str(verdict),
        },
        "current_state": {
            "as_of": str(percentile.index[-1])[:10] if not percentile.empty else None,
            "price_median": round(current_median, 2) if current_median else None,
            "percentile": round(current_pct, 3) if current_pct is not None else None,
            "note": "窗口末端读数（WINDOW 截断），非今日实时分位；运行时动态重算见 dsa /健康",
        },
        "position_history": timed.get("position_multipliers", []),
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print(json.dumps(report["gates"], ensure_ascii=False, indent=1))
    print(f"当前: 中位数 {report['current_state']['price_median']}, "
          f"分位 {report['current_state']['percentile']}")
    for k in ("timed", "untimed"):
        r = report["results"][k]
        print(f"  {k}: return {r['total_return']:.2%}, dd {r['max_drawdown']:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

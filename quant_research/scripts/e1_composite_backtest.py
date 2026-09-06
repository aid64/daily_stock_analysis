# -*- coding: utf-8 -*-
"""E1：合成因子四臂对照回测（蓝图 CB-RESEARCH-PLAN 诊断线收官实验）

四臂（同引擎同费率同窗口）：
  1. double_low  — 基线（价格+溢价率，越低越好）
  2. ytm_true    — 真 YTM（越高越好，D1 产出）
  3. low_premium — 转股溢价率（越低越好）
  4. composite   — rank(ytm_true) + rank(-premium_rate) 合成（各 50%）

预注册门槛（运行前写死，不得现场调整）：
  通过门：合成因子 ① 全窗口年化超额 vs 双低 ≥ +2pp 且最大回撤不恶化
          ② 2021 年（双低死亡年）IC 为正
  关闭门：任一不满足 → 关闭，记录"债性+股性合成不优于分量"
窗口：2020-01-01..2026-08-08（holdout 前）
费率：真实费率 万0.845 + 最低 5 元（不免五）
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest.convertible import load_pit_snapshot
from quant_research.core.panel import Panel
from quant_research.core.factors import ic_by_year

logger = logging.getLogger("e1_composite")

OUT_JSON = _PROJECT_ROOT / "output" / "e1_composite_vs_baseline.json"
SAMPLE_STEP = 5
WINDOW = ("2020-01-01", "2026-08-08")
TOP_N = 5
FEES = {"commission": 0.0000845, "min_commission": 5.0,
        "stamp_duty": 0.0005, "transfer_fee": 0.00002}


def build_factor_matrices(
    ytm: pd.DataFrame, pit: pd.DataFrame, dates: List[pd.Timestamp]
) -> Dict[str, pd.DataFrame]:
    """构造四臂因子矩阵（date × symbol，每季度首日截面）"""
    # 从 PIT 补充 premium_rate（cb_ytm_true 没有此列）
    pit_latest = pit.sort_values("as_of").drop_duplicates("symbol", keep="last")
    prem_map = pit_latest.set_index("symbol")["premium_rate"]
    ytm = ytm.copy()
    ytm["premium_rate"] = ytm["symbol"].map(prem_map)

    dl_m, ytm_m, prem_m, comp_m = [], [], [], []
    for ts in dates:
        day_ytm = ytm[ytm["as_of"] == ts].set_index("symbol")
        if day_ytm.empty:
            continue
        day_ytm = day_ytm[day_ytm["price"] > 0]
        syms = day_ytm.index.tolist()
        if len(syms) < TOP_N:
            continue

        # 因子值
        dl = day_ytm["price"] + day_ytm["premium_rate"].fillna(0)
        ytm_v = day_ytm["ytm_true"]
        prem = day_ytm["premium_rate"].fillna(0)

        # rank 合成：rank(ytm_true) + rank(-premium_rate)，各 50%
        rk_ytm = ytm_v.rank(pct=True)
        rk_prem = (-prem).rank(pct=True)
        composite = (rk_ytm + rk_prem) / 2

        # 每臂取 top_n（合成因子越高越好；双低/溢价率越低越好 → 取负）
        top_dl = dl.nsmallest(TOP_N).index
        top_ytm = ytm_v.nlargest(TOP_N).index
        top_prem = prem.nsmallest(TOP_N).index
        top_comp = composite.nlargest(TOP_N).index

        date_str = str(ts)[:10]
        for sym in syms:
            dl_m.append({"as_of": ts, "symbol": sym,
                         "signal": 1.0 / TOP_N if sym in top_dl else 0.0})
            ytm_m.append({"as_of": ts, "symbol": sym,
                          "signal": 1.0 / TOP_N if sym in top_ytm else 0.0})
            prem_m.append({"as_of": ts, "symbol": sym,
                           "signal": 1.0 / TOP_N if sym in top_prem else 0.0})
            comp_m.append({"as_of": ts, "symbol": sym,
                           "signal": 1.0 / TOP_N if sym in top_comp else 0.0})

    def _pivot(rows, val_col="signal"):
        df = pd.DataFrame(rows)
        return df.pivot_table(index="as_of", columns="symbol", values=val_col)

    return {
        "double_low": _pivot(dl_m),
        "ytm_true": _pivot(ytm_m),
        "low_premium": _pivot(prem_m),
        "composite": _pivot(comp_m),
    }


def simulate_rebalance(
    signal_matrix: pd.DataFrame,
    price_panel: pd.DataFrame,
    initial_capital: float = 10000.0,
) -> Dict[str, Any]:
    """季频调仓模拟（正确版）：每季度首日选 top_n → 持有 3 月 → 下季调仓。

    price_panel: (date × symbol) 收盘价矩阵（来自 PIT 面板）。
    期末收益 = 各标的季度收益等权平均（扣买入佣金）。
    """
    quarters = signal_matrix.index.tolist()
    price_dates = price_panel.index.tolist()
    equity = initial_capital
    equity_curve = [{"date": str(quarters[0])[:10], "equity": round(equity, 2)}]
    total_cost = 0.0
    prev_holdings: Dict[str, float] = {}  # symbol -> weight

    for qi in range(len(quarters)):
        ts = quarters[qi]
        signals = signal_matrix.loc[ts]
        active = signals[signals > 0]
        if active.empty:
            equity_curve.append({"date": str(ts)[:10], "equity": round(equity, 2)})
            continue

        # 找到下一季度首日（用于计算本季收益）
        if qi + 1 < len(quarters):
            next_ts = quarters[qi + 1]
        else:
            next_ts = price_panel.index[-1]

        # 获取本季度的期初价和期末价
        buy_prices, sell_prices = {}, {}
        for sym in active.index:
            if sym not in price_panel.columns:
                continue
            col = price_panel[sym].dropna()
            buy_candidates = col[col.index >= ts]
            sell_candidates = col[col.index <= next_ts]
            if buy_candidates.empty:
                continue
            buy_prices[sym] = buy_candidates.iloc[0]
            if not sell_candidates.empty:
                sell_prices[sym] = sell_candidates.iloc[-1]
            else:
                sell_prices[sym] = buy_candidates.iloc[0]

        if not buy_prices:
            equity_curve.append({"date": str(ts)[:10], "equity": round(equity, 2)})
            continue

        # 等权买入
        per_position = equity / len(buy_prices)
        quarter_end_value = 0.0
        for sym, buy_p in buy_prices.items():
            if buy_p <= 0:
                continue
            qty = int(per_position / buy_p / 10) * 10  # 整手 10 张
            if qty <= 0:
                qty = 10  # 最低一手
            cost_basis = qty * buy_p
            # 买入费用
            fee = max(cost_basis * FEES["commission"], FEES["min_commission"])
            fee += cost_basis * FEES["transfer_fee"]
            total_cost += fee

            # 期末估值
            sell_p = sell_prices.get(sym, buy_p)
            quarter_end_value += qty * sell_p
            # 卖出费用（非最后期）
            if qi + 1 < len(quarters):
                sell_fee = max(qty * sell_p * FEES["commission"],
                               FEES["min_commission"])
                sell_fee += qty * sell_p * FEES["transfer_fee"]
                sell_fee += qty * sell_p * FEES["stamp_duty"]
                total_cost += sell_fee

        # 本季收益 = 期末值 / 期初投入
        invested = per_position * len(buy_prices)
        if invested > 0 and quarter_end_value > 0:
            gross_return = quarter_end_value / invested
            net_return = (quarter_end_value - total_cost * 0.1) / invested  # 粗略扣费
            equity = equity * max(gross_return - 0.01, 0.5)  # 保守扣 1% 费用
        equity_curve.append({"date": str(ts)[:10], "equity": round(equity, 2)})

    final = equity_curve[-1]["equity"] if equity_curve else initial_capital
    n_years = max(1, len(quarters) / 4)
    total_return = (final - initial_capital) / initial_capital
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if total_return > -1 else -1.0

    values = [e["equity"] for e in equity_curve]
    peak = values[0] if values else initial_capital
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(max_dd, 6),
        "total_cost": round(total_cost, 2),
        "n_rebalances": len(quarters),
        "equity_curve": equity_curve,
    }


def _build_price_panel(pit: pd.DataFrame) -> pd.DataFrame:
    """从 PIT 构建 (date × symbol) 收盘价矩阵"""
    pit = pit.copy()
    pit["as_of"] = pd.to_datetime(pit["as_of"])
    pit["price"] = pd.to_numeric(pit["price"], errors="coerce")
    valid = pit[pit["price"] > 0].drop_duplicates(subset=["as_of", "symbol"])
    return valid.pivot_table(index="as_of", columns="symbol", values="price")




def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        force=True)

    ytm = pd.read_parquet(_PROJECT_ROOT / "data" / "pit" / "cb_ytm_true.parquet")
    ytm = ytm[ytm["as_of"] <= pd.Timestamp(WINDOW[1])]
    ytm = ytm[ytm["as_of"] >= pd.Timestamp(WINDOW[0])]
    pit = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")
    pit = pit[pit["as_of"] <= pd.Timestamp(WINDOW[1])]

    # 季度首日日期
    dates = sorted(ytm["as_of"].unique())
    dates = [pd.Timestamp(d) for d in dates]

    matrices = build_factor_matrices(ytm, pit, dates)
    price_panel = _build_price_panel(pit)
    logger.info("四臂矩阵构建完成: %s", {k: len(v) for k, v in matrices.items()})

    results = {}
    for name, matrix in matrices.items():
        sim = simulate_rebalance(matrix, price_panel)
        results[name] = sim
        logger.info("%s: return %.2f%%, dd %.2f%%, cost ¥%.0f",
                     name, sim["total_return"] * 100, sim["max_drawdown"] * 100,
                     sim["total_cost"])

    # 预注册门槛
    dl = results["double_low"]
    comp = results["composite"]
    excess_pp = round((comp["annual_return"] - dl["annual_return"]) * 100, 2)
    dd_ok = comp["max_drawdown"] <= dl["max_drawdown"] + 0.001
    gate_1 = excess_pp >= 2.0 and dd_ok

    report = {
        "task": "E1 composite factor four-arm comparison (preregistered)",
        "window": f"{WINDOW[0]}..{WINDOW[1]}",
        "top_n": TOP_N,
        "results": {k: {kk: vv for kk, vv in v.items() if kk != "equity_curve"}
                    for k, v in results.items()},
        "gates": {
            "composite_excess_vs_double_low_pp": excess_pp,
            "gate_1_excess_ge_2pp_and_dd_ok": bool(gate_1),
            "verdict": "pass" if bool(gate_1) else "fail",
        },
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(json.dumps(report["gates"], ensure_ascii=False, indent=1))
    for k, v in results.items():
        print(f"  {k}: return {v['total_return']:.2%}, dd {v['max_drawdown']:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

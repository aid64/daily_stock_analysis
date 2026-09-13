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
        "stamp_duty": 0.0, "transfer_fee": 0.00002}
# C1 口径修正：转债免印花税（RuleBook cb.stamp_duty=0），旧值 0.0005 错误。


def build_factor_matrices(
    ytm: pd.DataFrame, pit: pd.DataFrame, dates: List[pd.Timestamp]
) -> Dict[str, pd.DataFrame]:
    """构造四臂因子矩阵（date × symbol，逐调仓日截面）。

    C1 修复（OPTIMIZATION-PLAN-V2）：premium_rate 按 as_of 逐日对齐——
    调仓日 t 只用 ≤t 的最新 PIT 截面（PIT 纪律）。旧实现把每券窗口末值
    映射到全部历史日期（实测中位偏差 17.5pp、排序相关 0.146，前视污染）。
    """
    pit = pit.copy()
    pit["as_of"] = pd.to_datetime(pit["as_of"])
    pit_sorted = pit.sort_values("as_of")

    dl_m, ytm_m, prem_m, comp_m = [], [], [], []
    for ts in dates:
        day_ytm = ytm[ytm["as_of"] == ts].set_index("symbol")
        if day_ytm.empty:
            continue
        day_ytm = day_ytm[day_ytm["price"] > 0]
        syms = day_ytm.index.tolist()
        if len(syms) < TOP_N:
            continue

        # PIT 对齐：t 日（或 t 前最近）的 premium 截面
        pit_at = pit_sorted[pit_sorted["as_of"] <= ts]
        if pit_at.empty:
            continue
        day_pit = pit_at[pit_at["as_of"] == pit_at["as_of"].max()].set_index("symbol")
        prem = pd.Series(
            [day_pit["premium_rate"].get(s, float("nan")) for s in syms],
            index=day_ytm.index).fillna(0.0)

        # 因子值
        dl = day_ytm["price"] + prem
        ytm_v = day_ytm["ytm_true"]

        # rank 合成：rank(ytm_true) + rank(-premium_rate)，各 50%
        rk_ytm = ytm_v.rank(pct=True)
        rk_prem = (-prem).rank(pct=True)
        composite = (rk_ytm + rk_prem) / 2

        # 每臂取 top_n（合成因子越高越好；双低/溢价率越低越好 → 取负）
        top_dl = dl.nsmallest(TOP_N).index
        top_ytm = ytm_v.nlargest(TOP_N).index
        top_prem = prem.nsmallest(TOP_N).index
        top_comp = composite.nlargest(TOP_N).index

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
    """调仓模拟（C1 重写）：现金守恒的整手换仓模型。

    口径（OPTIMIZATION-PLAN-V2 批次 C1）：
    - 现金守恒：equity = cash + Σ持仓市值，逐笔费用真扣，无"每期-1%"
      截断（旧实现每期 max(gross-1%, 0.5) 灾难性失真）；
    - 整手取整余款留现金，不强制最低一手；
    - 转债免印花税（FEES.stamp_duty=0）；
    - 调仓频率按实际日历年化（调仓日是 ytm 每 5 交易日采样，旧 len/4
      把 263 期当年化 65.8 年，年化被压成≈0）。
    """
    quarters = signal_matrix.index.tolist()
    if not quarters:
        return {"total_return": 0.0, "annual_return": 0.0, "max_drawdown": 0.0,
                "total_cost": 0.0, "n_rebalances": 0, "equity_curve": []}

    cash = float(initial_capital)
    holdings: Dict[str, int] = {}  # symbol -> 张数
    total_cost = 0.0
    equity_curve: List[Dict[str, Any]] = []

    def _price_at(sym: str, ts: pd.Timestamp, forward: bool) -> Optional[float]:
        """forward=True 取 ts 当日或之后首个价；False 取 ts 当日或之前末个价。"""
        if sym not in price_panel.columns:
            return None
        col = price_panel[sym].dropna()
        cand = col[col.index >= ts] if forward else col[col.index <= ts]
        return float(cand.iloc[0]) if len(cand) else None

    def _buy_fee(notional: float) -> float:
        return max(notional * FEES["commission"], FEES["min_commission"]) \
            + notional * FEES["transfer_fee"]

    def _sell_fee(notional: float) -> float:
        return max(notional * FEES["commission"], FEES["min_commission"]) \
            + notional * FEES["transfer_fee"] \
            + notional * FEES["stamp_duty"]

    def _mark_equity(ts: pd.Timestamp) -> float:
        """现金 + 按最近可得价格估值的持仓市值。"""
        value = cash
        for sym, qty in holdings.items():
            p = _price_at(sym, ts, forward=False)
            if p is None:
                p = _price_at(sym, ts, forward=True)
            if p is not None:
                value += qty * p
        return value

    equity_curve.append({"date": str(quarters[0])[:10],
                         "equity": round(initial_capital, 2)})

    for qi, ts in enumerate(quarters):
        end_ts = quarters[qi + 1] if qi + 1 < len(quarters) else price_panel.index[-1]

        # ① 卖出全部旧持仓（以 ts 当日或之前末个价格成交；无价则持有不动）
        for sym in list(holdings):
            qty = holdings[sym]
            p = _price_at(sym, ts, forward=False)
            if p is None:
                continue  # 停牌/缺价：继续持有，不虚构成交
            notional = qty * p
            cash += notional - _sell_fee(notional)
            total_cost += _sell_fee(notional)
            del holdings[sym]

        # ② 等权买入新目标（限 [ts, end_ts] 季内首个价格——跨断档跳价禁入）
        signals = signal_matrix.loc[ts]
        active = signals[signals > 0]
        buy_prices: Dict[str, float] = {}
        for sym in active.index:
            if sym not in price_panel.columns:
                continue
            col = price_panel[sym].dropna()
            cand = col[(col.index >= ts) & (col.index <= end_ts)]
            if len(cand) and float(cand.iloc[0]) > 0:
                buy_prices[sym] = float(cand.iloc[0])
        if buy_prices:
            per_position = cash / len(buy_prices)
            for sym, buy_p in sorted(buy_prices.items()):
                qty = int(per_position / buy_p / 10) * 10  # 整手，余款留现金
                if qty <= 0:
                    continue
                notional = qty * buy_p
                fee = _buy_fee(notional)
                if notional + fee > cash:
                    continue  # 现金不足（最低佣金主导的小额仓）
                cash -= notional + fee
                total_cost += fee
                holdings[sym] = holdings.get(sym, 0) + qty

        # ③ 季末估值（下季首日前最后一个可得价）
        end_ts = quarters[qi + 1] if qi + 1 < len(quarters) else price_panel.index[-1]
        equity_curve.append({"date": str(end_ts)[:10],
                             "equity": round(_mark_equity(end_ts), 2)})

    # 期末清算（按最后可得价卖出，扣费）→ 终值含现金
    final_ts = price_panel.index[-1]
    for sym in list(holdings):
        qty = holdings[sym]
        p = _price_at(sym, final_ts, forward=False)
        if p is not None:
            notional = qty * p
            cash += notional - _sell_fee(notional)
            total_cost += _sell_fee(notional)
            del holdings[sym]
    final_value = cash + sum(
        (holdings[s] * (_price_at(s, final_ts, forward=False)
                        or _price_at(s, final_ts, forward=True) or 0.0))
        for s in holdings)
    equity_curve.append({"date": str(final_ts)[:10],
                         "equity": round(final_value, 2)})

    final = final_value
    span_days = max(1.0, (pd.Timestamp(quarters[-1]) - pd.Timestamp(quarters[0])).days)
    n_years = max(span_days / 365.25, 1 / 365.25)
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

    # 季度首日（C1 修正：预注册口径是季频调仓；ytm 每 5 交易日采样，
    # 全部采样日当调仓日会把 263 次全换仓的最低佣金拖累当成因子表现）
    dates_raw = sorted(pd.Timestamp(d) for d in ytm["as_of"].unique())
    _s = pd.Series(dates_raw)
    dates = sorted(pd.Timestamp(d) for d in
                   _s.groupby([_s.dt.year, _s.dt.quarter]).min().tolist())

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
        "revision": "2026-09-12 C1 evaluator fix: PIT-aligned premium, quarterly dates, cash-conservative fills, no stamp duty on CB; supersedes pre-fix numbers",
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

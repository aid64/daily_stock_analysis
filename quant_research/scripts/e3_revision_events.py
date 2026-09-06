# -*- coding: utf-8 -*-
"""E3：下修博弈事件窗回测（蓝图 CB-RESEARCH-PLAN 实验线）

预注册门槛（运行前写死）：
- 事件定义：T = cb_share 转股价跳变日的 publish_date（公告日），下修 = convert_price 下降
  且降幅 ≥2%（排除分红除权——除权降幅小且与正股分红日对齐）
- 入场：T+1 交易日买入（按转债日频收盘价，简化无滑点）
- 出场：持有 20 交易日 或 价格 ≥130 先到先卖
- 排除：已进入强赎程序（cb_call is_call 含"公告实施强赎"/"公告到期赎回"）
- 样本要求：≥30 例才评估；T+1~T+20 平均超额（相对转债等权）>2% 才考虑上线；≤0 关闭
- 首期定位：纯观察统计（与季频调仓框架冲突，不进操作单）

数据源（D0 tushare 落盘产物）：
- cb_share_history.parquet：转股价日频 → 跳变检测
- cb_calls.parquet：强赎事件 → 排除
- convertible_pit.parquet：转债日频价格（事件窗收益计算）
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest.convertible import load_pit_snapshot

logger = logging.getLogger("e3_revision")

OUT_JSON = _PROJECT_ROOT / "output" / "e3_revision_event_window.json"
MIN_REVISION_PCT = 2.0   # 转股价降幅 ≥2% 才算下修（排除除权噪声）
HOLD_DAYS = 20
TAKE_PROFIT = 130.0
MIN_EVENTS = 30
MIN_EXCESS_PCT = 2.0


def detect_revisions(
    share_history: pd.DataFrame,
    min_drop_pct: float = MIN_REVISION_PCT,
) -> pd.DataFrame:
    """从转股价日频历史检测下修事件（跳变 = 修正后价格下降 ≥min_drop_pct）。

    Returns:
        DataFrame: publish_date, ts_code, price_before, price_after, drop_pct
    """
    df = share_history.sort_values(["ts_code", "end_date"]).copy()
    df["convert_price"] = pd.to_numeric(df["convert_price"], errors="coerce")
    df = df.dropna(subset=["convert_price"])
    df = df[df["convert_price"] > 0]

    # 找跳变：同券相邻行价格下降
    df["prev_price"] = df.groupby("ts_code")["convert_price"].shift(1)
    df["prev_end_date"] = df.groupby("ts_code")["end_date"].shift(1)
    df["drop_pct"] = (df["prev_price"] - df["convert_price"]) / df["prev_price"] * 100

    # 过滤：降幅 ≥ threshold（排除分红除权：除权降幅通常 <1%）
    revisions = df[
        (df["prev_price"].notna()) &
        (df["drop_pct"] >= min_drop_pct)
    ].copy()

    # publish_date 优先（公告日），缺失用 end_date
    revisions["event_date"] = pd.to_datetime(
        revisions["publish_date"].fillna(revisions["end_date"]),
        format="%Y%m%d", errors="coerce")
    revisions = revisions.dropna(subset=["event_date"])
    revisions = revisions.drop_duplicates(subset=["ts_code", "event_date"])

    return revisions[["ts_code", "event_date", "prev_price",
                      "convert_price", "drop_pct"]].reset_index(drop=True)


def build_redemption_exclusion(
    calls: pd.DataFrame,
) -> set:
    """已进入强赎/到赎程序的券 → 排除集合（这些债没有博弈价值）"""
    if calls.empty:
        return set()
    excluded = calls[
        calls["is_call"].isin(["公告实施强赎", "公告到期赎回"])
    ]["ts_code"].unique()
    return set(excluded)


def run_event_window(
    revisions: pd.DataFrame,
    price_panel: pd.DataFrame,
    equity_index: pd.Series,
    excluded: set,
    hold_days: int = HOLD_DAYS,
    take_profit: float = TAKE_PROFIT,
) -> Dict[str, Any]:
    """逐事件回测：T+1 买入 → 持有 20 日或 ≥130 先到先卖 → 算绝对收益和超额收益"""
    results = []
    for _, rev in revisions.iterrows():
        ts_code = rev["ts_code"]
        event_date = rev["event_date"]
        if ts_code in excluded:
            continue
        if ts_code not in price_panel.columns:
            continue

        col = price_panel[ts_code].dropna()
        # T+1 = 事件日之后第一个交易日
        buy_candidates = col[col.index > event_date]
        if buy_candidates.empty:
            continue
        buy_date = buy_candidates.index[0]
        buy_price = buy_candidates.iloc[0]
        if buy_price <= 0:
            continue

        # 持有窗口
        window = col[(col.index > buy_date)].head(hold_days)
        if window.empty:
            continue

        exit_price = None
        exit_date = None
        exit_reason = "hold_end"
        for dt, price in window.items():
            if price >= take_profit:
                exit_price = price
                exit_date = dt
                exit_reason = "take_profit"
                break
            exit_price = price
            exit_date = dt
        if exit_price is None:
            exit_price = buy_price
            exit_date = buy_date

        ret = (exit_price - buy_price) / buy_price * 100

        # 超额 = 事件收益 - 同期等权指数收益
        eq_start = equity_index.asof(buy_date)
        eq_end = equity_index.asof(exit_date) if exit_date in equity_index.index else equity_index.asof(exit_date)
        eq_ret = ((eq_end - eq_start) / eq_start * 100
                  if eq_start and eq_end and eq_start > 0 else 0.0)
        excess = ret - eq_ret

        results.append({
            "ts_code": ts_code,
            "event_date": str(event_date)[:10],
            "buy_date": str(buy_date)[:10],
            "buy_price": round(buy_price, 3),
            "exit_price": round(exit_price, 3) if exit_price else None,
            "exit_date": str(exit_date)[:10] if exit_date else None,
            "exit_reason": exit_reason,
            "return_pct": round(ret, 2),
            "excess_pct": round(excess, 2),
            "drop_pct": round(rev["drop_pct"], 1),
        })

    return {"events": results, "n_total": len(results)}


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        force=True)

    share = pd.read_parquet(_PROJECT_ROOT / "data" / "pit" / "cb_share_history.parquet")
    calls = pd.read_parquet(_PROJECT_ROOT / "data" / "pit" / "cb_calls.parquet")
    pit = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")
    pit = pit[pit["as_of"] <= pd.Timestamp("2026-08-08")]
    pit["as_of"] = pd.to_datetime(pit["as_of"])
    pit["price"] = pd.to_numeric(pit["price"], errors="coerce")
    valid = pit[pit["price"] > 0].dropna(subset=["price"])

    # 价格矩阵
    price_panel = valid.pivot_table(index="as_of", columns="symbol", values="price")

    # 等权指数（简化：全市场中位数日收益累乘）
    median = valid.groupby("as_of")["price"].median()
    daily_ret = median.pct_change().fillna(0)
    equity_index = (1 + daily_ret).cumprod()

    # 下修事件检测
    revisions = detect_revisions(share)
    logger.info("下修事件检测: %d 例（降幅 ≥%.0f%%）", len(revisions), MIN_REVISION_PCT)

    # 强赎排除
    excluded = build_redemption_exclusion(calls)
    logger.info("强赎排除: %d 只", len(excluded))

    # 事件窗回测
    result = run_event_window(revisions, price_panel, equity_index, excluded)
    events = result["events"]
    n = result["n_total"]
    logger.info("可回测事件: %d 例", n)

    if n < MIN_EVENTS:
        report = {
            "task": "E3 revision event window (preregistered)",
            "verdict": "insufficient_sample",
            "n_events": n,
            "min_required": MIN_EVENTS,
            "note": f"事件数 {n} < {MIN_EVENTS}，不做统计推断",
        }
        OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    # 统计
    returns = [e["return_pct"] for e in events]
    excesses = [e["excess_pct"] for e in events]
    win_rate = sum(1 for r in returns if r > 0) / n * 100
    avg_ret = np.mean(returns)
    avg_excess = np.mean(excesses)
    n_tp = sum(1 for e in events if e["exit_reason"] == "take_profit")
    tp_rate = n_tp / n * 100

    # 预注册判定
    gate_excess = avg_excess > MIN_EXCESS_PCT
    gate_sample = n >= MIN_EVENTS
    verdict = "pass" if (gate_excess and gate_sample) else "fail"

    report = {
        "task": "E3 revision event window (preregistered)",
        "verdict": str(verdict),
        "gates": {
            "avg_excess_pct": round(avg_excess, 2),
            "gate_excess_gt_2pct": bool(gate_excess),
            "n_events": n,
            "gate_sample_ge_30": bool(gate_sample),
        },
        "stats": {
            "win_rate_pct": round(win_rate, 1),
            "avg_return_pct": round(avg_ret, 2),
            "avg_excess_pct": round(avg_excess, 2),
            "take_profit_rate_pct": round(tp_rate, 1),
            "median_return_pct": round(float(np.median(returns)), 2),
        },
        "events_sample": events[:20],
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print(json.dumps(report["gates"] | report["stats"], ensure_ascii=False, indent=1))
    print(f"判定: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

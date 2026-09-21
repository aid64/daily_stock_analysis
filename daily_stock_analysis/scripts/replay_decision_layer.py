# -*- coding: utf-8 -*-
"""在线决策层回放与敏感性（v4.1 计划 P0.9）

问题（v4.1 批评#1/#5）：每天真正对用户输出指令的三层——结构锚价位、
破位分档、组合器参数——是未经回测的启发式，却发了免检。000899 从"立即全卖"
翻成"持有观察"就是此盲区实证。本脚本给这三层建立与"策略"同级的回放/对照纪律。

它做两件事（只读本地日线，不联网、不下单、无副作用）：
  1. 决策回放：对固定标的的历史日线，逐个"评估日"回放当日破位诊断判决，
     产出"哪天系统会说什么"的判决序列——重生价位/改阈值后重跑，diff 即
     "哪些判决因此改变"。
  2. 参数扰动敏感性：把破位阈值（深度/浅破位/持续/量能/首日降档）与价位锚
     参数（窗口/波幅倍数）逐个扰动到邻近值，统计判决分布如何变——证明
     不是脆弱拟合，或暴露脆弱点。

用法（在 daily_stock_analysis/ 下）：
  py scripts/replay_decision_layer.py                    # 当前持仓标的
  py scripts/replay_decision_layer.py --symbols 000899,002475
  py scripts/replay_decision_layer.py --days 30          # 回放最近 30 个交易日

结果建议贴入 docs/decision-layer-changelog.md 作为变更前基线。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))


def _load_full_bars(symbol: str, days: int = 400) -> List[Tuple[str, float, float]]:
    from datetime import date, timedelta

    from src.repositories.stock_repo import StockRepository

    end = date.today()
    start = end - timedelta(days=days)
    rows = StockRepository().get_range(symbol, start, end)
    bars: List[Tuple[str, float, float]] = []
    for row in rows or []:
        try:
            close = float(row.close)
            if close <= 0:
                continue
            bars.append((str(row.date), close,
                         float(getattr(row, "volume", 0) or 0)))
        except (TypeError, ValueError):
            continue
    return bars


def _structural_levels(closes: List[float], window: int = 20,
                       buffer_atr: float = 1.5) -> Optional[List[float]]:
    """结构锚止损档（与 regen_price_rules.compute_levels 同口径的止损两档）。"""
    if len(closes) < window + 1:
        return None
    win = closes[-window:]
    low = min(win)
    moves = [abs(closes[i] - closes[i - 1])
             for i in range(len(closes) - window, len(closes))]
    avg_move = sum(moves) / len(moves) if moves else 0.0
    stop1 = round(low, 2)
    stop2 = round(low - buffer_atr * avg_move, 2)
    return [stop1, stop2]


def replay_symbol(symbol: str, days: int, thresholds=None,
                  window: int = 20, buffer_atr: float = 1.5) -> List[Dict]:
    """逐评估日回放：取每个评估日之前的窗口算价位与诊断当日判决。"""
    from src.services.account_decision_service import diagnose_breach

    bars = _load_full_bars(symbol)
    if len(bars) < 25:
        return []
    out: List[Dict] = []
    n = len(bars)
    start_idx = max(21, n - days)
    for i in range(start_idx, n):
        hist = bars[:i + 1]                 # 含当日
        price = hist[-1][1]
        closes_before = [c for _, c, _ in hist[:-1]]   # 价位用截至前一日
        levels = _structural_levels(closes_before, window, buffer_atr)
        if not levels:
            continue
        deepest = min(levels)
        verdict = "hold(未破位)"
        if price < deepest:
            diag = diagnose_breach(symbol=symbol, price=price,
                                   stop_levels=levels, bars=hist,
                                   thresholds=thresholds)
            verdict = diag.urgency if diag else "hold(诊断空)"
        out.append({"date": hist[-1][0], "price": price,
                    "stop": deepest, "verdict": verdict})
    return out


def _verdict_dist(rows: List[Dict]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for r in rows:
        v = r["verdict"].split("(")[0]
        dist[v] = dist.get(v, 0) + 1
    return dist


def sensitivity(symbol: str, days: int) -> None:
    """参数逐个扰动到邻近值，打印判决分布对照。"""
    from src.services.account_decision_service import BreachThresholds

    base = BreachThresholds()
    base_rows = replay_symbol(symbol, days, thresholds=base)
    print(f"\n===== {symbol} 参数扰动敏感性（{len(base_rows)} 个破位评估日）=====")
    print(f"基线 {base.__dict__}")
    print(f"基线判决分布: {_verdict_dist(base_rows)}")

    perturbations = [
        ("deep_pct", [3.0, 4.0, 6.0, 7.0]),
        ("shallow_pct", [1.0, 1.5, 2.5, 3.0]),
        ("persist_days", [3, 4, 7, 10]),
        ("volume_spike", [1.2, 1.5, 2.0]),
        ("fresh_days", [0, 2, 3]),
    ]
    print("\n--- 破位阈值扰动 → 判决分布 ---")
    for field, vals in perturbations:
        for v in vals:
            th = replace(base, **{field: v})
            rows = replay_symbol(symbol, days, thresholds=th)
            print(f"  {field}={v:<5} → {_verdict_dist(rows)}")

    print("\n--- 价位锚扰动（窗口/波幅倍数）→ 破位日数与判决 ---")
    for window in (10, 20, 30):
        for buf in (1.0, 1.5, 2.0):
            rows = replay_symbol(symbol, days, thresholds=base,
                                 window=window, buffer_atr=buf)
            breached = [r for r in rows if not r["verdict"].startswith("hold")]
            print(f"  window={window} buffer={buf} → 破位 {len(breached)}/"
                  f"{len(rows)} 日, {_verdict_dist(rows)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="在线决策层回放与敏感性（P0.9）")
    ap.add_argument("--symbols", default="", help="逗号分隔；缺省取当前持仓")
    ap.add_argument("--days", type=int, default=60, help="回放最近 N 个评估日")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        from src.services.portfolio_service import PortfolioService
        snap = PortfolioService().get_portfolio_snapshot(
            cost_method="fifo", include_realtime=False)
        for acc in snap.get("accounts") or []:
            for p in acc.get("positions") or []:
                if float(p.get("quantity") or 0) > 0:
                    d = "".join(ch for ch in str(p.get("symbol")) if ch.isdigit())
                    if len(d) == 6:
                        symbols.append(d)
    if not symbols:
        print("无标的可回放（无持仓且未指定 --symbols）")
        return

    for sym in symbols:
        rows = replay_symbol(sym, args.days)
        breached = [r for r in rows if not r["verdict"].startswith("hold")]
        print(f"\n===== {sym} 判决回放（最近 {len(rows)} 评估日，"
              f"破位 {len(breached)} 日）=====")
        for r in breached[-15:]:
            print(f"  {r['date']} 价 {r['price']:g} < 止损 {r['stop']:g}"
                  f" → {r['verdict']}")
        sensitivity(sym, args.days)


if __name__ == "__main__":
    main()

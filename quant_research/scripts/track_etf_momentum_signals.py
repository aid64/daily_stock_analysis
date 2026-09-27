# -*- coding: utf-8 -*-
"""ETF 动量影子信号跟踪（冻结豁免 v3，2026-09-27：paper-only，无研究评估、无实盘）

口径零漂移承诺：
- 动量得分**直接调用** `backtest.strategies.momentum_scores`——与引擎/回测同一函数；
- 参数读 `config/strategy/etf_momentum.yaml`（不复制数值）；
- 选债/防御逻辑镜像 `ETFMomentum.next()` 的 targets 块（strategies.py:257-268）。

执行约定（与引擎一致，strategies.py:162 注释）：调仓日=每周第一个交易日，
当日收盘算信号 → **次日开盘成交**。手动跟单参考：调仓日 14:50 前按现价下单
（T+1 品种当日不可卖；17 池中 511010/518880/511880 为 T+0）。

已知口径局限（每条记录附注）：v4 快照 7 只 ETF 为新浪不复权降级源——
512890（红利低波，月月分红）的动量被系统性低估；v1 证据同口径，一致性成立。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from quant_research.backtest.strategies import momentum_scores  # noqa: E402


class _Series:
    """快照 DataFrame → BarSeries 适配（momentum_scores 所需最小接口）。"""

    def __init__(self, name: str, df: pd.DataFrame):
        self.name = name
        self.df = df.sort_values("date").reset_index(drop=True)

    def __len__(self) -> int:
        return len(self.df)

    def closes(self, n: int) -> np.ndarray:
        return self.df["close"].tail(n).to_numpy(dtype=float)

    def amounts(self, n: int) -> np.ndarray:
        return self.df["amount"].tail(n).to_numpy(dtype=float)


class _View:
    def __init__(self, feeds: dict):
        self._series = [_Series(k, v) for k, v in feeds.items()]

    def all_series(self):
        return self._series


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _next_iso_week_first_day(after: date) -> str:
    """下一个"ISO 周第一个交易日"（周末跳过；节假日以实际交易日历为准，
    此处仅做日历近似并在记录中注明）。"""
    base_iso = after.isocalendar()[:2]
    d = after + timedelta(days=1)
    for _ in range(30):
        if d.weekday() < 5 and d.isocalendar()[:2] != base_iso:
            return d.isoformat()
        if d.weekday() < 5 and d.isocalendar()[:2] == base_iso:
            d += timedelta(days=1)
            continue
        d += timedelta(days=1)
    return d.isoformat()


def build_signal(snapshot_path: Path, strategy_config: Path) -> dict:
    with strategy_config.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    params = cfg.get("params") or {}
    defensive = params.get("defensive") or cfg.get("defensive")
    windows = (int(params.get("lookback") or 60),)
    ma_filter = int(params.get("ma_filter") or 250)
    min_volume = float(params.get("min_volume") or 1_000_000)
    top_k = int(params.get("top_k") or 3)
    rebalance_freq = str(params.get("rebalance_freq") or cfg.get("rebalance_freq") or "W")
    # 快照键统一带交易所后缀（511010.SH），defensive 配置补齐后缀
    if defensive and "." not in str(defensive):
        defensive = f"{defensive}.SH"

    feeds = pd.read_pickle(snapshot_path)
    as_of = max(df["date"].max() for df in feeds.values())
    as_of = pd.Timestamp(as_of).date()

    view = _View(feeds)
    scores = momentum_scores(
        view, windows, ma_filter, min_volume, False, defensive)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    selected = [sym for sym, _ in ranked[:top_k]]
    defensive_mode = not selected

    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "strategy": "etf_momentum",
        "params": {
            "windows": list(windows), "ma_filter": ma_filter,
            "min_volume": min_volume, "top_k": top_k,
            "rebalance_freq": rebalance_freq, "defensive": defensive,
            "universe_size": len(feeds),
        },
        "scores": [{"symbol": s, "score": round(v, 6)} for s, v in ranked],
        "filtered_out": sorted(
            set(feeds.keys()) - {defensive}
            - {s for s, _ in ranked} - {defensive}
            if defensive else set(feeds.keys()) - {s for s, _ in ranked}),
        "selected": selected,
        "defensive_mode": defensive_mode,
        "next_rebalance_day": _next_iso_week_first_day(as_of),
        "execution_convention": "调仓日收盘算信号 → 次日开盘成交（引擎约定）",
        "snapshot": {"path": str(snapshot_path),
                     "sha256": _sha256_file(snapshot_path)},
        "caveats": [
            "影子跟踪·非投资建议·不落单（冻结期豁免 v3，paper-only）",
            "v4 快照 7 只 ETF 为新浪不复权降级源：512890（红利低波，月月分红）"
            "动量被系统性低估；与 v1 回测证据同口径，一致性成立",
            "下一调仓日为日历近似（周末跳过），节假日以实际交易日历为准",
        ],
    }


def main(argv: Optional[list] = None) -> int:
    # 路径全部由 __file__ 推导、CWD 无关（计划任务上下文 CWD=System32，
    # backup_ledger.py 同款纪律——2026-09-27 任务上下文实测教训）
    p = argparse.ArgumentParser(description="ETF 动量影子信号跟踪")
    p.add_argument("--snapshot",
                   default=str(_REPO / "output" / "feeds_snapshot_v4.pkl"))
    p.add_argument("--strategy-config",
                   default=str(_REPO / "config" / "strategy" / "etf_momentum.yaml"))
    p.add_argument("--log",
                   default=str(_REPO / "output" / "etf_momentum_tracking"
                               / "signals.jsonl"))
    args = p.parse_args(argv)

    snap_path = Path(args.snapshot)
    if not snap_path.exists():
        print(f"快照不存在: {snap_path}", file=sys.stderr)
        return 2

    signal = build_signal(snap_path, Path(args.strategy_config))

    out = Path(args.log)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(signal, ensure_ascii=False) + "\n")

    mode = "防御态（无候选 → 全仓 511010）" if signal["defensive_mode"] else ""
    print(f"signal appended: {out}")
    print(f"  as_of={signal['as_of']} 下一调仓日={signal['next_rebalance_day']}")
    if signal["defensive_mode"]:
        print(f"  {mode}")
    else:
        for i, sym in enumerate(signal["selected"], 1):
            sc = next(s["score"] for s in signal["scores"] if s["symbol"] == sym)
            print(f"  {i}. {sym} (score={sc:+.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""从 backtrader 引擎提取 etf_mwu / etf_mwu_cb 的最新调仓目标（不手搓、引擎口径）。

原理：ETFMWUProbe 继承引擎策略类，仅覆写 _target_portfolio 记录每次调仓日
的合成目标；用冻结快照(v*.pkl)跑到最新 bar，读最后一条目标 + 专家权重。
快照口径与 E 槽位 run_paper_tracking（PIT 截至 09-24）一致，诚实可复现。

用法：
  py scripts/extract_mwu_targets.py --strategy etf_mwu \
      --strategy-config config/strategy/etf_mwu.yaml
输出（stdout JSON）：{"strategy": ..., "as_of": ..., "weights": {...},
                      "target": {...}, "picks": {...}}
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import backtrader as bt  # noqa: E402

from quant_research.backtest.engine import PandasBarsData  # noqa: E402
from quant_research.backtest.mwu import ETFMWU, build_experts  # noqa: E402
from quant_research.backtest.strategies import ETFMomentum  # noqa: E402
from quant_research.backtest.convertible.snapshot import load_pit_snapshot  # noqa: E402
from quant_research.config.loader import load_strategy_config, load_config  # noqa: E402
from quant_research.data.loader import DataLoader  # noqa: E402

PIT_PATH = ROOT / "data" / "pit" / "convertible_pit.parquet"


class ETFMWUProbe(ETFMWU):
    """只加记录不改行为的探针：记录每次调仓日目标；不执行真实交易（故无需转债 feed）。"""

    def __init__(self):
        super().__init__()
        self.last_target: dict[str, float] = {}
        self.last_date: str = ""

    def _target_portfolio(self) -> dict[str, float]:
        target = super()._target_portfolio()
        self.last_target = {k: round(float(v), 6) for k, v in target.items()}
        self.last_date = str(self.datas[0].datetime.date(0))
        return target

    def _rebalance_to(self, target) -> None:
        return  # 探针只提取目标，跳过下单（含无 feed 的转债标的）


class MomentumProbe(ETFMomentum):
    """A 动量探针：记录每次调仓日的引擎排序（ranked）——选股口径 100% 引擎。"""

    def __init__(self):
        super().__init__()
        self.last_ranked: list = []
        self.last_date: str = ""

    def _compute_momentum(self):
        ranked = super()._compute_momentum()
        self.last_ranked = sorted(ranked.items(), key=lambda kv: kv[1], reverse=True)
        self.last_date = str(self.datas[0].datetime.date(0))
        return ranked

    def _buy_defensive(self) -> None:
        return  # 探针不下单(规避引擎 _buy_defensive 对上市前 NaN feed 的未防御路径)

    def _rebalance_to(self, target) -> None:
        return  # 探针不下单

    def _target_portfolio(self) -> dict[str, float]:
        target = super()._target_portfolio()
        self.last_target = {k: round(float(v), 6) for k, v in target.items()}
        self.last_date = str(self.datas[0].datetime.date(0))
        return target


def load_snapshot(snapshot: str | None) -> dict[str, pd.DataFrame]:  # noqa: ARG001（保留兼容签名）
    """改走项目数据链（tushare P-1 优先，带 amount 列，数据新鲜到最近交易日）。"""
    return {}  # 占位：main 已改为直接 loader 取数


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="etf_mwu")
    ap.add_argument("--strategy-config", default="config/strategy/etf_mwu.yaml")
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--start", default="2024-01-01", help="回测起点(预热另加)")
    args = ap.parse_args()

    strat_cfg = load_strategy_config(args.strategy_config, name=args.strategy)
    cfg = load_config(args.config)

    universe = [s.strip().upper() for s in (strat_cfg.universe or [])]
    params = dict(strat_cfg.params or {})
    defensive = params.get("defensive")
    if defensive and defensive not in universe:
        universe.append(defensive)

    # 预热窗口照抄 run_backtest：max(顶层 ma_filter/lookback, 各专家窗口)×1.7 历日
    warmup = max(int(params.get("ma_filter", 250)), int(params.get("lookback", 60)))
    for exp_kw in (strat_cfg.experts or {}).values():
        for key in ("ma_filter", "lookback"):
            if isinstance(exp_kw, dict) and key in exp_kw:
                warmup = max(warmup, int(exp_kw[key]))
    data_start = (pd.Timestamp(args.start) - pd.Timedelta(days=int(warmup * 1.7))).strftime("%Y-%m-%d")
    end = pd.Timestamp.today().strftime("%Y-%m-%d")

    cerebro = bt.Cerebro(stdstats=False)
    missing = []
    prepared: dict[str, pd.DataFrame] = {}
    with DataLoader(cfg) as loader:
        for sym in universe:
            try:
                df = loader.get_etf_daily(sym, data_start, end)
            except Exception as exc:
                print(f"# {sym} 取数失败: {exc!r:.80}", file=sys.stderr)
                missing.append(sym)
                continue
            if df is None or len(df) == 0:
                missing.append(sym)
                continue
            # 预处理照抄 engine.run：date→DatetimeIndex、排序去重、openinterest
            d = df.copy()
            if "date" in d.columns:
                d["date"] = pd.to_datetime(d["date"])
                d = d.set_index("date")
            elif not isinstance(d.index, pd.DatetimeIndex):
                d.index = pd.to_datetime(d.index)
            d = d.sort_index()
            d = d[~d.index.duplicated(keep="last")]
            if "openinterest" not in d.columns:
                d["openinterest"] = 0.0
            prepared[sym] = d
    if missing:
        print(f"# 缺标的(跳过): {missing}", file=sys.stderr)

    # 多标的日期对齐（生命周期内 ffill，上市前 NaN 由策略层排除）——同 engine.run
    if len(prepared) > 1:
        union_index = sorted(set().union(*(d.index for d in prepared.values())))
        aligned: dict[str, pd.DataFrame] = {}
        for sym, d in prepared.items():
            out = d.reindex(union_index)
            in_life = (out.index >= d.index[0]) & (out.index <= d.index[-1])
            out.loc[in_life] = out.loc[in_life].ffill()
            aligned[sym] = out
        prepared = aligned
    for sym, d in prepared.items():
        cerebro.adddata(PandasBarsData(dataname=d, name=sym), name=sym)
    if missing:
        print(f"# 缺标的(跳过): {missing}", file=sys.stderr)

    expert_spec = {k: dict(v) for k, v in (strat_cfg.experts or {}).items()}
    if args.strategy == "etf_mwu_cb":
        if not PIT_PATH.exists():
            raise SystemExit(f"PIT 快照不存在: {PIT_PATH}")
        pit_table = load_pit_snapshot(PIT_PATH)
        cb = expert_spec.pop("convertible_double_low", None)
        if cb is None:
            raise SystemExit("etf_mwu_cb 需要 convertible_double_low 专家")
        pit_spec = {k: v for k, v in cb.items() if k != "candidate_pool"}
        pit_spec["pit_table"] = pit_table
        expert_spec["convertible_double_low_pit"] = pit_spec
        print(f"# PIT 转债快照: {pit_table.shape[0]} 行", file=sys.stderr)

    if args.strategy == "etf_momentum":
        m_kwargs = {
            "mom_windows": tuple(params.get("windows") or (params.get("lookback", 60),)),
            "lookback": int(params.get("lookback", 60)),
            "ma_filter": int(params.get("ma_filter", 250)),
            "min_volume": float(params.get("min_volume", 0)),
            "vol_adjusted": bool(params.get("vol_adjusted", False)),
            "top_k": int(params.get("top_k", 3)),
            "rebalance_freq": strat_cfg.rebalance_freq,
            "defensive": defensive,
        }
        cerebro.addstrategy(MomentumProbe, **m_kwargs)
        results = cerebro.run()
        probe = results[0]
        top_k = int(params.get("top_k", 3))
        ranked = [{"symbol": s, "score": round(float(v), 6)} for s, v in probe.last_ranked]
        defensive_mode = not ranked and bool(defensive)
        print(json.dumps({
            "strategy": args.strategy, "config": args.strategy_config,
            "universe": universe, "data_window": [data_start, end],
            "as_of": probe.last_date, "ranked": ranked[:max(top_k, 5)],
            "top_k": top_k, "defensive_mode": defensive_mode,
        }, ensure_ascii=False, indent=2))
        return 0

    kwargs = {
        "experts": build_experts(expert_spec),
        "eta": float(params.get("eta", 0.1)),
        "loss_scale": float(params.get("loss_scale", 0.05)),
        "rebalance_freq": strat_cfg.rebalance_freq,
        "lot_size": int(params.get("lot_size", 100)),
        "defensive": defensive,
        "defensive_lot_size": int(params.get("defensive_lot_size", 10)),
        "stop_loss_pct": params.get("stop_loss_pct"),
    }
    cerebro.addstrategy(ETFMWUProbe, **kwargs)
    results = cerebro.run()
    probe = results[0]

    print(json.dumps({
        "strategy": args.strategy,
        "config": args.strategy_config,
        "universe": universe,
        "data_window": [data_start, end],
        "as_of": probe.last_date,
        "expert_weights": {k: round(float(v), 4) for k, v in probe._weights.items()},
        "target": probe.last_target,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""R1 重构等价性对拍脚本（合成数据，离线确定性）。

在重构前/后各运行一次，输出 JSON：
- ETFMWU 端到端回测：equity 曲线、final_value、期末专家权重、订单统计
- LiveMWU 权重轨迹：滑动窗口逐期 update 后的专家权重

diff 应为空（R1a 行为保持）；R1b（loss 映射修正）预期有差异，单独声明。

用法：py .scratch/r1_equiv_check.py <output.json>
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "quant_research"))

from quant_research.backtest.engine import BacktestEngine  # noqa: E402
from quant_research.backtest.mwu import (  # noqa: E402
    DefensiveExpert,
    ETFMWU,
    MeanReversionExpert,
    MomentumExpert,
)
from quant_research.config.schema import (  # noqa: E402
    AppConfig,
    BacktestConfig,
    DataConfig,
    DataSource,
    TushareConfig,
)
from tests.test_backtest import _make_trend_bars  # noqa: E402


class _Spy(ETFMWU):
    curve: list[float] = []
    orders: list = []

    def next(self):
        super().next()
        _Spy.curve.append(float(self.broker.getvalue()))

    def stop(self):
        _Spy.final_weights = dict(self._weights)
        _Spy.n_trades = int(sum(1 for o in self.broker.get_orders_open())) if False else None


def build_feeds() -> dict[str, pd.DataFrame]:
    """确定性合成行情：强趋势 / 缓涨 / 防御 / 诱多后暴跌"""
    up = _make_trend_bars(25, 0.008)
    down = _make_trend_bars(45, -0.012, start_price=up["close"].iloc[-1])
    risk = pd.concat([up, down.iloc[1:]], ignore_index=True)
    risk["date"] = pd.bdate_range("2024-01-01", periods=len(risk))
    return {
        "STRONG.SH": _make_trend_bars(120, 0.004),
        "MILD.SH": _make_trend_bars(120, 0.001),
        "RISK.SH": risk,
        "DEF.SH": _make_trend_bars(120, 0.0005, start_price=100.0),
    }


def run_backtest_part() -> dict:
    cfg = AppConfig(
        data=DataConfig(primary=DataSource.BAOSTOCK),
        tushare=TushareConfig(token=""),
        backtest=BacktestConfig(initial_capital=10000.0),
    )
    engine = BacktestEngine(cfg, logger=logging.getLogger("equiv"))
    experts = [
        MomentumExpert(lookback=5, top_k=1, ma_filter=10, min_volume=0.0),
        MeanReversionExpert(rsi_period=2, rsi_buy=30, ma_filter=20, top_k=1, min_volume=0.0),
        DefensiveExpert("DEF.SH"),
    ]
    params = {
        "experts": experts,
        "eta": 0.3,
        "loss_scale": 0.05,
        "share_alpha": 0.02,
        "rebalance_freq": "W",
        "defensive": "DEF.SH",
        "defensive_lot_size": 10,
    }
    _Spy.curve = []
    result = engine.run(_Spy, params, build_feeds())
    return {
        "equity": [round(v, 6) for v in _Spy.curve],
        "final_value": round(float(result.final_value), 6),
        "total_trades": int(result.total_trades),
        "completed_orders": int(result.completed_orders),
        "final_weights": {k: round(v, 10) for k, v in _Spy.final_weights.items()},
    }


def run_live_part() -> list[dict]:
    """LiveMWU 权重轨迹：窗口逐 bar 前移，每次 update 记录权重与目标"""
    from quant_research.trade.pipeline import LiveMWU

    experts = [
        MomentumExpert(lookback=5, top_k=1, ma_filter=10, min_volume=0.0),
        MeanReversionExpert(rsi_period=2, rsi_buy=30, ma_filter=20, top_k=1, min_volume=0.0),
        DefensiveExpert("DEF.SH"),
    ]
    mwu = LiveMWU(experts, eta=0.3, loss_scale=0.05, share_alpha=0.02)
    feeds = build_feeds()
    closes_map = {sym: df["close"].to_numpy(dtype=float) for sym, df in feeds.items()}
    n = min(len(v) for v in closes_map.values())

    trace = []
    for end in range(30, n + 1):
        prices = {}
        for sym, closes in closes_map.items():
            window = closes[:end]
            amount = np.full(min(20, len(window)), 1e8)
            prices[sym] = (window, amount)
        target = mwu.update(prices)
        trace.append({
            "step": end,
            "weights": {k: round(v, 10) for k, v in mwu.weights.items()},
            "target": {k: round(v, 10) for k, v in target.items()},
        })
    return trace


def main() -> int:
    out = {
        "backtest": run_backtest_part(),
        "live_trace": run_live_part(),
    }
    path = Path(sys.argv[1])
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written: {path}")
    print(f"final_value={out['backtest']['final_value']} "
          f"trades={out['backtest']['total_trades']} "
          f"live_steps={len(out['live_trace'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

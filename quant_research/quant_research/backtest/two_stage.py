"""两段式转债双低回测：矩阵研究计算 → 事件执行模拟（R5）

重构 R5（2026-08-29）：痛点 3 的治本改造。旧路径为让 849 只转债装进
backtrader feed，需 precompute_cb_union.py 预计算候选并集（并附加
"扫参须用最宽参数"的超集原则负担）。两段式拆解：

- 第一段（研究计算）：Panel 矩阵上向量化逐调仓日调用
  pit_snapshot_picks（与 PIT 专家同一过滤链，单一来源）——全量池
  过滤/排序就是矩阵运算，候选并集成为选债结果的天然副产品，
  precompute 步骤废除；
- 第二段（执行模拟）：只为被选中的券构建 feed（数量 = 并集大小，
  远小于全量池），WeightTargetStrategy 消费目标权重序列复用
  TargetWeightExecutionMixin 的先卖后买/整数手/容差执行，跑在
  BacktestEngine 上，享有 RuleBook 强制闸与佣金模型。

诚实近似：PIT 只有收盘价，pit_to_feeds 以 open=high=low=close 合成
（次_bar 开盘成交 ≈ 信号日收盘价），与旧管线（真实 OHLC 开盘成交）
存在轻微成交价差异，结果不可与旧基线直接逐位对比，作为新基线记录。
"""

from __future__ import annotations

from typing import Mapping, Optional

import backtrader as bt
import numpy as np
import pandas as pd

from ..core.panel import Panel
from ..runner import ETF_COMMISSION
from .convertible import pit_snapshot_picks
from .mwu import TargetWeightExecutionMixin


def compute_cb_target_weights(
    pit_table: pd.DataFrame,
    *,
    start: str,
    end: str,
    rebalance: str = "M",
    top_n: int = 5,
    min_rating: str = "AA-",
    max_premium: float = 20.0,
    exclude_new_days: int = 30,
    max_price_median: bool = True,
    max_conv_value: Optional[float] = None,
    min_maturity_days: Optional[int] = None,
    max_price: Optional[float] = None,
    min_candidates: Optional[int] = None,
) -> dict[str, dict[str, float]]:
    """第一段：PIT 矩阵逐调仓日选债 → {日期: {symbol: 目标权重}}

    rebalance="M" 为每月首个 PIT 交易日；"W" 为 ISO 周首日。
    选债走 pit_snapshot_picks 单一过滤链（与 PIT 专家语义一致）。
    """
    panel = Panel(pit_table)
    price = panel.get_panel("price", start=start, end=end)
    if price.empty:
        return {}

    dates = pd.Series(price.index)
    if rebalance.upper() == "M":
        keys = dates.groupby([dates.dt.year, dates.dt.month]).min()
    else:  # W
        iso = dates.dt.isocalendar()
        keys = dates.groupby([iso.year, iso.week]).min()
    schedule = sorted(pd.Timestamp(d) for d in keys)

    target_weights: dict[str, dict[str, float]] = {}
    for ts in schedule:
        snapshot = panel.snapshot(ts)
        if snapshot.empty:
            continue
        picks = pit_snapshot_picks(
            snapshot,
            top_n=top_n,
            min_rating=min_rating,
            max_premium=max_premium,
            exclude_new_days=exclude_new_days,
            max_price_median=max_price_median,
            as_of=ts,
            max_conv_value=max_conv_value,
            min_maturity_days=min_maturity_days,
            max_price=max_price,
            min_candidates=min_candidates,
        )
        if picks:
            target_weights[ts.date().isoformat()] = picks
    return target_weights


def pit_to_feeds(pit_table: pd.DataFrame, symbols: Sequence[str]) -> dict[str, pd.DataFrame]:
    """从 PIT 长表提取被选中券的日线 feed（7 列合成）

    open/high/low/close 全部 = PIT 收盘价（诚实近似，见模块 docstring）；
    volume/amount 为常数占位（WeightTargetStrategy 不做流动性过滤）。

    退市近似：已退市券的价格前向填充到 PIT 全区间末端——否则退市后
    价格线为 NaN，持仓被估值防御计为 0，净值塌方。前向填充使"到期/
    强赎按最后成交价退出"成为可执行的近似（下个调仓日整仓卖出），
    停留期 ≤1 个调仓周期。
    """
    table = pit_table.copy()
    table["as_of"] = pd.to_datetime(table["as_of"], errors="coerce").dt.normalize()
    table["price"] = pd.to_numeric(table["price"], errors="coerce")
    table = table.dropna(subset=["as_of", "price"])
    wanted = {s.upper() for s in symbols}
    table = table[table["symbol"].astype(str).isin(wanted)]
    global_end = table["as_of"].max()

    feeds: dict[str, pd.DataFrame] = {}
    for symbol, group in table.groupby("symbol"):
        group = group.sort_values("as_of").rename(columns={"as_of": "date"})
        dates = pd.DatetimeIndex(pd.to_datetime(group["date"]))
        close = group["price"].to_numpy(dtype=float)
        # 退市后按最后成交价前向填充（工作日）至全区间末端
        pad_idx = pd.bdate_range(dates.max() + pd.Timedelta(days=1), global_end)
        if len(pad_idx) > 0:
            dates = dates.append(pd.DatetimeIndex(pad_idx))
            close = np.concatenate([close, np.full(len(pad_idx), close[-1])])
        feeds[str(symbol)] = pd.DataFrame({
            "date": dates.to_numpy(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(len(close), 1_000_000.0),
            "amount": close * 1_000_000.0,
        }).reset_index(drop=True)
    return feeds


class WeightTargetStrategy(TargetWeightExecutionMixin, bt.Strategy):
    """第二段：消费预计算的目标权重计划，按日执行调仓

    与 ETFMWU 共用执行 mixin（先卖后买/整数手/5% 容差/止损带），
    享有 broker 强制闸（涨跌停/T+1）与佣金模型。
    """

    params = (
        ("target_weights", None),     # {"YYYY-MM-DD": {symbol: weight}}
        ("lot_size", 100),
        ("defensive", None),
        ("defensive_lot_size", 10),
        ("convertible_symbols", ()),
        ("convertible_lot_size", 10),
        ("stop_loss_pct", None),
    )

    def __init__(self):
        schedule = self.p.target_weights or {}
        self._schedule: dict[str, dict[str, float]] = {
            str(k): dict(v) for k, v in schedule.items()
        }
        self._executed: set[str] = set()

    def next(self):
        self._check_stop_loss()
        key = self.datas[0].datetime.date(0).isoformat()
        if key not in self._schedule or key in self._executed:
            return
        self._executed.add(key)
        self._rebalance_to(self._schedule[key])


def run_two_stage(
    pit_table: pd.DataFrame,
    cfg,
    *,
    start: str,
    end: str,
    rebalance: str = "M",
    logger=None,
    pick_params: Optional[Mapping] = None,
):
    """两段式转债双低回测入口

    返回 (BacktestResult, target_weights, feeds)；weights 为第一段产物，
    feeds 为第二段实际构建的 feed（并集大小的副产品）。
    """
    from .engine import BacktestEngine

    pick_params = dict(pick_params or {})
    target_weights = compute_cb_target_weights(
        pit_table, start=start, end=end, rebalance=rebalance, **pick_params
    )
    if not target_weights:
        raise ValueError("两段式选债结果为空：请检查 PIT 覆盖与过滤参数")

    picked = sorted({sym for picks in target_weights.values() for sym in picks})
    feeds = pit_to_feeds(pit_table, picked)
    if not feeds:
        raise ValueError("被选中券在 PIT 中无价格数据")

    if logger is not None:
        logger.info(
            "两段式: 调仓 %d 次, 被选券并集 %d 只 (全量池无需建 feed)",
            len(target_weights), len(feeds),
        )

    engine = BacktestEngine(cfg, commission_overrides=ETF_COMMISSION, logger=logger)
    params = {
        "target_weights": target_weights,
        "convertible_symbols": tuple(feeds.keys()),
        "convertible_lot_size": 10,
        "lot_size": 10,
    }
    result = engine.run(WeightTargetStrategy, params, feeds)
    return result, target_weights, feeds

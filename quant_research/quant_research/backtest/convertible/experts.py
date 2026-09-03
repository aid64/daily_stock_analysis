"""MWU 双低专家：当日快照版 / PIT 历史快照版

R-B 拆分自 convertible.py；与 MomentumExpert 等遵循同一 Expert 协议。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .picks import pit_snapshot_picks, rating_rank
from .snapshot import PIT_REQUIRED_COLUMNS, normalize_cb_symbol


class ConvertibleDoubleLowExpert:
    """可转债双低专家（P5，供 MWU 融合层使用）

    与 MomentumExpert / MeanReversionExpert / DefensiveExpert 遵循同一
    Expert 协议：select(datas) -> {symbol: 专家内目标权重}。

    消费 bond_table（当日快照，含 symbol/price/premium_rate/rating/
    list_date）与候选转债的日线 feed。每个 MWU 调仓周期：
      1. 按快照 + 当前 feed 现价计算双低排名（price 用现价增强时序性）
      2. 返回 top_n 等权建议（债券 10 张/手申报单位由执行层处理）
      3. 选不出（无满足过滤条件的转债）返回空 dict → MWU 层按现金处理

    与 ConvertibleDoubleLow 回测策略共用 _compute_double_low_rank 逻辑，
    保证"历史回测"与"实盘信号"一致。
    """

    def __init__(
        self,
        bond_table,
        top_n: int = 5,
        min_rating: str = "AA-",
        max_premium: float = 20.0,
        exclude_new_days: int = 30,
        max_price_median: bool = True,
        name: str = "convertible_double_low",
    ):
        self.name = name
        if bond_table is None or len(bond_table) == 0:
            raise ValueError("ConvertibleDoubleLowExpert 需要 bond_table")
        self._bond_table = bond_table.copy()
        self.top_n = int(top_n)
        self.min_rating = min_rating
        self.max_premium = float(max_premium)
        self.exclude_new_days = int(exclude_new_days)
        self.max_price_median = bool(max_price_median)

    def _compute_rank(self, view) -> dict[str, int]:
        """基于快照 + 当前 feed 现价计算双低排名（越小越优）"""
        table = self._bond_table.copy()
        rows = []
        for s in view.all_series():
            name = s.name
            row = table[table["symbol"] == name]
            if row.empty:
                continue
            r = row.iloc[0].to_dict()
            price = s.last_close()
            if not np.isfinite(price) or price <= 0:
                continue
            premium = float(pd.to_numeric(r.get("premium_rate", np.nan), errors="coerce"))
            rating = r.get("rating", "")
            if not np.isfinite(premium):
                continue
            if rating_rank(rating) > rating_rank(self.min_rating):
                continue
            if premium > self.max_premium:
                continue
            r["_price"] = price
            r["_premium"] = premium
            r["_double_low"] = price + premium
            rows.append(r)

        if not rows:
            return {}
        cand = pd.DataFrame(rows)
        cand = cand.sort_values("_double_low", ascending=True)
        return {row["symbol"]: i for i, row in cand.reset_index(drop=True).iterrows()}

    def select(self, view) -> dict[str, float]:
        rank = self._compute_rank(view)
        if not rank:
            return {}
        top = sorted(rank, key=rank.get)[: self.top_n]
        w = 1.0 / len(top)
        return {sym: w for sym in top}


class PITConvertibleDoubleLowExpert:
    """按调仓日历史快照选债的可转债双低专家。"""

    REQUIRED_COLUMNS = PIT_REQUIRED_COLUMNS  # 单一真源在 snapshot.py

    def __init__(
        self,
        pit_table,
        top_n: int = 5,
        min_rating: str = "AA-",
        max_premium: float = 20.0,
        exclude_new_days: int = 30,
        max_price_median: bool = True,
        max_conv_value: Optional[float] = None,
        min_maturity_days: Optional[int] = None,
        max_price: Optional[float] = None,
        min_candidates: Optional[int] = None,
        dd_median_upper: Optional[float] = None,
        dd_median_lower: Optional[float] = None,
        name: str = "convertible_double_low_pit",
    ):
        if pit_table is None or len(pit_table) == 0:
            raise ValueError("PITConvertibleDoubleLowExpert 需要 pit_table")
        missing = self.REQUIRED_COLUMNS - set(pit_table.columns)
        if missing:
            raise ValueError(f"PIT 可转债快照缺少字段: {sorted(missing)}")
        table = pit_table.copy()
        table["as_of"] = pd.to_datetime(table["as_of"], errors="coerce").dt.normalize()
        table["list_date"] = pd.to_datetime(table["list_date"], errors="coerce")
        table["price"] = pd.to_numeric(table["price"], errors="coerce")
        table["premium_rate"] = pd.to_numeric(table["premium_rate"], errors="coerce")
        table["symbol"] = table["symbol"].map(normalize_cb_symbol)
        if "maturity_date" in table.columns:
            table["maturity_date"] = pd.to_datetime(table["maturity_date"], errors="coerce")
        table = table.dropna(subset=["as_of", "symbol", "price", "premium_rate"])
        if table.empty:
            raise ValueError("PIT 可转债快照没有有效记录")
        self.name = name
        self._pit_table = table
        self.top_n = int(top_n)
        self.min_rating = min_rating
        self.max_premium = float(max_premium)
        self.exclude_new_days = int(exclude_new_days)
        self.max_price_median = bool(max_price_median)
        # v2 生命周期/强赎风险过滤（B1，2026-08-18）。None = 关闭（向后兼容）
        self.max_conv_value = float(max_conv_value) if max_conv_value is not None else None
        self.min_maturity_days = int(min_maturity_days) if min_maturity_days is not None else None
        self.max_price = float(max_price) if max_price is not None else None
        # 池规模下限（P2，2026-08-18）：过滤后候选 < min_candidates 时专家退出
        # （返回空 → MWU 按现金处理），防止存量缩水期的集中度暴露。
        # 阈值建议取结构值 top_n×3，不做网格扫描（避免对失败折拟合）。
        self.min_candidates = int(min_candidates) if min_candidates is not None else None
        # 温度计门控（P1-1，2026-08-29）：全市场双低中位数 ≥ dd_median_upper
        # → 专家让位现金；< dd_median_lower → 正常；中间 → 缩量。None = 关闭。
        # 门槛值取社区公开规则（yyb凌波全清仓线 170 / 启用线 140），非网格扫描产物。
        self.dd_median_upper = float(dd_median_upper) if dd_median_upper is not None else None
        self.dd_median_lower = float(dd_median_lower) if dd_median_lower is not None else None

    def _apply_v2_filters(self, snapshot: pd.DataFrame, current_ts: pd.Timestamp) -> pd.DataFrame:
        """B1 行业标准过滤（在双低打分前应用）。

        - max_conv_value: 转股价值 = 价格 / (1 + 溢价率/100)（数学恒等，PIT 可直接
          推导），超限视为强赎风险区剔除（社区共识阈值 135，强赎触发线 130×15/30日）
        - min_maturity_days: 剩余期限不足剔除（临期债波动衰减 + 反向选择，咖啡侠/
          狐狸说投资共识：回避到期 1 年内）；maturity 缺失行宽容保留
        - max_price: 价格硬顶（债性保护底线，社区底线规则"价格<115"的宽松版）
        """
        if self.max_conv_value is not None and not snapshot.empty:
            conv_value = snapshot["price"] / (1.0 + snapshot["premium_rate"] / 100.0)
            snapshot = snapshot[conv_value <= self.max_conv_value]
        if (
            self.min_maturity_days is not None
            and not snapshot.empty
            and "maturity_date" in snapshot.columns
        ):
            remain_days = (snapshot["maturity_date"] - current_ts).dt.days
            snapshot = snapshot[remain_days.isna() | (remain_days >= self.min_maturity_days)]
        if self.max_price is not None and not snapshot.empty:
            snapshot = snapshot[snapshot["price"] <= self.max_price]
        return snapshot

    def _snapshot(self, view) -> pd.DataFrame:
        current_date = pd.Timestamp(view.current_date()).normalize()
        snapshot = self._pit_table[self._pit_table["as_of"] == current_date].copy()
        if snapshot.empty:
            return snapshot
        prices = {}
        for s in view.all_series():
            p = s.last_close()
            if np.isfinite(p) and p > 0:
                prices[s.name] = p
        snapshot = snapshot[snapshot["symbol"].isin(prices)].copy()
        snapshot["price"] = snapshot["symbol"].map(prices)
        return snapshot

    def select(self, view) -> dict[str, float]:
        snapshot = self._snapshot(view)
        if snapshot.empty:
            return {}
        current_ts = pd.Timestamp(view.current_date()).normalize()
        return pit_snapshot_picks(
            snapshot,
            top_n=self.top_n,
            min_rating=self.min_rating,
            max_premium=self.max_premium,
            exclude_new_days=self.exclude_new_days,
            max_price_median=self.max_price_median,
            as_of=current_ts,
            max_conv_value=self.max_conv_value,
            min_maturity_days=self.min_maturity_days,
            max_price=self.max_price,
            min_candidates=self.min_candidates,
            dd_median_upper=self.dd_median_upper,
            dd_median_lower=self.dd_median_lower,
        )

"""可转债双低轮动 backtrader 策略（P5）

R-B 拆分自 convertible.py。注意：溢价率/评级取自当日快照，回测跨年
存在幸存者偏差与固定 universe 局限（详见类 docstring）。
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import pandas as pd

from .picks import rating_rank


class ConvertibleDoubleLow(bt.Strategy):
    """可转债双低轮动（Backtrader 实现）

    策略消费 bond_table（DataFrame 快照，含 symbol/price/premium_rate/
    rating/list_date）与候选标的的日线 feed。每个调仓周期：
      1. 从快照计算双低排序（价格用快照价——免费源无历史 PIT 溢价率，
         文档标注此为近似；price_col 可换成 feed 现价增强时序性）
      2. 买入 top_n（等权，100 张/手可转债申报单位，2% 现金缓冲）
      3. 排名跌出 top_n 的持仓卖出

    注意：双低溢价率与评级是"当日快照"，回测跨年时存在幸存者偏差与
    固定 universe 局限，仅作近期行情验证，不作为长期盈利承诺。
    """

    params = (
        ("bond_table", None),       # DataFrame：symbol/price/premium_rate/rating/list_date
        ("top_n", 5),
        ("min_rating", "AA-"),
        ("max_premium", 20.0),
        ("exclude_new_days", 30),
        ("rebalance_freq", "M"),    # M 月 / W 周（转债低频）
        ("lot_size", 10),           # 可转债申报单位 10 张
        ("price_col", "price"),
        ("premium_col", "premium_rate"),
        ("rating_col", "rating"),
        ("list_date_col", "list_date"),
    )

    _LOT_BUFFER = 0.98

    def __init__(self):
        if self.p.bond_table is None:
            raise ValueError("ConvertibleDoubleLow 需要 bond_table 参数")
        self._last_period: tuple | None = None
        self._rank: dict[str, int] = {}   # symbol -> 双低排名（越小越优）

    def _is_rebalance_day(self, dt) -> bool:
        freq = str(self.p.rebalance_freq).upper()
        if freq == "D":
            return True
        if freq == "M":
            key = (dt.year, dt.month)
        else:
            iso = dt.isocalendar()
            key = (iso[0], iso[1])
        if key != self._last_period:
            self._last_period = key
            return True
        return False

    def _open_order_symbols(self) -> set[str]:
        return {o.data._name for o in self.broker.get_orders_open()}

    def _compute_rank(self, dt) -> dict[str, int]:
        """基于快照 + 当前 feed 价格计算双低排名"""
        table = self.p.bond_table.copy()
        rows = []
        for d in self.datas:
            name = d._name
            row = table[table["symbol"] == name]
            if row.empty:
                continue
            r = row.iloc[0].to_dict()
            price = float(d.close[0])
            if not np.isfinite(price) or price <= 0:
                continue
            premium = float(pd.to_numeric(r.get(self.p.premium_col, np.nan), errors="coerce"))
            rating = r.get(self.p.rating_col, "")
            if not np.isfinite(premium):
                continue
            rank = rating_rank(rating)
            if rank > rating_rank(self.p.min_rating):
                continue
            if premium > self.p.max_premium:
                continue
            r["_price"] = price
            r["_premium"] = premium
            r["_rating_rank"] = rank
            r["_double_low"] = price + premium
            rows.append(r)

        if not rows:
            return {}
        cand = pd.DataFrame(rows)
        cand = cand.sort_values("_double_low", ascending=True)
        return {row["symbol"]: i for i, row in cand.reset_index(drop=True).iterrows()}

    def next(self):
        dt = self.datas[0].datetime.date(0)
        if not self._is_rebalance_day(dt):
            return
        self._rank = self._compute_rank(dt)
        targets = set(self._rank.keys())

        # 卖出：跌出前 top_n 的持仓
        top = set(list(targets)[: int(self.p.top_n)])
        open_syms = self._open_order_symbols()
        for d in self.datas:
            name = d._name
            if name in open_syms:
                continue
            pos = self.getposition(d)
            if pos.size <= 0:
                continue
            if name not in top:
                self.close(d)

        # 买入 top_n（等权，10 张/手）
        per_value = self.broker.getvalue() / max(1, len(top))
        for d in self.datas:
            name = d._name
            if name not in top or name in open_syms:
                continue
            pos = self.getposition(d)
            price = float(d.close[0])
            if not np.isfinite(price) or price <= 0:
                continue
            lot = int(self.p.lot_size)
            diff_value = per_value - pos.size * price
            lots = int(diff_value * self._LOT_BUFFER / price / lot)
            if lots > 0:
                self.buy(d, size=lots * lot)

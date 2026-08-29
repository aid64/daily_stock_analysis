"""实盘信号层：把回测专家复用到实时价格上

回测里的专家（MomentumExpert / MeanReversionExpert / DefensiveExpert）
消费 backtrader feed（.close.get(size) / .amount.get(size) / ._name）。
实盘没有 feed，本模块用 PriceSeries 提供等价的最小接口，让同一套
专家在"历史回测"和"实时调仓"两个场景复用，保证信号一致。

LiveMWU 维护专家权重，按调仓周期执行 MWU 更新（含 Fixed Share
回拉，与回测 mwu.ETFMWU 同逻辑），输出归一化目标权重交给执行引擎。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from ..backtest.mwu import Expert


class _AmountLine:
    """成交额线：get(size) 返回成交额数组（与 backtrader line 语义对齐）"""

    def __init__(self, amount: np.ndarray):
        self._amount = np.asarray(amount, dtype=float)

    def get(self, size: int = 1) -> np.ndarray:
        size = max(1, int(size))
        return self._amount[-size:]


class PriceSeries:
    """最小行情序列适配器：给专家提供 close/amount 查询接口

    包装最近 price_window 天的收盘价与近 20 日成交额。
    """

    def __init__(
        self,
        name: str,
        closes: np.ndarray,
        amount: float | np.ndarray = 1e8,
    ):
        self._name = name
        self._closes = np.asarray(closes, dtype=float)
        if isinstance(amount, (int, float)):
            self._amount = np.full(len(self._closes), float(amount))
        else:
            self._amount = np.asarray(amount, dtype=float)

    def __len__(self) -> int:
        return len(self._closes)

    def __getitem__(self, idx: int) -> float:
        return float(self._closes[idx - 1])  # 0 → 最新

    @property
    def close(self) -> "PriceSeries":
        return self

    @property
    def amount(self) -> "_AmountLine":
        return _AmountLine(self._amount)

    def get(self, size: int = 1) -> np.ndarray:
        size = max(1, int(size))
        if size == 1:
            return self._closes[-1:]
        return self._closes[-size:]

    @property
    def price(self) -> float:
        return float(self._closes[-1])

    @property
    def recent_amount(self) -> float:
        return float(self._amount[-min(20, len(self._amount)):].mean()) if len(self._amount) else 0.0


class LiveMWU:
    """实时 MWU 权重维护器（与回测 ETFMWU 逻辑一致）

    用法：
        mwu = LiveMWU(experts, eta=0.1, loss_scale=0.05, share_alpha=0.02)
        每周调仓：mwu.update(prices_dict)  → 返回 {symbol: 目标权重}
    其中 prices_dict = {symbol: (closes, amount)}。
    """

    def __init__(
        self,
        experts: list[Expert],
        eta: float = 0.1,
        loss_scale: float = 0.05,
        share_alpha: float = 0.02,
        logger: Optional[logging.Logger] = None,
    ):
        if len(experts) < 2:
            raise ValueError("LiveMWU 至少需要 2 个专家")
        self._experts = experts
        self._eta = float(eta)
        self._loss_scale = float(loss_scale)
        self._share_mass = float(share_alpha) / len(experts)
        n = len(experts)
        self._weights: dict[str, float] = {e.name: 1.0 / n for e in experts}
        self._last_picks: dict[str, dict[str, float]] = {}
        self.logger = logger or logging.getLogger("live_mwu")

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def _build_series(self, prices: dict[str, tuple[np.ndarray, np.ndarray]]) -> list[PriceSeries]:
        return [PriceSeries(sym, closes, amount) for sym, (closes, amount) in prices.items()]

    def update(self, prices: dict[str, tuple[np.ndarray, np.ndarray]]) -> dict[str, float]:
        """按当前行情更新权重并返回归一化目标组合权重"""
        series = self._build_series(prices)

        # 1) MWU 乘性更新（用上期选取价的纸面收益）
        for e in self._experts:
            prev = self._last_picks.get(e.name)
            if prev:
                rets = []
                for sym, base in prev.items():
                    s = next((x for x in series if x._name == sym), None)
                    if s is None or base <= 0:
                        continue
                    p = s.price
                    if np.isfinite(p) and p > 0:
                        rets.append(p / base - 1.0)
                r = float(np.mean(rets)) if rets else 0.0
                loss = min(max(-r / self._loss_scale, 0.0), 1.0)
                self._weights[e.name] = self._weights[e.name] * (1.0 - self._eta * loss)

        # 2) Fixed Share 回拉 + 归一化
        total = sum(max(w, 1e-6) for w in self._weights.values())
        n = len(self._experts)
        for k in self._weights:
            norm = max(self._weights[k], 1e-6) / total
            self._weights[k] = (1.0 - self._share_mass * n) * norm + self._share_mass

        # 3) 专家选股 → 目标组合
        target: dict[str, float] = {}
        picks: dict[str, dict[str, float]] = {}
        for e in self._experts:
            sel = e.select(series)
            picks[e.name] = {sym: p for sym, p in sel.items()}
            w = self._weights.get(e.name, 0.0)
            if w <= 0 or not sel:
                continue
            for sym, ew in sel.items():
                target[sym] = target.get(sym, 0.0) + w * float(ew)

        # 4) 记录选取价供下期更新
        self._last_picks = {}
        for e in self._experts:
            rec = {}
            for sym in picks.get(e.name, {}):
                s = next((x for x in series if x._name == sym), None)
                if s is not None:
                    rec[sym] = s.price
            self._last_picks[e.name] = rec

        self.logger.info(
            "MWU 权重 %s，目标组合 %s",
            {k: round(v, 3) for k, v in self._weights.items()},
            {k: round(v, 3) for k, v in target.items()},
        )
        return target

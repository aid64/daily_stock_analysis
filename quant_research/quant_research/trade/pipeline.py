"""实盘信号层：把回测专家复用到实时价格上

重构 R1（2026-08-29）：适配方向反转。专家只消费 core.market 的
MarketView 协议（协议真源在 core 包），实盘侧用 ArrayMarketView
提供数组实现；权重乘性更新与建议合成委托 core.mwu_core.MWUCore
——与回测侧 ETFMWU 共用唯一实现，保证"历史回测"与"实时调仓"
的 MWU 语义逐位一致（此前 PriceSeries 反向模仿 backtrader line、
权重更新在两处手抄，易漂移）。

LiveMWU 每次调仓调用 update(prices)：
    prices = {symbol: (closes, amounts)}，时间升序、[-1] 为当前 bar
返回归一化目标组合权重 {symbol: weight}，交给执行引擎（Executor）。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from ..backtest.mwu import Expert
from ..core.market import ArrayMarketView
from ..core.mwu_core import MWUCore


class LiveMWU:
    """实时 MWU 权重维护器（回测/实盘共用 MWUCore）

    用法：
        mwu = LiveMWU(experts, eta=0.1, loss_scale=0.05, share_alpha=0.02)
        每周调仓：mwu.update(prices_dict)  → 返回 {symbol: 目标权重}
    其中 prices_dict = {symbol: (closes, amounts)}。
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
        self._core = MWUCore(
            [e.name for e in experts],
            eta=eta,
            loss_scale=loss_scale,
            share_alpha=share_alpha,
        )
        self.logger = logger or logging.getLogger("live_mwu")

    @property
    def weights(self) -> dict[str, float]:
        return self._core.weights

    def update(
        self,
        prices: dict[str, tuple[np.ndarray, float | np.ndarray]],
        current_date=None,
    ) -> dict[str, float]:
        """按当前行情更新权重并返回归一化目标组合权重

        current_date 可选（PIT 专家需要当前日期时传入 datetime.date）。
        """
        view = ArrayMarketView(prices, current_date=current_date)
        prices_flat = {s.name: s.last_close() for s in view.all_series()}

        # 1) MWU 乘性更新（用上期选取价的纸面收益）+ Fixed Share 回拉
        self._core.update_weights(prices_flat)

        # 2) 专家选股 → 按权重合成目标组合 → 记录选取价
        picks = {e.name: e.select(view) for e in self._experts}
        target = self._core.combine(picks)
        self._core.record_picks(picks, prices_flat)

        self.logger.info(
            "MWU 权重 %s，目标组合 %s",
            {k: round(v, 3) for k, v in self._core.weights.items()},
            {k: round(v, 3) for k, v in target.items()},
        )
        return target

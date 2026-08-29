"""MWU（Multiplicative Weights Update）融合核

全项目唯一的专家权重融合实现：回测侧（backtest/mwu.py 的 ETFMWU
策略）与实盘侧（trade/pipeline.py 的 LiveMWU）都委托本类，
保证"历史回测"与"实时调仓"的权重更新语义逐位一致
（重构 R1 前该算法在两处手抄，存在漂移风险）。

来源：知乎《谈量化交易中被严重低估的执行模块》（栀染）方法论，
"（一）请使用 MWU 进行策略融合"，评论区实战修正后的散户落地版。
理论背景见 quant_research/PRIMARY_SOURCE_RESEARCH.md 第 3 节：
regret 界是相对保证（追平事后最优专家），融合层不创造 alpha，
专家自身的样本外边际才是全部。

更新规则（每调仓周期）：
1. 乘性更新：专家上期建议组合的纸面收益 r_i，
   l_i = clip(-r_i / loss_scale, 0, 1)；w_i ← w_i · (1 - η·l_i)
   （R1b 将把该单边 loss 映射修正为理论一致的线性映射，见 git 历史）
2. Fixed Share 回拉（Herbster & Warmuth 1998）：w_i ← (1-α)·ŵ_i + α/N，
   ŵ 为权重下限截断后的归一化。标准 MWU 只惩罚不奖励，专家权重被
   长期熊市压到 0 后即使后续表现最优也无法恢复（A 股 2015 股灾 →
   2019-21 结构牛实测锁死）；Fixed Share 是 regime switch 场景的
   标准修正。α = share_alpha。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


class MWUCore:
    """专家权重维护器（纯逻辑，不依赖任何引擎对象）"""

    _W_FLOOR = 1e-6  # 权重下限，防乘性更新数值下溢后无法恢复

    def __init__(
        self,
        expert_names: Sequence[str],
        eta: float = 0.1,
        loss_scale: float = 0.05,
        share_alpha: float = 0.02,
    ):
        names = [str(n) for n in expert_names]
        if len(names) < 2:
            raise ValueError("MWUCore 至少需要 2 个专家")
        if len(set(names)) != len(names):
            raise ValueError(f"专家名重复: {names}")
        n = len(names)
        self._names = names
        self._weights: dict[str, float] = {name: 1.0 / n for name in names}
        self._eta = float(eta)
        self._loss_scale = float(loss_scale)
        self._share_mass = float(share_alpha) / n
        # 上期各专家建议 {expert_name: {symbol: 选取时价格}}
        self._last_picks: dict[str, dict[str, float]] = {}

    @property
    def expert_names(self) -> list[str]:
        return list(self._names)

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    @property
    def last_picks(self) -> dict[str, dict[str, float]]:
        return {name: dict(picks) for name, picks in self._last_picks.items()}

    # ── 权重更新 ─────────────────────────────────

    def update_weights(self, prices: Mapping[str, float]) -> None:
        """用当前价格结算上期建议的纸面收益，执行乘性更新 + Fixed Share 回拉

        prices = {symbol: 当前价}。非有限/非正价格或缺失标的的收益项
        跳过；某专家全部收益项无效时按现金收益 0 处理。
        """
        eta = self._eta
        scale = self._loss_scale
        for name in self._names:
            prev = self._last_picks.get(name)
            if not prev:
                continue
            rets = []
            for sym, base in prev.items():
                price = prices.get(sym)
                if price is None or base <= 0:
                    continue
                price = float(price)
                if not np.isfinite(price) or price <= 0:
                    continue
                rets.append(price / base - 1.0)
            r = float(np.mean(rets)) if rets else 0.0
            loss = min(max(-r / scale, 0.0), 1.0)
            self._weights[name] = self._weights[name] * (1.0 - eta * loss)

        self._fixed_share_pullback()

    def _fixed_share_pullback(self) -> None:
        """向均匀分布回拉，防止权重锁死（语义与历史版本逐位一致）"""
        total = sum(max(w, self._W_FLOOR) for w in self._weights.values())
        n = len(self._names)
        for k in self._weights:
            normalized = max(self._weights[k], self._W_FLOOR) / total
            self._weights[k] = (1.0 - self._share_mass * n) * normalized + self._share_mass

    # ── 建议合成 ─────────────────────────────────

    def combine(
        self,
        picks_by_expert: Mapping[str, Mapping[str, float]],
    ) -> dict[str, float]:
        """按当前权重合成各专家建议 → {symbol: 组合权重}（同标的合并）"""
        target: dict[str, float] = {}
        for name in self._names:
            sel = picks_by_expert.get(name)
            if not sel:
                continue
            w = self._weights.get(name, 0.0)
            if w <= 0:
                continue
            for sym, ew in sel.items():
                target[sym] = target.get(sym, 0.0) + w * float(ew)
        return target

    def record_picks(
        self,
        picks_by_expert: Mapping[str, Mapping[str, float]],
        prices: Mapping[str, float],
    ) -> None:
        """记录本期各专家建议及选取时价格（供下期纸面收益结算）

        价格非有限/非正的标的跳过记录（与历史版本一致：晚上市或
        数据异常的标的不参与下期收益结算）。
        """
        recorded: dict[str, dict[str, float]] = {}
        for name in self._names:
            sel = picks_by_expert.get(name, {})
            rec: dict[str, float] = {}
            for sym in sel:
                price = prices.get(sym)
                if price is None:
                    continue
                price = float(price)
                if np.isfinite(price) and price > 0:
                    rec[sym] = price
            recorded[name] = rec
        self._last_picks = recorded

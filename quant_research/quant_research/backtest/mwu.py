"""MWU（Multiplicative Weights Update）多策略融合层

来源：知乎《谈量化交易中被严重低估的执行模块》（栀染）方法论
"（一）请使用 MWU 进行策略融合"，评论区实战修正后的散户落地版：

- 机构用 XGBoost 融合多因子是 batch learning，假设未来重复过去；
  A 股分布随时间剧烈漂移（regime switch），在线更新的 MWU 有
  worst-case regret 保证：R_T ≤ 2√(T·ln N) + √(T/2)，平均 regret
  以 O(1/√T) 趋近 0——无论市场风格怎么切换，长期表现收敛于
  事后最优的那个专家策略，唯一超参是学习率 η。
- 评论区修正："组合优化是真正的 alpha 放大器"（认可融合/调度价值）；
  "取整那点影响说明规模太小"（1 万本金不做整数规划，沿用 lot 取整
  + 2% 缓冲）；"执行只有 trade-off"（不做拆单，无 market impact）。
- 理论边界提示（PRIMARY_SOURCE_RESEARCH.md 第 3 节）：regret 界是
  相对保证，不保证绝对收益；专家无样本外边际时融合层只能"不比
  最优专家差太多"。

专家（Expert）是快照选择器：select(view) 返回该专家当前的目标
持仓 {symbol: 专家内权重}，数据访问只经 core.market.MarketView
协议（重构 R1：回测/实盘共用同一专家代码，不再各写一份）。

权重乘性更新与 Fixed Share 回拉的唯一实现是 core.mwu_core.MWUCore，
ETFMWU（回测）与 LiveMWU（实盘）都委托它，保证两侧语义逐位一致。
MWU 层每个调仓周期：

1. 用专家上期建议组合的纸面收益 r_i 更新权重（MWUCore.update_weights）
2. 目标组合 = Σ w_i × 专家组合（MWUCore.combine）
3. 按目标权重交易（先卖后买、整数手、2% 现金缓冲）
"""

from __future__ import annotations

import numpy as np
import backtrader as bt

from ..core.market import MarketView
from ..core.mwu_core import MWUCore
from .adapter import BTMarketView
from .strategies import momentum_scores


# ── 专家信号层 ──────────────────────────────────────────────


class Expert:
    """专家协议：快照选择器，返回 {symbol: 专家内目标权重}

    子类按需实现 select(view)。name 用于 MWU 权重字典的键。
    """

    name: str

    def select(self, view: MarketView) -> dict[str, float]:
        raise NotImplementedError


def rsi(closes: np.ndarray, period: int) -> float:
    """Cutler's RSI（简单均值版，无 Wilder 平滑，跨标的可复现）

    单调上涨 → 100，单调下跌 → 0。period=2 是社区均值回归共识参数。
    """
    if closes.size < period + 1:
        return float("nan")
    delta = np.diff(closes[-(period + 1):].astype(float))
    gain = float(delta.clip(min=0).mean())
    loss = float((-delta.clip(max=0)).mean())
    if loss == 0.0:
        return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


class MomentumExpert:
    """动量专家：与 ETFMomentum 相同的双动量 + 均线过滤逻辑"""

    def __init__(
        self,
        lookback: int = 60,
        top_k: int = 3,
        ma_filter: int = 250,
        min_volume: float = 1_000_000.0,
        mom_windows: tuple[int, ...] | None = None,
        vol_adjusted: bool = False,
        name: str = "momentum",
    ):
        self.name = name
        self.lookback = int(lookback)
        self.top_k = int(top_k)
        self.ma_filter = int(ma_filter)
        self.min_volume = float(min_volume)
        self.mom_windows = mom_windows
        self.vol_adjusted = bool(vol_adjusted)

    def _windows(self) -> tuple[int, ...]:
        if self.mom_windows:
            windows = tuple(sorted({int(w) for w in self.mom_windows if int(w) > 0}))
            if windows:
                return windows
        return (self.lookback,)

    def select(self, view: MarketView) -> dict[str, float]:
        scores = momentum_scores(
            view,
            windows=self._windows(),
            ma_filter=self.ma_filter,
            min_volume=self.min_volume,
            vol_adjusted=self.vol_adjusted,
        )
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        picked = ranked[: self.top_k]
        if not picked:
            return {}
        w = 1.0 / len(picked)
        return {sym: w for sym, _ in picked}


class MeanReversionExpert:
    """均值回归专家（社区调研共识参数）

    - 入选：RSI(2) < rsi_buy（超卖）且 收盘价 > MA200（长期趋势过滤，
      只在牛市回调中接飞刀，熊市下跌趋势不做反转）
    - 排序：RSI 升序（越超卖越优先）
    - 出口：下个调仓周期重算时 RSI 回升 / 跌破 MA200 即自然剔除
    """

    def __init__(
        self,
        rsi_period: int = 2,
        rsi_buy: float = 30.0,
        ma_filter: int = 200,
        top_k: int = 3,
        min_volume: float = 1_000_000.0,
        name: str = "mean_reversion",
    ):
        self.name = name
        self.rsi_period = int(rsi_period)
        self.rsi_buy = float(rsi_buy)
        self.ma_filter = int(ma_filter)
        self.top_k = int(top_k)
        self.min_volume = float(min_volume)

    def select(self, view: MarketView) -> dict[str, float]:
        need = max(self.rsi_period + 1, self.ma_filter)
        candidates: list[tuple[float, str]] = []

        for s in view.all_series():
            if len(s) < need:
                continue
            closes = np.asarray(s.closes(need), dtype=float)
            amounts = np.asarray(s.amounts(min(20, len(s))), dtype=float)
            if closes.size < need or amounts.size == 0:
                continue
            if not (np.isfinite(closes).all() and np.isfinite(amounts).all()):
                continue
            close = float(closes[-1])
            if close <= 0 or float(amounts.mean()) < self.min_volume:
                continue
            ma = float(closes[-self.ma_filter:].mean())
            if ma <= 0 or close <= ma:  # 趋势过滤
                continue
            value = rsi(closes, self.rsi_period)
            if np.isfinite(value) and value < self.rsi_buy:
                candidates.append((value, s.name))

        candidates.sort()  # RSI 升序，最超卖优先
        picked = candidates[: self.top_k]
        if not picked:
            return {}
        w = 1.0 / len(picked)
        return {sym: w for _, sym in picked}


class DefensiveExpert:
    """防御专家：永远建议全仓持有防御资产（国债 ETF）"""

    def __init__(self, symbol: str, name: str = "defensive"):
        self.name = name
        self.symbol = symbol

    def select(self, view: MarketView) -> dict[str, float]:
        s = view.series(self.symbol)
        if s is None:
            return {}
        price = s.last_close()
        if np.isfinite(price) and price > 0:
            return {self.symbol: 1.0}
        return {}


# ── MWU 融合层 ──────────────────────────────────────────────


# ── 执行层（R5 抽取：ETFMWU 与 WeightTargetStrategy 共用）────


class TargetWeightExecutionMixin:
    """目标权重调仓执行：先卖后买、整数手、5% 容差、止损带

    依赖宿主提供 bt.Strategy 上下文（self.datas/self.broker/
    getposition/self.params 中的 lot 配置）。
    """

    _LOT_BUFFER = 0.98   # 与 ETFMomentum 一致的现金缓冲
    _TOLERANCE = 1.05    # 卖出容差：目标市值 5% 以内的偏离不动

    def _get_data(self, name: str):
        for d in self.datas:
            if d._name == name:
                return d
        return None

    def _open_order_symbols(self) -> set[str]:
        return {o.data._name for o in self.broker.get_orders_open()}

    def _lot_of(self, name: str) -> int:
        if name in set(self.p.convertible_symbols or ()):
            return int(self.p.convertible_lot_size)
        if name == self.p.defensive:
            return int(self.p.defensive_lot_size)
        return int(self.p.lot_size)

    def _check_stop_loss(self) -> None:
        if self.p.stop_loss_pct is None:
            return
        stop = float(self.p.stop_loss_pct)
        open_syms = self._open_order_symbols()
        for d in self.datas:
            if d._name == self.p.defensive or d._name in open_syms:
                continue
            pos = self.getposition(d)
            if pos.size <= 0 or pos.price <= 0:
                continue
            price = float(d.close[0])
            if np.isfinite(price) and price > 0 and price / float(pos.price) - 1.0 <= stop:
                self.close(d)

    def _rebalance_to(self, target: dict[str, float]) -> None:
        """按目标权重调仓：先卖后买，整数手，买不起一手的自动跳过

        全平语义（R5 修正）：目标权重 ≤0 的持仓整仓卖出（此前按"超额
        ×0.98 取整"计算，1 手持仓的超额取整为 0，导致小资金 1 手仓位
        永远无法换仓）；目标内超配部分仍按超额+缓冲卖出。
        """
        total_value = self.broker.getvalue()
        open_syms = self._open_order_symbols()

        # 卖出：目标外的持仓整仓退出；目标内仅卖出超出 5% 容差的部分
        for d in self.datas:
            name = d._name
            if name in open_syms:
                continue
            pos = self.getposition(d)
            if pos.size <= 0:
                continue
            price = float(d.close[0])
            if not np.isfinite(price) or price <= 0:
                continue
            lot = self._lot_of(name)
            tgt_value = total_value * float(target.get(name, 0.0))
            cur_value = pos.size * price
            if name not in target or float(target.get(name, 0.0)) <= 0.0:
                # 全平：整仓（按申报单位取整）
                lots = pos.size // lot
                if lots > 0:
                    self.sell(d, size=lots * lot)
                continue
            if cur_value > tgt_value * self._TOLERANCE:
                lots = int((cur_value - tgt_value) * self._LOT_BUFFER / price / lot)
                if lots > 0:
                    self.sell(d, size=lots * lot)

        # 买入：目标市值超出当前市值 + 一手成本的差额。
        # 按差额升序提交（小额先成交）：同一 bar 多个买单按提交顺序
        # 撮合，先提交者先占用现金；大额单最后提交，避免其挤占
        # 后续小额单的现金导致连环 Margin 拒单（诊断实测每周调仓
        # 拒单率可达 1/3）。
        buy_candidates: list[tuple[float, object, int]] = []
        for d in self.datas:
            name = d._name
            if name in open_syms:
                continue
            price = float(d.close[0])
            if not np.isfinite(price) or price <= 0:
                continue
            w = float(target.get(name, 0.0))
            if w <= 0:
                continue
            lot = self._lot_of(name)
            pos = self.getposition(d)
            tgt_value = total_value * w
            cur_value = pos.size * price
            diff_value = tgt_value - cur_value
            if diff_value * self._LOT_BUFFER >= price * lot:  # 至少买得起一手
                lots = int(diff_value * self._LOT_BUFFER / price / lot)
                if lots > 0:
                    buy_candidates.append((diff_value, d, lots * lot))

        for diff_value, d, size in sorted(buy_candidates, key=lambda x: x[0]):
            self.buy(d, size=size)


class ETFMWU(TargetWeightExecutionMixin, bt.Strategy):
    """ETF 多策略 MWU 融合（P3）

    每个调仓周期按专家纸面收益乘性更新权重，按权重合成目标组合。
    防御专家 + 动量专家 + 均值回归专家组合时，MWU 自动完成
    "牛市抬动量、震荡抬均值回归、熊市抬防御"的风格切换——
    即 P2 结论"需要市场状态门控"的免预测实现。

    纸面收益 = 上期建议组合的等权价格收益（不含交易成本），
    专家空选时按现金收益 0 处理。
    """

    params = (
        ("experts", None),            # [Expert, ...]，至少 2 个
        ("eta", 0.1),                 # MWU 学习率 η ∈ (0, 1]
        ("loss_scale", 0.05),         # 满量程单期收益：±5% → loss 1/0（R1b 线性映射）
        ("share_alpha", 0.02),        # Fixed Share 回拉系数：每期向均匀
                                      # 分布回拉 α/N。标准 MWU 只惩罚不奖励，
                                      # 专家权重被长期熊市压到 0 后，即使后续
                                      # 表现最优占比也无法恢复（A 股 2015 股灾
                                      # → 2019-21 结构牛实测锁死）；Fixed Share
                                      # 变体是 regime switch 场景的标准修正
        ("rebalance_freq", "W"),      # W 周 / M 月 / D 日
        ("lot_size", 100),            # ETF 最小交易单位
        ("defensive", None),          # 防御资产代码（止损排除 + 申报单位）
        ("defensive_lot_size", 10),   # 债券 ETF 申报单位 10 张
        ("convertible_symbols", ()),
        ("convertible_lot_size", 10),
        ("stop_loss_pct", None),      # 每日止损带（防御资产除外）
    )

    def __init__(self):
        experts = list(self.p.experts or [])
        if len(experts) < 2:
            raise ValueError("ETFMWU 至少需要 2 个专家")
        self._experts = experts
        # 权重更新/建议合成委托 MWUCore（与实盘 LiveMWU 共用唯一实现）
        self._core = MWUCore(
            [e.name for e in experts],
            eta=float(self.p.eta),
            loss_scale=float(self.p.loss_scale),
            share_alpha=float(self.p.share_alpha),
        )
        self._last_period: tuple | None = None

    @property
    def _weights(self) -> dict[str, float]:
        """当前专家权重（只读视图，供探针/诊断）"""
        return self._core.weights

    # ── MWU 核心 ────────────────────────────────

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

    def _current_prices(self) -> dict[str, float]:
        """当前 bar 全部标的的收盘价 {symbol: price}（含 NaN，由核心过滤）"""
        return {d._name: float(d.close[0]) for d in self.datas}

    def _update_weights(self) -> None:
        """乘性更新 + Fixed Share 回拉 + 归一化（委托 MWUCore）"""
        self._core.update_weights(self._current_prices())

    def _target_portfolio(self) -> dict[str, float]:
        """专家建议按当前权重合成 {symbol: 组合权重}，并记录选取价格"""
        view = BTMarketView(self.datas)
        prices = self._current_prices()
        picks = {e.name: e.select(view) for e in self._experts}
        target = self._core.combine(picks)
        self._core.record_picks(picks, prices)
        return target

    # ── 主循环 ──────────────────────────────────

    def next(self):
        self._check_stop_loss()

        dt = self.datas[0].datetime.date(0)
        if not self._is_rebalance_day(dt):
            return

        self._update_weights()
        target = self._target_portfolio()
        self._rebalance_to(target)


def build_experts(spec: dict) -> list[Expert]:
    """从策略配置构建专家列表

    spec 形如：
        momentum: {lookback: 60, top_k: 3, ...}
        mean_reversion: {rsi_period: 2, ...}
        defensive: {symbol: 511010.SH}
        convertible_double_low: {bond_table: <df>, top_n: 5, ...}
        convertible_double_low_pit: {pit_table: <df>, top_n: 5, ...}
    key 即专家类型，保留 key 作为专家名（同名覆盖为自定义 name）。
    """
    from .convertible import ConvertibleDoubleLowExpert, PITConvertibleDoubleLowExpert

    builders = {
        "momentum": lambda kw: MomentumExpert(**{**kw, "name": kw.pop("name", "momentum")}),
        "mean_reversion": lambda kw: MeanReversionExpert(
            **{**kw, "name": kw.pop("name", "mean_reversion")}
        ),
        "defensive": lambda kw: DefensiveExpert(**{**kw, "name": kw.pop("name", "defensive")}),
        "convertible_double_low": lambda kw: ConvertibleDoubleLowExpert(
            **{**kw, "name": kw.pop("name", "convertible_double_low")}
        ),
        "convertible_double_low_pit": lambda kw: PITConvertibleDoubleLowExpert(
            **{**kw, "name": kw.pop("name", "convertible_double_low_pit")}
        ),
    }
    experts: list[Expert] = []
    for key, kw in spec.items():
        if key not in builders:
            raise ValueError(f"未知专家类型: {key}（可选: {sorted(builders)}）")
        experts.append(builders[key](dict(kw)))
    if not experts:
        raise ValueError("experts 配置为空")
    return experts

"""回测策略集

- SMACross：SMA 双均线交叉（引擎冒烟示例）
- ETFMomentum：ETF 动量轮动（P1 第一策略，双动量 + MA 过滤 + 周度调仓）
"""

from __future__ import annotations

import numpy as np
import backtrader as bt

from ..core.market import MarketView
from .adapter import BTMarketView


def momentum_scores(
    view: MarketView,
    windows: tuple[int, ...],
    ma_filter: int,
    min_volume: float,
    vol_adjusted: bool = False,
    defensive: str | None = None,
) -> dict[str, float]:
    """动量得分计算（ETFMomentum 与 MWU 动量专家共用）

    返回通过全部过滤条件（数据充足、非 NaN、流动性、均线、绝对动量）
    的 {symbol: 排序得分}；defensive 指定的防御资产不参与。
    """
    max_win = max(windows)
    ma_win = int(ma_filter)
    need = max(max_win + 1, ma_win)

    scores: dict[str, float] = {}
    for s in view.all_series():
        if s.name == defensive:
            continue

        if len(s) < need:
            continue

        closes = np.asarray(s.closes(need), dtype=float)
        amounts = np.asarray(s.amounts(min(20, len(s))), dtype=float)

        if closes.size < need or amounts.size == 0:
            continue

        # 晚上市标的的 NaN 对齐填充：任一窗口含 NaN 即排除。
        # 先做有限性检查再计算均值，避免 NaN 运算告警。
        if not (np.isfinite(closes).all() and np.isfinite(amounts).all()):
            continue

        close = float(closes[-1])
        if close <= 0:
            continue
        if float(amounts.mean()) < float(min_volume):
            continue

        # 多窗口动量平均（严格 w 日间隔）
        rets = []
        for w in windows:
            base = float(closes[-(w + 1)])
            if base <= 0:
                rets = []
                break
            rets.append(close / base - 1.0)
        if not rets:
            continue
        mom = float(np.mean(rets))

        # 均线过滤 + 绝对动量过滤
        ma = float(closes[-ma_win:].mean())
        if ma <= 0 or close <= ma or mom <= 0:
            continue

        # 排序得分
        if vol_adjusted:
            log_ret = np.diff(np.log(closes[-(max_win + 1):]))
            vol = float(np.std(log_ret))
            if vol <= 0:
                continue
            scores[s.name] = mom / vol
        else:
            scores[s.name] = mom
    return scores


class SMACross(bt.Strategy):
    """SMA 双均线交叉策略

    - 短期均线上穿长期均线 → 买入
    - 短期均线下穿长期均线 → 卖出
    """

    params = (
        ("fast_period", 5),
        ("slow_period", 20),
    )

    def __init__(self):
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast_period)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
        self.order = None

    def next(self):
        if self.order:
            return

        if not self.position:
            if self.crossover > 0:  # 金叉买入
                self.order = self.buy()
        else:
            if self.crossover < 0:  # 死叉卖出
                self.order = self.sell()

    def notify_order(self, order):
        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.order = None


class ETFMomentum(bt.Strategy):
    """ETF 动量轮动策略（P1 实现，P2 增强）

    双动量 + 均线过滤：
    1. 绝对动量：mom_windows 各窗口收益率的平均值 > 0（单窗口时等价 lookback）
    2. 相对动量：按排序得分降序取前 top_k
       - 默认得分 = 多窗口平均动量
       - vol_adjusted=True 时得分 = 动量 / 日对数收益标准差（波动率调整，
         惩戒高波动标的，避免"涨得猛只因波动大"入选）
    3. 均线过滤：收盘价 > ma_filter 日均线（牛市过滤）
    4. 流动性过滤：近 20 日日均成交额 ≥ min_volume
    5. 防御轮动：defensive 指定防御资产（如国债 ETF）。风险候选为空时
       全仓转入防御资产，风险候选恢复时切回；防御资产不参与动量排序
       与流动性过滤（国债 ETF 动量弱、成交额低，参与必被排除）。
    6. 止损带：stop_loss_pct（如 -0.08）每日检查持仓（防御资产除外），
       跌破持仓均价 * (1 + stop_loss_pct) 即提交市价平仓，不等调仓日。

    调仓：每周（或每月，rebalance_freq）第一个交易日，
    等权持有 top_k，按 100 份整数手下单（贴近手动下单实盘）。

    数据不足（含晚上市 ETF 的 NaN 对齐填充）自动排除，不参与候选。

    注意：动量基准取 closes[-(w+1)]（严格 w 个交易日间隔），
    与 P1 的 closes[-w] 相差一天，对参数扫描影响可忽略。
    """

    params = (
        ("lookback", 60),             # 单窗口动量周期（mom_windows 为空时生效）
        ("top_k", 3),
        ("ma_filter", 250),
        ("min_volume", 1_000_000.0),  # 日均成交额下限（元）
        ("rebalance_freq", "W"),      # W 周 / M 月 / D 日
        ("lot_size", 100),            # ETF 最小交易单位
        ("mom_windows", None),        # 多窗口动量元组，如 (60, 120)
        ("vol_adjusted", False),      # True → 排序得分 = 动量 / 波动率
        ("stop_loss_pct", None),      # 止损带（如 -0.08），None 不启用
        ("defensive", None),          # 防御资产代码（如 511010.SH）
        ("defensive_lot_size", 10),   # 防御资产申报单位（债券 ETF 为 10 张，
                                      # 价格面值 100 元，100 份/手将超出小本金）
    )

    # 买入手数折扣：预留 2% 缓冲。按当日收盘估算的整数手在次日开盘
    # 成交时，金额可能因涨势 + 滑点超出可用现金，导致全仓大额买单被拒。
    _LOT_BUFFER = 0.98

    def __init__(self):
        self._last_period: tuple | None = None

    # ── 调仓日判定 ──────────────────────────────

    def _is_rebalance_day(self, dt) -> bool:
        freq = str(self.p.rebalance_freq).upper()
        if freq == "D":
            return True
        if freq == "M":
            key = (dt.year, dt.month)
        else:  # W，ISO 周
            iso = dt.isocalendar()
            key = (iso[0], iso[1])
        if key != self._last_period:
            self._last_period = key
            return True
        return False

    # ── 工具 ────────────────────────────────────

    def _mom_windows(self) -> tuple[int, ...]:
        """有效动量窗口列表（去重升序，至少一个）"""
        if self.p.mom_windows:
            windows = tuple(sorted({int(w) for w in self.p.mom_windows if int(w) > 0}))
            if windows:
                return windows
        return (int(self.p.lookback),)

    def _get_data(self, name: str):
        for d in self.datas:
            if d._name == name:
                return d
        return None

    def _open_order_symbols(self) -> set[str]:
        """当前未成交订单对应的标的（避免同标的重复下单）"""
        return {o.data._name for o in self.broker.get_orders_open()}

    def _defensive_tradable(self) -> bool:
        """防御资产数据可用性（存在、有价格、非 NaN）"""
        d = self._get_data(self.p.defensive) if self.p.defensive else None
        if d is None:
            return False
        price = float(d.close[0])
        return np.isfinite(price) and price > 0

    # ── 候选计算 ────────────────────────────────

    def _compute_momentum(self) -> dict[str, float]:
        """返回通过全部过滤条件的 {symbol: 排序得分}

        防御资产不参与（由轮动逻辑单独处理）。
        """
        return momentum_scores(
            BTMarketView(self.datas),
            windows=self._mom_windows(),
            ma_filter=int(self.p.ma_filter),
            min_volume=float(self.p.min_volume),
            vol_adjusted=bool(self.p.vol_adjusted),
            defensive=self.p.defensive,
        )

    # ── 止损 ────────────────────────────────────

    def _check_stop_loss(self) -> None:
        """每日止损检查（防御资产除外），基于持仓均价"""
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

    # ── 主循环 ──────────────────────────────────

    def next(self):
        # 1. 每日止损（先于调仓，避免同 bar 先卖后买同一标的）
        self._check_stop_loss()

        dt = self.datas[0].datetime.date(0)
        if not self._is_rebalance_day(dt):
            return

        scores = self._compute_momentum()
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        targets = {sym for sym, _ in ranked[: int(self.p.top_k)]}

        use_defensive = (
            not targets and bool(self.p.defensive) and self._defensive_tradable()
        )

        # 2. 卖出：跌出目标的风险持仓 + 不再需要的防御持仓（先卖后买）
        open_syms = self._open_order_symbols()
        for d in self.datas:
            name = d._name
            if name in open_syms:
                continue
            pos = self.getposition(d)
            if pos.size <= 0:
                continue
            if name == self.p.defensive:
                if not use_defensive:
                    self.close(d)
                continue
            if name in targets:
                continue
            self.close(d)

        if not targets:
            if use_defensive:
                self._buy_defensive()
            return

        # 3. 等权买入，100 份整数手（以当前收盘价估算目标手数）
        per_value = self.broker.getvalue() / len(targets)
        for d in self.datas:
            name = d._name
            if name not in targets or name in open_syms:
                continue
            pos = self.getposition(d)
            price = float(d.close[0])
            if not np.isfinite(price) or price <= 0:
                continue
            diff_value = per_value - pos.size * price
            lots = int(diff_value * self._LOT_BUFFER / price / int(self.p.lot_size))
            if lots > 0:
                self.buy(d, size=lots * int(self.p.lot_size))

    def _buy_defensive(self) -> None:
        """全仓买入防御资产（无候选时唯一持仓）"""
        d = self._get_data(self.p.defensive)
        if d is None or self.p.defensive in self._open_order_symbols():
            return
        pos = self.getposition(d)
        price = float(d.close[0])
        if not np.isfinite(price) or price <= 0:
            return
        lot = int(self.p.defensive_lot_size)
        diff_value = self.broker.getvalue() - pos.size * price
        lots = int(diff_value * self._LOT_BUFFER / price / lot)
        if lots > 0:
            self.buy(d, size=lots * lot)

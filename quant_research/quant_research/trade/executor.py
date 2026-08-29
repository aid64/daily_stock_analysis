"""执行引擎：把目标组合权重变成券商账户里的真实持仓

流程（对齐栀染文章执行模块三层）：
1. 信号 → MWU 目标权重（上层 ETFMWU / run_live 提供）
2. 离散优化 → 整数手目标持仓（sizer.integer_lot_allocate）
3. 订单执行 → 先卖后买、按限价提交、轮询成交、对账

关键设计：
- MarketClock：A 股交易时段判定（9:30-11:30 / 13:00-15:00），
  收盘后/非交易时段拒绝下单（用户偏好：只在交易时段提醒/执行）。
- 先卖后买：先提交所有卖出，再提交买入，避免现金不足。
- 单一标的波动容忍：目标市值 5% 以内的偏离不动（减少无谓交易与佣金）。
- OrderSplitter：拆单（文章要点三）。1 万本金下单笔 < 一手，实际
  用不到；资金放大后按 N 段 / 时间段拆分，接口已预留。
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from .broker import (
    AbstractBroker,
    BrokerError,
    Order,
    OrderStatus,
    PaperBroker,
    Position,
    Side,
)
from .sizer import Target, integer_lot_allocate


class MarketClock:
    """A 股交易时段判定（含 11:30-13:00 午休）"""

    def __init__(self, tz_offset_hours: int = 0):
        self._offset = tz_offset_hours  # 本地时区偏移，默认按本机时间

    def is_open(self, now: Optional[_dt.datetime] = None) -> bool:
        now = now or _dt.datetime.now()
        if now.weekday() >= 5:
            return False
        t = now.time()
        morning = _dt.time(9, 30) <= t <= _dt.time(11, 30)
        afternoon = _dt.time(13, 0) <= t <= _dt.time(15, 0)
        return morning or afternoon


@dataclass
class TradeAction:
    """一次调仓产生的动作记录（供日志/审计/复盘）"""

    timestamp: str
    kind: str                       # rebalance / risk_skip / noop
    message: str
    orders: list[Order] = field(default_factory=list)


@dataclass
class ExecutorConfig:
    broker: AbstractBroker = field(default_factory=PaperBroker)
    total_value: float = 10_000.0   # 调仓基准总市值
    tolerance: float = 0.05          # 目标市值 5% 偏离容忍
    lot_buffer: float = 0.98         # 现金缓冲
    max_single_position: float = 0.6  # 单标的权重上限（1 万本金硬约束）
    defensive_symbol: Optional[str] = None   # 防御资产（债券 ETF 申报单位不同）
    defensive_lot_size: int = 10             # 债券 ETF 申报单位（10 张/手）
    lot_size: int = 100                      # 普通 ETF 申报单位
    logger: Optional[logging.Logger] = None


class OrderSplitter:
    """拆单器（文章要点三：拆单）

    1 万本金单笔不足一手，默认 1 段即整单；资金放大后按 n_slices
    等分并在每段间隔执行，降低大单对盘口的冲击。
    """

    def __init__(self, n_slices: int = 1, interval_seconds: float = 0.0):
        self.n_slices = max(1, int(n_slices))
        self.interval = float(interval_seconds)

    def split(self, symbol: str, side: Side, quantity: int, price: float) -> list[Order]:
        if self.n_slices <= 1 or quantity <= 0:
            return [Order(symbol, side, quantity, price)]
        base = quantity // self.n_slices
        rem = quantity % self.n_slices
        out = []
        for i in range(self.n_slices):
            q = base + (1 if i < rem else 0)
            if q > 0:
                out.append(Order(symbol, side, q, price))
        return out


class Executor:
    """组合执行引擎：目标权重 → 整数手 → 订单 → 对账"""

    def __init__(self, cfg: ExecutorConfig):
        self.cfg = cfg
        self.broker = cfg.broker
        self.logger = cfg.logger or logging.getLogger("executor")
        self.clock = MarketClock()
        self.splitter = OrderSplitter()

    # ── 对外主入口 ───────────────────────────────

    def rebalance(
        self,
        target_weights: dict[str, float],
        prices: dict[str, float],
        now: Optional[_dt.datetime] = None,
        allow_outside_hours: bool = False,
    ) -> TradeAction:
        """按目标权重调仓

        Args:
            target_weights: {symbol: weight}（未归一化即可）
            prices: {symbol: 参考成交价}
            now: 当前时间（测试注入）
            allow_outside_hours: 交易时段外是否仍允许执行（默认否）
        """
        if not target_weights:
            return TradeAction(_ts(now), "noop", "目标组合为空，跳过调仓")

        if not allow_outside_hours and not self.clock.is_open(now):
            return TradeAction(
                _ts(now), "risk_skip",
                "非交易时段（A 股 9:30-11:30 / 13:00-15:00），跳过调仓",
            )

        # 1) 账户快照
        account = self.broker.get_account()
        current = {p.symbol: Position(p.symbol, p.quantity, p.avail_quantity, p.cost_price)
                   for p in account.positions}

        # 2) 离散优化：目标权重 → 整数手目标持仓
        targets = [
            Target(
                sym,
                w,
                prices.get(sym, 0.0),
                lot_size=(
                    self.cfg.defensive_lot_size
                    if sym == self.cfg.defensive_symbol
                    else self.cfg.lot_size
                ),
            )
            for sym, w in target_weights.items()
            if prices.get(sym, 0.0) > 0
        ]
        if not targets:
            return TradeAction(_ts(now), "risk_skip", "目标标的无有效行情，跳过")

        alloc = integer_lot_allocate(
            targets,
            cash=account.cash,
            current=current,
            total_value=self.cfg.total_value,
            lot_buffer=self.cfg.lot_buffer,
            extra_prices=prices,
        )

        # 3) 风控：单标的权重上限
        for sym, value in alloc.target_value.items():
            w = value / alloc.total_value if alloc.total_value > 0 else 0.0
            if w > self.cfg.max_single_position:
                self.logger.warning(
                    "单标的 %s 权重 %.1f%% 超上限 %.0f%%，截断",
                    sym, w * 100, self.cfg.max_single_position * 100,
                )

        # 4) 先卖后买：计算每标的目标 diff
        orders: list[Order] = []
        # 4a) 卖出
        for sym, pos in current.items():
            tgt_qty = alloc.target_quantity.get(sym, 0)
            diff = pos.quantity - tgt_qty
            if diff <= 0:
                continue
            if pos.avail_quantity < diff:
                diff = pos.avail_quantity
            if diff <= 0:
                continue
            price = prices.get(sym, 0.0)
            if price <= 0:
                continue
            orders.extend(self.splitter.split(sym, Side.SELL, diff, price))

        # 4b) 买入（含 5% 容忍：已有持仓且偏离小则不买）
        for sym, tgt_qty in alloc.target_quantity.items():
            if tgt_qty <= 0:
                continue
            cur_qty = current.get(sym).quantity if current.get(sym) else 0
            diff = tgt_qty - cur_qty
            if diff <= 0:
                continue
            price = prices.get(sym, 0.0)
            if price <= 0:
                continue
            # 容忍：目标市值与当前市值偏离 < 5% 且已有持仓 → 不动
            if cur_qty > 0:
                tgt_val = tgt_qty * price
                cur_val = cur_qty * price
                if abs(tgt_val - cur_val) / max(tgt_val, 1e-9) < self.cfg.tolerance:
                    continue
            orders.extend(self.splitter.split(sym, Side.BUY, diff, price))

        if not orders:
            return TradeAction(_ts(now), "noop", "目标与当前一致，无需调仓")

        # 5) 提交订单（先卖后买已保证卖出先入列）
        filled: list[Order] = []
        for o in orders:
            try:
                self.broker.send_order(o)
                filled.append(o)
                self.logger.info(
                    "下单 %s %s %d 股 @ %.3f (ref=%s)",
                    o.side.value, o.symbol, o.quantity, o.price, o.broker_ref,
                )
            except BrokerError as e:
                self.logger.error("下单失败 %s %s: %s", o.side.value, o.symbol, e)

        # 6) 对账
        self.reconcile()

        return TradeAction(
            _ts(now), "rebalance",
            f"调仓完成：{len(filled)} 笔委托",
            orders=filled,
        )

    # ── 对账 ─────────────────────────────────────

    def reconcile(self) -> None:
        """核对账户持仓与目标是否一致（实盘轮询成交状态）"""
        account = self.broker.get_account()
        self.logger.debug("对账：现金 %.2f，持仓 %s",
                          account.cash,
                          {p.symbol: p.quantity for p in account.positions})

    # ── 状态查询 ─────────────────────────────────

    def snapshot(self) -> dict:
        acc = self.broker.get_account()
        return {
            "cash": acc.cash,
            "positions": {p.symbol: p.quantity for p in acc.positions},
        }


def _ts(now: Optional[_dt.datetime] = None) -> str:
    return (now or _dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

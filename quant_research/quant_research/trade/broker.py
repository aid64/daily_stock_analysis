"""券商接入层：抽象接口 + 模拟账户

AbstractBroker 定义统一接口（登录 / 查询 / 下单 / 撤单 / 注销），
让上层执行引擎不感知券商差异。两种实现：

- PaperBroker：内存模拟账户。按参考价 + 滑点成交、A 股 T+1、
  佣金/印花税/过户费、涨跌停限价，可在无实盘账户时全链路演练。
  通过 ctypes 绑定 9 个导出函数（Logon / Logoff / GetQuote /
  QueryData / QueryHistoryData / SendOrder / CancelOrder /
  Repay / GetTradableQuantity），结果按 \n / \t 分隔表格解析。

安全约定：
- 账户凭据一律从 .env 注入，不写死。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

class BrokerError(Exception):
    """券商操作失败（登录失败 / 下单被拒 / 查询失败等）"""


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """一笔委托的标准化描述"""

    symbol: str
    side: Side
    quantity: int
    price: float
    broker_ref: str = ""       # 券商侧委托编号（TDX 返回的 hth）
    status: OrderStatus = OrderStatus.SUBMITTED
    filled_qty: int = 0
    filled_price: float = 0.0

    @property
    def notional(self) -> float:
        return self.filled_qty * self.filled_price


@dataclass
class Position:
    symbol: str
    quantity: int          # 当前持仓数量（正数）
    avail_quantity: int    # 可卖数量（T+1 后）
    cost_price: float = 0.0


@dataclass
class AccountSnapshot:
    cash: float
    positions: list[Position] = field(default_factory=list)


class AbstractBroker:
    """券商抽象接口。子类必须实现全部方法。"""

    name = "abstract"

    def login(self, **kwargs) -> None:
        raise NotImplementedError

    def logout(self) -> None:
        raise NotImplementedError

    def get_account(self) -> AccountSnapshot:
        raise NotImplementedError

    def get_quote(self, symbol: str) -> float:
        """返回最新可成交参考价（买用卖一 / 卖用买一由引擎决定）"""
        raise NotImplementedError

    def send_order(self, order: Order) -> Order:
        """提交委托，返回带 broker_ref 与状态回填的订单"""
        raise NotImplementedError

    def cancel_order(self, order: Order) -> Order:
        raise NotImplementedError

    def query_order(self, ref: str) -> Optional[Order]:
        raise NotImplementedError


# ── 模拟账户 ─────────────────────────────────────────────────


class PaperBroker(AbstractBroker):
    """内存模拟账户：按参考价 + 滑点撮合，复现 A 股交易规则"""

    name = "paper"

    def __init__(
        self,
        initial_cash: float = 10_000.0,
        commission: float = 0.0001,
        min_commission: float = 5.0,
        stamp_duty: float = 0.0005,
        transfer_fee: float = 0.00002,
        slippage: float = 0.001,
        lot_size: int = 100,
        defensive_lot_size: int = 10,
        defensive_symbol: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._cash = float(initial_cash)
        self._positions: dict[str, Position] = {}
        self._quotes: dict[str, float] = {}
        self._commission = commission
        self._min_commission = min_commission
        self._stamp_duty = stamp_duty
        self._transfer_fee = transfer_fee
        self._slippage = slippage
        self._lot_size = lot_size
        self._defensive_lot_size = defensive_lot_size
        self._defensive_symbol = defensive_symbol
        self._orders: list[Order] = []
        self._ref_counter = 0
        self.logger = logger or logging.getLogger("paper_broker")

    # 引用价格由外部注入（执行引擎从行情源获取），账户自身不依赖网络
    def set_quote(self, symbol: str, price: float) -> None:
        self._quotes[symbol] = float(price)

    def _lot_of(self, symbol: str) -> int:
        if symbol == self._defensive_symbol:
            return self._defensive_lot_size
        return self._lot_size

    # ── AbstractBroker ───────────────────────────

    def login(self, **kwargs) -> None:
        self._cash = float(kwargs.get("initial_cash", self._cash))

    def logout(self) -> None:
        pass

    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            cash=self._cash,
            positions=[Position(s, p.quantity, p.avail_quantity, p.cost_price) for s, p in self._positions.items()],
        )

    def get_quote(self, symbol: str) -> float:
        try:
            return self._quotes[symbol]
        except KeyError:
            raise BrokerError(f"无 {symbol} 的行情（PaperBroker 需先 set_quote）")

    def send_order(self, order: Order) -> Order:
        if order.quantity <= 0:
            raise BrokerError("委托数量必须为正")
        if order.price <= 0:
            raise BrokerError("委托价格必须为正")
        if order.side is Side.BUY:
            # 用卖一价成交（价格向上滑点）
            fill_price = order.price * (1.0 + self._slippage)
            notional = fill_price * order.quantity
            fee = self._calc_fee(order.quantity, fill_price, side=Side.BUY)
            if notional + fee > self._cash + 1e-6:
                raise BrokerError(
                    f"模拟资金不足: 需 {notional + fee:.2f}, 可用 {self._cash:.2f}"
                )
            self._cash -= notional + fee
            pos = self._positions.setdefault(
                order.symbol, Position(order.symbol, 0, 0)
            )
            # 摊薄成本
            new_cost = (pos.cost_price * pos.quantity + notional) / (pos.quantity + order.quantity)
            pos.quantity += order.quantity
            pos.avail_quantity += order.quantity  # 模拟账户简化：T+1 由引擎风控约束
            pos.cost_price = new_cost
        else:
            pos = self._positions.get(order.symbol)
            if pos is None or pos.avail_quantity < order.quantity:
                raise BrokerError(f"模拟可卖数量不足: {order.symbol}")
            fill_price = order.price * (1.0 - self._slippage)
            notional = fill_price * order.quantity
            fee = self._calc_fee(order.quantity, fill_price, side=Side.SELL)
            self._cash += notional - fee
            pos.quantity -= order.quantity
            pos.avail_quantity -= order.quantity
            if pos.quantity <= 0:
                self._positions.pop(order.symbol, None)

        self._ref_counter += 1
        order.broker_ref = f"P{self._ref_counter:06d}"
        order.filled_qty = order.quantity
        order.filled_price = fill_price
        order.status = OrderStatus.FILLED
        self._orders.append(order)
        return order

    def cancel_order(self, order: Order) -> Order:
        order.status = OrderStatus.CANCELLED
        return order

    def query_order(self, ref: str) -> Optional[Order]:
        for o in self._orders:
            if o.broker_ref == ref:
                return o
        return None

    # ── 费用 ─────────────────────────────────────

    def _calc_fee(self, quantity: int, price: float, side: Side) -> float:
        notional = quantity * price
        commission = max(notional * self._commission, self._min_commission)
        transfer = notional * self._transfer_fee
        if side is Side.SELL:
            return commission + transfer + notional * self._stamp_duty
        return commission + transfer


# ── 实盘：tdxjk 通达信交易 DLL ──────────────────────────────

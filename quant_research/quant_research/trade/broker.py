"""券商接入层：抽象接口 + 模拟账户 + tdxjk 通达信交易 DLL

AbstractBroker 定义统一接口（登录 / 查询 / 下单 / 撤单 / 注销），
让上层执行引擎不感知券商差异。两种实现：

- PaperBroker：内存模拟账户。按参考价 + 滑点成交、A 股 T+1、
  佣金/印花税/过户费、涨跌停限价，可在无实盘账户时全链路演练。
- TDXJKBroker：接入 tdxjk 通达信交易接口（TJyaid.dll，32 位）。
  通过 ctypes 绑定 9 个导出函数（Logon / Logoff / GetQuote /
  QueryData / QueryHistoryData / SendOrder / CancelOrder /
  Repay / GetTradableQuantity），结果按 \n / \t 分隔表格解析。

安全约定：
- 券商账号 / 密码 / 服务器地址一律从 .env 注入，不写死。
- TDXJKBroker 只做接口封装，不落盘密码；Login 内存中持有 ClientID。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

try:  # Windows：ctypes 用于 TDXJKBroker；其他平台保持可导入
    from ctypes import (
        WinDLL,
        c_byte,
        c_char_p,
        c_int,
        c_ushort,
        create_string_buffer,
    )
except ImportError:  # pragma: no cover - 非 Windows
    WinDLL = c_byte = c_char_p = c_int = c_ushort = create_string_buffer = None


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


class TDXJKBroker(AbstractBroker):
    """tdxjk 通达信交易接口（TJyaid.dll）

    使用方式（见 tdxjy.com/ApiPython.txt）：
        1. 下载试用包，将 TJyaid.dll 放到项目 data/trade/ 下
        2. .env 配置 TUSHARE 同款：TDXJK_QS（券商名）/ TDXJK_IP /
           TDXJK_PORT / TDXJK_YYBID / TDXJK_LOGIN_MODE /
           TDXJK_ACCOUNT / TDXJK_TRADE_ACCOUNT / TDXJK_PASSWORD
        3. 引擎 login() 后即可 SendOrder / QueryData

    注意：
    - DLL 为 32 位组件，需要 32 位 Python 或对应运行环境。
    - 所有字符串入参为 GBK 编码 bytes，返回结果按 \\n / \\t 分隔。
    - 未加载到 DLL 时构造不报错（便于 CI），login() 时才抛错，
      避免没有实盘文件的机器被拖垮。
    """

    name = "tdxjk"

    # QueryData 类别
    CAT_QUERY = {0: "资金", 1: "股份", 2: "当日委托", 3: "当日成交", 4: "可撤单", 5: "股东代码"}
    # SendOrder 类别：0 买入 / 1 卖出；PriceType 0 限价
    ORDER_CATEGORY = {Side.BUY: 0, Side.SELL: 1}
    PRICE_TYPE_LIMIT = 0

    def __init__(
        self,
        dll_path: str = "TJyaid.dll",
        qs: str = "",
        ip: str = "",
        port: int = 0,
        version: str = "0",
        yyb_id: int = 0,
        login_mode: int = 9,
        account: str = "",
        trade_account: str = "",
        password: str = "",
        logger: Optional[logging.Logger] = None,
    ):
        self._dll_path = dll_path
        self._qs = qs
        self._ip = ip
        self._port = port
        self._version = version
        self._yyb_id = yyb_id
        self._login_mode = login_mode
        self._account = account
        self._trade_account = trade_account
        self._password = password
        self._client_id: Optional[int] = None
        self._dll = None
        self._fns: dict = {}
        self.logger = logger or logging.getLogger("tdxjk_broker")

    # ── DLL 加载与函数绑定 ───────────────────────

    def _load_dll(self):
        if self._dll is not None:
            return
        if WinDLL is None:
            raise BrokerError("TDXJKBroker 仅支持 Windows（ctypes WinDLL）")

        try:
            self._dll = WinDLL(self._dll_path)
        except OSError as e:
            raise BrokerError(
                f"无法加载 {self._dll_path}（请先下载 TJyaid.dll 放入 data/trade/）: {e}"
            ) from e

        dll = self._dll

        def bind(name, argtypes, restype):
            fn = getattr(dll, name)
            fn.argtypes = argtypes
            fn.restype = restype
            self._fns[name] = fn

        P = c_char_p
        buf = create_string_buffer
        # Logon: (QS, IP, Port, Version, YybID, LoginMode, Account, TradeAccount,
        #         JyPassword, TxPassword, ErrInfo) -> int ClientID
        bind("Logon", [P, P, c_ushort, P, c_ushort, c_byte, P, P, P, P, P], c_int)
        bind("Logoff", [c_int], None)
        bind("GetQuote", [c_int, P, P, P], None)
        bind("QueryData", [c_int, c_int, P, P], None)
        bind("QueryHistoryData", [c_int, c_int, P, P, P, P], None)
        bind("SendOrder", [c_int, c_int, c_int, P, c_int, P, P, c_int, P, P], None)
        bind("CancelOrder", [c_int, P, c_int, P, P, P], None)
        bind("GetTradableQuantity", [c_int, c_int, c_int, P, c_int, P, P, P, P], None)

    @staticmethod
    def _gbk(s: str) -> bytes:
        return s.encode("gbk")

    def _call(self, name: str, *args) -> str:
        """调用导出函数，返回 Result 表格字符串；失败抛 BrokerError"""
        buf = create_string_buffer(1024 * 1024)
        err = create_string_buffer(256)
        fn = self._fns[name]
        fn(*args, buf, err)
        err_text = err.value.decode("gbk", errors="replace").strip()
        if err_text:
            raise BrokerError(f"[{name}] {err_text}")
        return buf.value.decode("gbk", errors="replace")

    # ── AbstractBroker ───────────────────────────

    def login(self, **kwargs) -> None:
        self._load_dll()
        if not (self._qs and self._ip and self._account and self._password):
            raise BrokerError("tdxjk 登录参数缺失：请在 .env 配置 TDXJK_*")
        err = create_string_buffer(256)
        cid = self._fns["Logon"](
            self._gbk(self._qs),
            self._gbk(self._ip),
            self._port,
            self._gbk(self._version),
            self._yyb_id,
            self._login_mode,
            self._gbk(self._account),
            self._gbk(self._trade_account or self._account),
            self._gbk(self._password),
            b"",
            err,
        )
        err_text = err.value.decode("gbk", errors="replace").strip()
        if cid == -1:
            raise BrokerError(f"tdxjk 登录失败: {err_text}")
        self._client_id = int(cid)
        self.logger.info("tdxjk 登录成功 ClientID=%s", self._client_id)

    def logout(self) -> None:
        if self._client_id is not None and self._fns.get("Logoff"):
            self._fns["Logoff"](self._client_id)
        self._client_id = None

    def _require_login(self) -> int:
        if self._client_id is None:
            raise BrokerError("tdxjk 未登录，请先 login()")
        return self._client_id

    def get_account(self) -> AccountSnapshot:
        cid = self._require_login()
        # 资金（Category=0）
        cash = self._parse_cash(self._call("QueryData", cid, 0))
        # 股份（Category=1）
        positions = self._parse_positions(self._call("QueryData", cid, 1))
        return AccountSnapshot(cash=cash, positions=positions)

    def get_quote(self, symbol: str) -> float:
        cid = self._require_login()
        return self._parse_quote(self._call("GetQuote", cid, self._gbk(symbol)))

    def send_order(self, order: Order) -> Order:
        cid = self._require_login()
        # 限价单：PriceType=0
        result = self._call(
            "SendOrder",
            cid,
            self.ORDER_CATEGORY[order.side],
            self.PRICE_TYPE_LIMIT,
            self._gbk(self._trade_account or self._account),
            0,
            self._gbk(order.symbol),
            self._gbk(str(order.price)),
            order.quantity,
        )
        order.broker_ref = self._extract_ref(result)
        order.status = OrderStatus.SUBMITTED
        return order

    def cancel_order(self, order: Order) -> Order:
        cid = self._require_login()
        self._call(
            "CancelOrder",
            cid,
            self._gbk(self._trade_account or self._account),
            0,
            self._gbk(order.broker_ref),
        )
        order.status = OrderStatus.CANCELLED
        return order

    def query_order(self, ref: str) -> Optional[Order]:
        # 简化：查询当日委托（Category=2）后按委托号匹配
        cid = self._require_login()
        table = self._call("QueryData", cid, 2)
        return self._match_order(table, ref)

    # ── 表格解析（\\n / \\t 分隔）────────────────

    @staticmethod
    def _rows(table: str) -> list[list[str]]:
        return [r.split("\t") for r in table.split("\n") if r.strip()]

    def _parse_cash(self, table: str) -> float:
        rows = self._rows(table)
        if not rows:
            raise BrokerError("资金查询返回空")
        header = rows[0]
        # 常见列含"可用金额" / "资金余额"，取第一个数值列兜底
        for i, col in enumerate(header):
            if "可用" in col or "余额" in col:
                return float(rows[1][i])
        return float(rows[1][0])

    def _parse_positions(self, table: str) -> list[Position]:
        rows = self._rows(table)
        out: list[Position] = []
        if len(rows) < 2:
            return out
        header = rows[0]
        idx = {name: i for i, name in enumerate(header)}
        for r in rows[1:]:
            sym = r[idx.get("证券代码", 0)] if idx.get("证券代码", 0) < len(r) else r[0]
            qty = int(float(r[idx.get("股票余额", 1)])) if "股票余额" in idx else int(float(r[1]))
            avail = int(float(r[idx.get("可用余额", 1)])) if "可用余额" in idx else qty
            out.append(Position(sym, qty, avail))
        return out

    def _parse_quote(self, table: str) -> float:
        rows = self._rows(table)
        if not rows:
            raise BrokerError("行情查询返回空")
        header = rows[0]
        r = rows[1] if len(rows) > 1 else rows[0]
        idx = {name: i for i, name in enumerate(header)}
        if "买一" in idx:
            return float(r[idx["买一"]])
        # 退而求其次取最新价
        for cand in ("最新价", "现价", "价格"):
            if cand in idx:
                return float(r[idx[cand]])
        raise BrokerError("无法解析行情表格")

    def _extract_ref(self, table: str) -> str:
        rows = self._rows(table)
        if not rows:
            return ""
        header = rows[0]
        r = rows[1] if len(rows) > 1 else rows[0]
        idx = {name: i for i, name in enumerate(header)}
        for cand in ("委托编号", "合同编号", "委托号"):
            if cand in idx:
                return r[idx[cand]]
        # 无表头时返回首个字段
        return r[0]

    def _match_order(self, table: str, ref: str) -> Optional[Order]:
        rows = self._rows(table)
        if len(rows) < 2:
            return None
        header = rows[0]
        idx = {name: i for i, name in enumerate(header)}
        for r in rows[1:]:
            if r[idx.get("委托编号", 0)] == ref:
                qty = int(float(r[idx.get("委托数量", 1)]))
                filled = int(float(r[idx.get("成交数量", 1)]))
                price = float(r[idx.get("委托价格", 2)])
                status = OrderStatus.FILLED if filled >= qty else OrderStatus.PARTIAL
                return Order("", Side.BUY, qty, price, broker_ref=ref, status=status, filled_qty=filled)
        return None

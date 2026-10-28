# -*- coding: utf-8 -*-
"""纸面执行核心：纯函数、无 IO、可独立回放。

多策略对比盘的唯一交易约束实现点。策略层只产出 Intent（目标数量），
本模块负责校验与撮合：报价闸、持仓/T+1 可卖、整手、涨跌停、费用、
现金守恒与订单幂等。前向纸面测试与历史隔离回放共用同一实现，
不得在其他模块复制本文件的任何规则。

金额与费率均为研究假设（佣金万0.845+最低5、卖出印花税0.05%仅股票、
ETF/转债免印花、逆回购零费），晋升真钱前必须与实际券商核对。

拒绝码语义（与 spec.md Phase 1 一致）：
    SESSION_CLOSED   全局闸关闭或不在交易时段（该批全部拒绝，状态不变）
    DUPLICATE_ORDER  同一订单键在本批或历史中已执行（幂等）
    NO_QUOTE         无有效报价
    STALE_QUOTE      报价过期（is_stale 标记或超出 quote_max_age）
    PRICE_INVALID    价格非正/非有限，或数量非法
    LIMIT_UP_DOWN    触及涨跌停，纸面按不可成交处理
    NO_POSITION      卖出时无该持仓
    OVERSELL         卖出数量超过总持仓
    T_PLUS_1         卖出数量超过 T+1 可卖数量（今日买入部分不可卖）
    LOT_SIZE         买入数量不是申报单位整数倍（卖出允许零股，见 execute_intents）
    INSUFFICIENT_CASH 买入所需现金（含费用）不足
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence


class RejectReason(str, Enum):
    SESSION_CLOSED = "session_closed"
    DUPLICATE_ORDER = "duplicate_order"
    NO_QUOTE = "no_quote"
    STALE_QUOTE = "stale_quote"
    PRICE_INVALID = "price_invalid"
    LIMIT_UP_DOWN = "limit_up_down"
    NO_POSITION = "no_position"
    OVERSELL = "oversell"
    T_PLUS_1 = "t_plus_1"
    LOT_SIZE = "lot_size"
    INSUFFICIENT_CASH = "insufficient_cash"


class AssetClass(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    CB = "cb"
    REPO = "repo"


@dataclass(frozen=True)
class FeePolicy:
    """单资产类别费用模型（双边佣金 + 卖出单边印花税 + 双边过户费）。"""

    commission_rate: float
    commission_min: float
    stamp_sell_rate: float = 0.0
    transfer_rate: float = 0.0


@dataclass(frozen=True)
class AssetPolicy:
    """单资产类别交易规则。fixed_value=True 时按买入本金估值（逆回购口径）。"""

    asset_class: AssetClass
    lot_size: int          # 0 = 不受整手限制（逆回购按 10 张档位由调用方约束）
    t_plus_1: bool
    fixed_value: bool = False


# 研究假设费率（与 compare_deck_runner/quant REAL_FEE 同源）；ETF 默认 T+1
# 覆盖境内股票 ETF，债券/货币/跨境 ETF 的 T+0 品种由注册表按标的覆盖。
DEFAULT_FEE_POLICIES: Mapping[AssetClass, FeePolicy] = {
    AssetClass.STOCK: FeePolicy(0.0000845, 5.0, stamp_sell_rate=0.0005),
    AssetClass.ETF: FeePolicy(0.0000845, 5.0),
    AssetClass.CB: FeePolicy(0.0000845, 5.0),
    AssetClass.REPO: FeePolicy(0.0, 0.0),
}

DEFAULT_ASSET_POLICIES: Mapping[AssetClass, AssetPolicy] = {
    AssetClass.STOCK: AssetPolicy(AssetClass.STOCK, lot_size=100, t_plus_1=True),
    AssetClass.ETF: AssetPolicy(AssetClass.ETF, lot_size=100, t_plus_1=True),
    AssetClass.CB: AssetPolicy(AssetClass.CB, lot_size=10, t_plus_1=False),
    AssetClass.REPO: AssetPolicy(AssetClass.REPO, lot_size=0, t_plus_1=False, fixed_value=True),
}


@dataclass(frozen=True)
class Quote:
    """单一标的报价快照。price<=0 或非有限视为无效；is_stale 由上游行情链标注。"""

    symbol: str
    price: float
    timestamp: Optional[datetime] = None
    is_stale: bool = False
    limit_up: Optional[float] = None
    limit_down: Optional[float] = None


@dataclass(frozen=True)
class Lot:
    """一批同日买入的持仓。卖出按 buy_day 升序消耗（FIFO）。"""

    symbol: str
    qty: int
    price: float
    buy_day: date


@dataclass(frozen=True)
class Intent:
    """策略层的交易意图。order_id 为调用方幂等键，可空（由核心按内容派生）。"""

    symbol: str
    side: str                      # BUY / SELL
    qty: int
    asset_class: AssetClass
    name: str = ""
    price_hint: Optional[float] = None   # 仅审计参考，撮合一律用 Quote
    order_id: str = ""


@dataclass(frozen=True)
class ExecutionContext:
    """一次撮合批次的全局约束。session_open/trading_ok 由调用方（服务层）判定。"""

    slot: str
    trading_day: date
    now: Optional[datetime] = None
    session_open: bool = True
    trading_ok: bool = True
    quote_max_age: Optional[timedelta] = None
    executed_order_keys: frozenset = frozenset()


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str
    qty: int
    price: float
    notional: float
    fee: float
    asset_class: AssetClass
    order_key: str
    executed_day: date


@dataclass(frozen=True)
class Rejection:
    symbol: str
    side: str
    qty: int
    reason: RejectReason
    detail: str
    order_key: str


@dataclass(frozen=True)
class ExecutionResult:
    fills: tuple = ()
    rejections: tuple = ()
    cash: float = 0.0
    lots: tuple = ()
    fees_total: float = 0.0
    order_keys: tuple = ()


def order_key(ctx: ExecutionContext, intent: Intent) -> str:
    """确定性幂等键：同槽同日同标的同方向同数量视为同一订单。"""
    basis = f"{ctx.slot}|{ctx.trading_day}|{intent.symbol}|{intent.side}|{intent.qty}|{intent.order_id}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def trade_fee(asset_class: AssetClass, side: str, notional: float,
              fees: Optional[Mapping[AssetClass, FeePolicy]] = None) -> float:
    """佣金（双边、含最低）+ 股票卖出印花税 + 过户费。"""
    fp = (fees or DEFAULT_FEE_POLICIES)[asset_class]
    fee = max(notional * fp.commission_rate, fp.commission_min)
    if side == "SELL":
        fee += notional * fp.stamp_sell_rate
    fee += notional * fp.transfer_rate
    return round(fee, 2)


def _symbol_code6(symbol: str) -> str:
    digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def price_limit_ratio(symbol: str, name: str = "",
                      asset_class: AssetClass = AssetClass.STOCK) -> float:
    """单日涨跌幅限制比例。

    A 股主板 10%、创业板/科创板 20%、北交所 30%、主板 ST 5%；
    ETF 10%、转债 20%（上市首日更宽，纸面按常规日处理）。
    """
    if asset_class == AssetClass.CB:
        return 0.20
    if asset_class == AssetClass.ETF:
        return 0.10
    if asset_class == AssetClass.REPO:
        return 0.0
    code = _symbol_code6(symbol)
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("4", "8", "92")):
        return 0.30
    if "ST" in str(name).upper():
        return 0.05
    return 0.10


def price_limits(symbol: str, pre_close: float, name: str = "",
                 asset_class: AssetClass = AssetClass.STOCK,
                 ) -> tuple[Optional[float], Optional[float]]:
    """按昨收计算涨跌停价（交易所口径四舍五入到分）；昨收无效时返回 (None, None)。"""
    try:
        pc = float(pre_close)
    except (TypeError, ValueError):
        return (None, None)
    if not math.isfinite(pc) or pc <= 0:
        return (None, None)
    ratio = price_limit_ratio(symbol, name, asset_class)
    if ratio <= 0:
        return (None, None)
    up = math.floor(pc * (1 + ratio) * 100 + 0.5) / 100
    down = math.floor(pc * (1 - ratio) * 100 + 0.5) / 100
    return (round(up, 2), round(down, 2))


def _quote_rejection_reason(q: Optional[Quote], intent: Intent, ctx: ExecutionContext) -> Optional[Rejection]:
    if q is None:
        return Rejection(intent.symbol, intent.side, intent.qty, RejectReason.NO_QUOTE,
                         "无报价", order_key(ctx, intent))
    price = q.price
    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return Rejection(intent.symbol, intent.side, intent.qty, RejectReason.PRICE_INVALID,
                         f"报价非法: {price!r}", order_key(ctx, intent))
    if q.is_stale:
        return Rejection(intent.symbol, intent.side, intent.qty, RejectReason.STALE_QUOTE,
                         f"报价标记过期 ts={q.timestamp}", order_key(ctx, intent))
    if (q.timestamp is not None and ctx.now is not None
            and ctx.quote_max_age is not None
            and (ctx.now - q.timestamp) > ctx.quote_max_age):
        return Rejection(intent.symbol, intent.side, intent.qty, RejectReason.STALE_QUOTE,
                         f"报价超龄 {(ctx.now - q.timestamp)} > {ctx.quote_max_age}",
                         order_key(ctx, intent))
    if intent.side == "BUY" and q.limit_up is not None and price >= q.limit_up:
        return Rejection(intent.symbol, intent.side, intent.qty, RejectReason.LIMIT_UP_DOWN,
                         f"买入价 {price} ≥ 涨停 {q.limit_up}", order_key(ctx, intent))
    if intent.side == "SELL" and q.limit_down is not None and price <= q.limit_down:
        return Rejection(intent.symbol, intent.side, intent.qty, RejectReason.LIMIT_UP_DOWN,
                         f"卖出价 {price} ≤ 跌停 {q.limit_down}", order_key(ctx, intent))
    return None


def _sellable_qty(lots_for_symbol: Sequence[Lot], trading_day: date, t_plus_1: bool) -> int:
    if not t_plus_1:
        return sum(l.qty for l in lots_for_symbol)
    return sum(l.qty for l in lots_for_symbol if l.buy_day < trading_day)


def consume_lots(lots: Sequence[Lot], qty: int) -> tuple:
    """FIFO 消耗 qty 份后的剩余批次。账本重放必须复用本函数，禁止另写一份。"""
    return tuple(_consume_lots(list(lots), qty))


def _consume_lots(lots_for_symbol: list[Lot], qty: int) -> list[Lot]:
    """FIFO 消耗 qty 份，返回剩余批次（buy_day 升序消耗）。调用方保证 qty<=总持仓。"""
    remaining = qty
    kept: list[Lot] = []
    for lot in sorted(lots_for_symbol, key=lambda l: (l.buy_day, l.price)):
        if remaining <= 0:
            kept.append(lot)
            continue
        if lot.qty <= remaining:
            remaining -= lot.qty
        else:
            kept.append(replace(lot, qty=lot.qty - remaining))
            remaining = 0
    return [l for l in kept if l.qty > 0]


def execute_intents(
    cash: float,
    lots: Sequence[Lot],
    intents: Sequence[Intent],
    quotes: Mapping[str, Quote],
    ctx: ExecutionContext,
    asset_policies: Optional[Mapping[AssetClass, AssetPolicy]] = None,
    fee_policies: Optional[Mapping[AssetClass, FeePolicy]] = None,
) -> ExecutionResult:
    """撮合一批意图：先卖后买（保持调用方给出的相对顺序），现金守恒、无负现金。

    纯函数：不读行情、不写账本；调用方（服务层）负责把返回的新状态
    与 fills/rejections 作为同一事务写入账本，并持久化 order_keys。
    """
    policies = asset_policies or DEFAULT_ASSET_POLICIES
    fees = fee_policies or DEFAULT_FEE_POLICIES
    fills: list[Fill] = []
    rejections: list[Rejection] = []
    keys_used: list[str] = []
    fees_total = 0.0
    cur_cash = float(cash)
    cur_lots = {s: [l for l in lots if l.symbol == s] for s in {l.symbol for l in lots}}
    seen_keys: set[str] = set()

    if not ctx.trading_ok or not ctx.session_open:
        reason = RejectReason.SESSION_CLOSED
        detail = "trading_ok=False" if not ctx.trading_ok else "session_open=False"
        for intent in intents:
            rejections.append(Rejection(intent.symbol, intent.side, intent.qty, reason,
                                        detail, order_key(ctx, intent)))
        return ExecutionResult((), tuple(rejections), cur_cash, tuple(lots), 0.0, ())

    sells = [i for i in intents if i.side == "SELL"]
    buys = [i for i in intents if i.side == "BUY"]
    others = [i for i in intents if i.side not in ("BUY", "SELL")]

    def _gate(intent: Intent) -> Optional[Rejection]:
        key = order_key(ctx, intent)
        if key in ctx.executed_order_keys or key in seen_keys:
            return Rejection(intent.symbol, intent.side, intent.qty,
                             RejectReason.DUPLICATE_ORDER,
                             "订单键已执行" if key in ctx.executed_order_keys else "同批重复",
                             key)
        seen_keys.add(key)
        policy = policies.get(intent.asset_class)
        if policy is None:
            return Rejection(intent.symbol, intent.side, intent.qty,
                             RejectReason.PRICE_INVALID, f"未知资产类别 {intent.asset_class}", key)
        if intent.qty <= 0:
            return Rejection(intent.symbol, intent.side, intent.qty,
                             RejectReason.PRICE_INVALID, f"数量非法: {intent.qty}", key)
        # 整手校验只约束买入；A股卖出允许零股（不足一手部分一次性申报，
        # 纸面简化为任意正数量 ≤ 可卖量，不做一次性申报限制）。
        if intent.side == "BUY" and policy.lot_size and intent.qty % policy.lot_size != 0:
            return Rejection(intent.symbol, intent.side, intent.qty,
                             RejectReason.LOT_SIZE,
                             f"买入 {intent.qty} 不是 {policy.lot_size} 的整数倍", key)
        return _quote_rejection_reason(quotes.get(intent.symbol), intent, ctx)

    for intent in sells + buys + others:
        rej = _gate(intent)
        if rej is not None:
            rejections.append(rej)
            continue
        key = order_key(ctx, intent)
        q = quotes[intent.symbol]
        policy = policies[intent.asset_class]
        notional = round(intent.qty * q.price, 2)
        fee = trade_fee(intent.asset_class, intent.side, notional, fees)

        if intent.side == "SELL":
            held_lots = cur_lots.setdefault(intent.symbol, [])
            total_held = sum(l.qty for l in held_lots)
            if total_held == 0:
                rejections.append(Rejection(intent.symbol, intent.side, intent.qty,
                                            RejectReason.NO_POSITION, "无持仓", key))
                continue
            sellable = _sellable_qty(held_lots, ctx.trading_day, policy.t_plus_1)
            if intent.qty > total_held:
                rejections.append(Rejection(intent.symbol, intent.side, intent.qty,
                                            RejectReason.OVERSELL,
                                            f"持仓 {total_held} < 卖出 {intent.qty}", key))
                continue
            if intent.qty > sellable:
                rejections.append(Rejection(intent.symbol, intent.side, intent.qty,
                                            RejectReason.T_PLUS_1,
                                            f"T+1 可卖 {sellable} < 卖出 {intent.qty}", key))
                continue
            cur_lots[intent.symbol] = _consume_lots(held_lots, intent.qty)
            cur_cash += notional - fee
            fees_total += fee
            keys_used.append(key)
            fills.append(Fill(intent.symbol, intent.side, intent.qty, q.price, notional,
                              fee, intent.asset_class, key, ctx.trading_day))
        elif intent.side == "BUY":
            cost = notional + fee
            if cost > cur_cash:
                rejections.append(Rejection(intent.symbol, intent.side, intent.qty,
                                            RejectReason.INSUFFICIENT_CASH,
                                            f"需 {cost:.2f} > 现金 {cur_cash:.2f}", key))
                continue
            cur_cash -= cost
            cur_lots.setdefault(intent.symbol, []).append(
                Lot(intent.symbol, intent.qty, q.price, ctx.trading_day))
            fees_total += fee
            keys_used.append(key)
            fills.append(Fill(intent.symbol, intent.side, intent.qty, q.price, notional,
                              fee, intent.asset_class, key, ctx.trading_day))
        else:
            rejections.append(Rejection(intent.symbol, intent.side, intent.qty,
                                        RejectReason.PRICE_INVALID,
                                        f"未知方向 {intent.side}", key))

    all_lots = [l for sym_lots in cur_lots.values() for l in sym_lots]
    return ExecutionResult(tuple(fills), tuple(rejections), round(cur_cash, 2),
                           tuple(all_lots), round(fees_total, 2), tuple(keys_used))


# ---------------------------------------------------------------- 估值
@dataclass(frozen=True)
class ValuationResult:
    """equity 只含可定价持仓；无法定价的持仓记入 unvalued，绝不伪装成成本价。"""

    cash: float
    market_value: float
    equity: float                 # cash + market_value（不含 unvalued）
    unvalued: Mapping[str, str] = field(default_factory=dict)   # symbol -> missing/stale/invalid
    fully_valued: bool = True


def value_positions(
    cash: float,
    lots: Sequence[Lot],
    quotes: Mapping[str, Quote],
    asset_policies: Optional[Mapping[AssetClass, AssetPolicy]] = None,
    symbol_asset_class: Optional[Mapping[str, AssetClass]] = None,
) -> ValuationResult:
    """按报价估值；REPO 等固定价值资产按本金（批次成本价×数量）估值。

    symbol_asset_class：symbol → AssetClass 映射（由注册表/服务层提供）；
    缺失映射的 symbol 按成本价估值并标 unvalued='no_asset_class'，
    保证"未知资产不静默当股票"。
    """
    policies = asset_policies or DEFAULT_ASSET_POLICIES
    per_symbol: dict[str, int] = {}
    cost_per_symbol: dict[str, float] = {}
    for lot in lots:
        per_symbol[lot.symbol] = per_symbol.get(lot.symbol, 0) + lot.qty
        cost_per_symbol[lot.symbol] = cost_per_symbol.get(lot.symbol, 0.0) + lot.qty * lot.price
    market_value = 0.0
    unvalued: dict[str, str] = {}
    for symbol, qty in per_symbol.items():
        cls = (symbol_asset_class or {}).get(symbol)
        if cls is None or cls not in policies:
            unvalued[symbol] = "no_asset_class"
            continue
        if policies[cls].fixed_value:
            market_value += cost_per_symbol[symbol]
            continue
        q = quotes.get(symbol)
        if q is None:
            unvalued[symbol] = "missing"
            continue
        price = q.price
        if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            unvalued[symbol] = "invalid"
            continue
        if q.is_stale:
            unvalued[symbol] = "stale"
            continue
        market_value += qty * price
    return ValuationResult(cash=round(float(cash), 2),
                           market_value=round(market_value, 2),
                           equity=round(float(cash) + market_value, 2),
                           unvalued=unvalued,
                           fully_valued=not unvalued)


def merge_lots_view(lots: Iterable[Lot]) -> dict:
    """批次 → 按标的合并视图 {symbol: {qty, avg_cost}}，供服务层投影 v3 兼容 holds 行。"""
    view: dict[str, dict] = {}
    for lot in lots:
        entry = view.setdefault(lot.symbol, {"qty": 0, "cost": 0.0})
        entry["qty"] += lot.qty
        entry["cost"] += lot.qty * lot.price
    return {s: {"qty": v["qty"], "avg_cost": round(v["cost"] / v["qty"], 6)}
            for s, v in view.items() if v["qty"] > 0}

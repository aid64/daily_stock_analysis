# -*- coding: utf-8 -*-
"""操作单生成器（蓝图 ①-B）：目标持仓 → 人可照抄执行的操作单

铁律（EXECUTION-BLUEPRINT.md 〇）：
- 具体数字：每条操作的限价 = T-1 收盘价（与回测成交假设一致，保证
  模拟账本与真实成交可比），配两条边界规则（开盘越界放弃 / 14:50 未成交撤单）。
- 人是执行引擎：本模块只产出指令与状态机，绝不发送任何委托。
- 账本选型：JSON 文件而非 DB——单人系统的轻方案（盘点 D4：QR 的
  experiments.py 文件注册已证明优于重诊断体系），可 git 追踪、零迁移。

纯函数核心 generate_order_sheet() 不做任何 IO/网络；价格由调用方传入
（信号桥 ①-C 的职责），保证完全可测。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 边界规则默认值
DEFAULT_ABANDON_PCT = 0.02          # 开盘越限价 2% → 放弃
DEFAULT_CANCEL_TIME = "14:50"       # 未成交撤单时刻
SHEET_STATUS_PENDING = "pending"
SHEET_STATUS_PARTIALLY_FILLED = "partially_filled"   # FIX OrdStatus(1)
SHEET_STATUS_FILLED = "filled"
SHEET_STATUS_CANCELLED = "cancelled"
SHEET_STATUS_EXPIRED = "expired"

# E1（RESEARCH-BROAD 批次）：生成侧风控上限——防异常数据（价格错 100 倍
# 之类）生成巨量订单。vnpy OrderSizeRule 的等价物，作用于操作单生成。
MAX_SINGLE_ORDER_VALUE = 20_000.0   # 单笔订单估算金额上限（元）
# 粗估费用率（展示用；精确模型见模拟账本）。追加/撤单后重算合计同用此口径。
DEFAULT_ESTIMATED_COST_RATE = 0.0001

_ROOT = Path(__file__).resolve().parents[2]
SHEETS_DIR = _ROOT / "output" / "order_sheets"


def lot_size_for(symbol: str) -> int:
    """按代码形态推断申报单位：6 位数字且 11/12 开头 = 沪深转债（10 张），
    其余（股票/ETF）= 100 股/份。兼容 SH113062 / 113062.SH / 113062 三种形态。"""
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if len(digits) == 6 and digits.startswith(("11", "12")):
        return 10
    return 100


def _cost_fields(symbol: str, quantity: int, price: float,
                 direction: str) -> Dict[str, float]:
    """E1 成本门（RESEARCH-BROAD G1，复用 simulation_ledger 费率纯函数）：
    买入单按"当前买入+同价卖出"估算往返成本与回本涨幅；卖出一单向。
    转债免印花税（RuleBook cb 语义，与 ToFu fee_book 的债类注释一致）。
    """
    from src.services.simulation_ledger import calculate_costs, load_fees

    fees = dict(load_fees())
    if lot_size_for(symbol) == 10:
        fees["stamp_duty"] = 0.0  # 转债免印花税
    notional = quantity * price
    if direction == "buy":
        buy = calculate_costs(quantity, price, "buy", fees)
        sell = calculate_costs(quantity, price, "sell", fees)
        round_trip = buy["total"] + sell["total"]
    else:
        round_trip = calculate_costs(quantity, price, "sell", fees)["total"]
    be = round_trip / notional if notional > 0 else 0.0
    return {"round_trip_cost": round(round_trip, 2),
            "break_even_pct": round(be, 5)}


@dataclass(frozen=True)
class OrderLine:
    """一条待执行操作（买入/卖出）"""

    direction: str          # buy / sell
    symbol: str
    quantity: int           # 申报单位数（转债=张，股票=股）
    limit_price: float      # 具体限价 = T-1 收盘价
    abandon_rule: str       # 开盘越界放弃条件（人话）
    cancel_rule: str        # 未成交撤单时刻
    est_value: float        # 估算成交额
    reason: str             # 为什么动这只（一行）
    round_trip_cost: float = 0.0   # E1：往返成本估算（元，买入单=买+卖；卖出一单向）
    break_even_pct: float = 0.0    # E1：回本涨幅（往返成本/成交额，小数）


@dataclass(frozen=True)
class HoldLine:
    """一条持有不动（含理由——沉默也必须有解释）"""

    symbol: str
    quantity: int
    reason: str


@dataclass
class OrderSheet:
    """一张当日操作单"""

    sheet_id: str
    trade_date: str
    created_at: str
    orders: List[OrderLine] = field(default_factory=list)
    holds: List[HoldLine] = field(default_factory=list)
    no_action_reason: Optional[str] = None   # 三档之一：整单无操作的原因
    total_buy_value: float = 0.0
    total_sell_value: float = 0.0
    estimated_cost: float = 0.0
    status: str = SHEET_STATUS_PENDING
    fills: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # symbol -> 回填

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderSheet":
        orders = [OrderLine(**o) for o in data.pop("orders", [])]
        holds = [HoldLine(**h) for h in data.pop("holds", [])]
        return cls(orders=orders, holds=holds, **data)


def _lot_floor(quantity: float, lot: int) -> int:
    return int(quantity // lot) * lot if lot > 0 else int(quantity)


def generate_order_sheet(
    *,
    trade_date: date,
    current_positions: Dict[str, float],
    target_positions: Dict[str, float],
    t1_close_prices: Dict[str, float],
    available_cash: float,
    abandon_pct: float = DEFAULT_ABANDON_PCT,
    cancel_time: str = DEFAULT_CANCEL_TIME,
    cost_rate: float = DEFAULT_ESTIMATED_COST_RATE,
    keep_reason: str = "在目标持仓内",
    sheet_id: Optional[str] = None,
    reasons: Optional[Dict[str, str]] = None,
    hold_reasons: Optional[Dict[str, str]] = None,
) -> OrderSheet:
    """目标持仓 diff → 操作单（纯函数）。

    Args:
        current_positions: {symbol: 当前数量}
        target_positions: {symbol: 目标数量}（已过 ①-A 仓位上限与信号桥权重）
        t1_close_prices: {symbol: T-1 收盘价}——限价唯一来源，缺失的标的进 holds
        available_cash: 可用现金（买单受此约束）
        keep_reason: 持有条目的默认理由
        reasons: 账户决策引擎注入的按标的态度理由（卖出单 reason 覆盖）
        hold_reasons: 按标的持有理由覆盖（无报价等场景）
    """
    sheet_id = sheet_id or f"{trade_date.isoformat()}_{uuid.uuid4().hex[:8]}"
    reasons = reasons or {}
    hold_reasons = hold_reasons or {}
    orders: List[OrderLine] = []
    holds: List[HoldLine] = []

    all_symbols = sorted(set(current_positions) | set(target_positions))
    buy_value_total = 0.0
    sell_value_total = 0.0

    # 第一遍：卖出（先卖后买——回笼资金优先）
    pending_buys: List[tuple] = []
    for symbol in all_symbols:
        current = float(current_positions.get(symbol, 0) or 0)
        target = float(target_positions.get(symbol, 0) or 0)
        price = t1_close_prices.get(symbol)
        lot = lot_size_for(symbol)

        if price is None or price <= 0:
            if current > 0 or target > 0:
                holds.append(HoldLine(
                    symbol=symbol, quantity=int(current),
                    reason=hold_reasons.get(symbol)
                    or "缺 T-1 收盘价，无法给出限价——人工处理"))
            continue

        diff = target - current
        if diff == 0:
            if current > 0:
                holds.append(HoldLine(
                    symbol=symbol, quantity=int(current),
                    reason=hold_reasons.get(symbol) or keep_reason))
            continue
        if _lot_floor(abs(diff), lot) == 0:
            # 差额不足一个申报单位：既买不进也卖不出（非清仓场景）
            if current > 0:
                holds.append(HoldLine(
                    symbol=symbol, quantity=int(current),
                    reason=f"差额不足一手（目标 {int(target)} vs 当前 {int(current)}），维持"))
            continue
        if diff > 0:
            # 买入：现金约束放到第二遍统一裁剪
            pending_buys.append((symbol, current, target, diff, price, lot))
            continue
        if diff < 0:
            if current <= 0:
                continue  # 目标为 0 且本就无持仓 → 不出现在单子上
            sell_qty = _lot_floor(min(-diff, current), lot)
            if sell_qty <= 0:
                continue
            est = sell_qty * price
            sell_value_total += est
            orders.append(OrderLine(
                direction="sell", symbol=symbol, quantity=sell_qty,
                limit_price=round(price, 3),
                abandon_rule=f"开盘<{price * (1 - abandon_pct):.3f} 放弃",
                cancel_rule=f"{cancel_time} 未成交撤单",
                est_value=round(est, 2),
                reason=reasons.get(symbol)
                or ("跌出目标持仓" if target < current and target == 0
                    else f"目标 {int(target)} < 当前 {int(current)}"),
                **_cost_fields(symbol, sell_qty, price, "sell")))

    # 第二遍：买入——贪心顺序分配（价格升序优先买满，预算耗尽的进 holds）。
    # 不用等比缩：逐标的取整手会让等比份额向上溢出预算。
    if pending_buys:
        budget = max(available_cash, 0.0)
        for symbol, current, target, diff, price, lot in sorted(
                pending_buys, key=lambda x: x[4]):  # 价格升序
            need_qty = _lot_floor(diff, lot)
            if need_qty <= 0:
                holds.append(HoldLine(
                    symbol=symbol, quantity=int(current),
                    reason=f"差额不足一手（目标 {int(target)} vs 当前 {int(current)}），维持"))
                continue
            affordable = int(budget / price / lot) * lot if price > 0 else 0
            buy_qty = min(need_qty, max(affordable, 0))
            if buy_qty <= 0:
                # 文案按现金状态区分（P0.4 复盘）：available_cash<=0 才是
                # "未登记"，否则就是单纯买不起下一手
                if available_cash <= 0:
                    note = ("现金不足且账户未登记现金——若已转入请先 /持仓登记 记账")
                else:
                    note = (f"现金不足（可用 ¥{available_cash:,.0f}，"
                            f"本手需约 ¥{price * lot:,.0f}）——本轮未加仓")
                holds.append(HoldLine(
                    symbol=symbol, quantity=int(current), reason=note))
                continue
            est = buy_qty * price
            # E1 生成侧风控（vnpy OrderSizeRule 等价）：单笔金额上限——
            # 异常价格数据（错 100 倍）或异常权重不应生成巨额订单
            if est > MAX_SINGLE_ORDER_VALUE:
                capped = int(MAX_SINGLE_ORDER_VALUE / price / lot) * lot
                if capped <= 0:
                    holds.append(HoldLine(
                        symbol=symbol, quantity=int(current),
                        reason=(f"单笔金额上限保护：本手即超 "
                                f"¥{MAX_SINGLE_ORDER_VALUE:,.0f}（限价 "
                                f"{price:g} 异常？）——人工核实数据")))
                    continue
                buy_qty = capped
                est = buy_qty * price
                reason_note = f"（单笔上限 {MAX_SINGLE_ORDER_VALUE:,.0f} 截断）"
            else:
                reason_note = ""
            budget -= est
            buy_value_total += est
            reason = (f"新建仓至目标 {int(target)}" if current == 0
                      else f"补仓至目标 {int(target)}")
            if buy_qty < need_qty:
                reason += f"（现金受限，目标 {int(need_qty)} 张）"
            if reason_note:
                reason += reason_note
            orders.append(OrderLine(
                direction="buy", symbol=symbol, quantity=buy_qty,
                limit_price=round(price, 3),
                abandon_rule=f"开盘>{price * (1 + abandon_pct):.3f} 放弃",
                cancel_rule=f"{cancel_time} 未成交撤单",
                est_value=round(est, 2), reason=reason,
                **_cost_fields(symbol, buy_qty, price, "buy")))

    if not orders and not holds:
        no_action = "目标持仓与当前持仓均为空：无信号、无操作"
    elif not orders:
        no_action = None
        if not holds:
            no_action = "无操作"
    else:
        no_action = None

    return OrderSheet(
        sheet_id=sheet_id,
        trade_date=trade_date.isoformat(),
        created_at=datetime.now().isoformat(timespec="seconds"),
        orders=orders,
        holds=holds,
        no_action_reason=no_action,
        total_buy_value=round(buy_value_total, 2),
        total_sell_value=round(sell_value_total, 2),
        estimated_cost=round((buy_value_total + sell_value_total) * cost_rate, 2),
        status=SHEET_STATUS_PENDING,
    )


def recalculate_totals(
    sheet: OrderSheet,
    cost_rate: float = DEFAULT_ESTIMATED_COST_RATE,
) -> OrderSheet:
    """按当前 orders 重算单据合计（追加/撤单腿后调用，保证单内数字自洽）。

    与 generate_order_sheet 同一口径（estimated_cost 为展示用粗估；
    逐笔往返成本仍是 simulation_ledger 费率纯函数的输出）。
    """
    sheet.total_buy_value = round(sum(
        o.est_value for o in sheet.orders if o.direction == "buy"), 2)
    sheet.total_sell_value = round(sum(
        o.est_value for o in sheet.orders if o.direction == "sell"), 2)
    sheet.estimated_cost = round(
        (sheet.total_buy_value + sheet.total_sell_value) * cost_rate, 2)
    return sheet


# ── 文件账本 + 状态机 ─────────────────────────────────────────


def _sheet_path(trade_date: str, sheet_id: str) -> Path:
    return SHEETS_DIR / trade_date / f"{sheet_id}.json"


def save_order_sheet(sheet: OrderSheet) -> Path:
    path = _sheet_path(sheet.trade_date, sheet.sheet_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sheet.to_dict(), ensure_ascii=False, indent=1),
        encoding="utf-8")
    return path


def load_order_sheet(trade_date: str, sheet_id: str) -> Optional[OrderSheet]:
    path = _sheet_path(trade_date, sheet_id)
    if not path.exists():
        return None
    return OrderSheet.from_dict(json.loads(path.read_text(encoding="utf-8")))


def load_latest_sheet(trade_date: Optional[str] = None) -> Optional[OrderSheet]:
    """取指定日（缺省：最近有单的一天）的最新操作单。

    A6 日终过期（FIX OrdStatus C / Expired）：跨日后仍 pending 的单据
    在读取时惰性置为 expired 并落盘——昨天的限价与放弃条件今天不可执行；
    已成交量保留（真实账本不受影响），过期后不可再回填该单。
    """
    root = SHEETS_DIR
    if not root.exists():
        return None
    day_dir = root / trade_date if trade_date else max(
        (d for d in root.iterdir() if d.is_dir()), default=None,
        key=lambda d: d.name)
    if day_dir is None or not day_dir.exists():
        return None
    # 同日多单按"最新"选择必须按修改时间，不能按文件名（uuid 十六进制
    # 排序等于抽签——/成交回填 与决策快照都压在这个函数上）
    candidates = sorted(
        day_dir.glob("*.json"),
        key=lambda p: (p.stat().st_mtime, p.name))
    if not candidates:
        return None
    sheet = OrderSheet.from_dict(
        json.loads(candidates[-1].read_text(encoding="utf-8")))
    if (sheet.status == SHEET_STATUS_PENDING
            and sheet.trade_date
            and sheet.trade_date < date.today().isoformat()):
        sheet.status = SHEET_STATUS_EXPIRED
        sheet.no_action_reason = (
            "跨日自动过期（A6）：昨日限价与放弃条件今日不可执行")
        save_order_sheet(sheet)
    return sheet


def mark_order_filled(
    sheet: OrderSheet, symbol: str, actual_price: float,
    actual_quantity: Optional[int] = None,
) -> OrderSheet:
    """回填成交：更新 fills 并按累计量推进状态（真账本由调用方先写成功）。

    R0-A3（OPTIMIZATION-PLAN-V2）：fills 按累计量记账而非覆盖——同一腿的
    多次部分成交（30 张 + 70 张）必须汇总，累计达计划量才置 filled，
    未满为 partially_filled（FIX OrdStatus 1）。数量超计划的校验由
    调用方（fill 入口量价合理性门）负责，此处只汇总事实。
    """
    order = next((o for o in sheet.orders if o.symbol == symbol), None)
    planned_qty = order.quantity if order is not None else 0

    prev = sheet.fills.get(symbol) or {}
    prev_qty = int(prev.get("actual_quantity") or 0)
    # actual_quantity=None（旧调用约定）= 该腿全量成交
    new_qty = int(actual_quantity) if actual_quantity is not None else int(planned_qty)
    total_qty = prev_qty + new_qty
    # 加权均价（按张/股计权的成交均价，多次部分成交的公允口径）
    total_cost = (prev_qty * float(prev.get("actual_price") or 0)
                  + new_qty * float(actual_price))
    avg_price = total_cost / total_qty if total_qty else float(actual_price)

    sheet.fills[symbol] = {
        "actual_price": round(avg_price, 4),
        "actual_quantity": total_qty,
        "filled_at": datetime.now().isoformat(timespec="seconds"),
        "fill_count": int(prev.get("fill_count") or 0) + 1,
        "planned_quantity": planned_qty,
    }
    filled_symbols = set(sheet.fills)
    ordered_symbols = {o.symbol for o in sheet.orders}
    if planned_qty > 0 and total_qty >= planned_qty:
        sheet.fills[symbol]["complete"] = True
    else:
        sheet.fills[symbol]["complete"] = False
        sheet.status = SHEET_STATUS_PARTIALLY_FILLED
    if (ordered_symbols and ordered_symbols.issubset(filled_symbols)
            and all(sheet.fills[s].get("complete") for s in ordered_symbols)):
        sheet.status = SHEET_STATUS_FILLED
    elif sheet.status == SHEET_STATUS_PENDING and sheet.fills:
        # 任一腿有成交进展即标 partially_filled（比 pending 更诚实）
        sheet.status = SHEET_STATUS_PARTIALLY_FILLED
    save_order_sheet(sheet)
    return sheet


def mark_sheet_cancelled(sheet: OrderSheet, reason: str = "") -> OrderSheet:
    sheet.status = SHEET_STATUS_CANCELLED
    if reason:
        sheet.no_action_reason = reason
    save_order_sheet(sheet)
    return sheet


# ── 渲染（bot 推送格式）───────────────────────────────────────


def _unit_label(symbol: str) -> str:
    """数量单位：转债按张（申报 10 张整数倍），股票按股。"""
    digits = "".join(ch for ch in str(symbol) if ch.isdigit())
    if len(digits) == 6 and digits.startswith(("11", "12")):
        return "张"
    return "股"


def render_text(sheet: OrderSheet) -> str:
    lines: List[str] = []
    if sheet.status == SHEET_STATUS_PENDING:
        lines.append(f"【今日操作单 {sheet.trade_date}】")
    else:
        lines.append(f"【操作单 {sheet.trade_date}（{sheet.status}）】")

    if sheet.no_action_reason:
        lines.append(f"· {sheet.no_action_reason}")
        return "\n".join(lines)

    for o in sheet.orders:
        arrow = "买入" if o.direction == "buy" else "卖出"
        lines.append(
            f"· {arrow} {o.symbol} @{o.limit_price:g} 限价 · {o.quantity}{_unit_label(o.symbol)}"
            f" · {o.abandon_rule} · {o.cancel_rule}\n"
            f"  理由：{o.reason}（约 ¥{o.est_value:,.0f}）")
        # E1 成本门：往返成本与回本涨幅（用户语言，ToFu fee_book 同语义）
        if o.round_trip_cost > 0:
            be_pct = o.break_even_pct * 100
            if o.direction == "buy":
                lines.append(
                    f"  成本门：往返约 ¥{o.round_trip_cost:,.2f}"
                    f"（{be_pct:.2f}%）→ 需涨 {be_pct:.2f}% 才回本"
                    + ("——小单成本占比偏高，确认值得再做" if be_pct >= 1.0 else ""))
            else:
                lines.append(
                    f"  成本门：单向费用约 ¥{o.round_trip_cost:,.2f}"
                    f"（{be_pct:.2f}%）")
    for h in sheet.holds:
        qty_text = f"{h.quantity}{_unit_label(h.symbol)}" if h.quantity > 0 else "未持有"
        lines.append(f"· 持有 {h.symbol} {qty_text} —— {h.reason}")
    if sheet.orders:
        lines.append(
            f"小计：买入 ¥{sheet.total_buy_value:,.0f} / 卖出 ¥{sheet.total_sell_value:,.0f}"
            f" / 费用约 ¥{sheet.estimated_cost:,.0f}")
    return "\n".join(lines)

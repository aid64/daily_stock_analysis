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
SHEET_STATUS_FILLED = "filled"
SHEET_STATUS_CANCELLED = "cancelled"
SHEET_STATUS_EXPIRED = "expired"

_ROOT = Path(__file__).resolve().parents[2]
SHEETS_DIR = _ROOT / "output" / "order_sheets"


def lot_size_for(symbol: str) -> int:
    """按代码形态推断申报单位：6 位数字且 11/12 开头 = 沪深转债（10 张），
    其余（股票/ETF）= 100 股/份。兼容 SH113062 / 113062.SH / 113062 三种形态。"""
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if len(digits) == 6 and digits.startswith(("11", "12")):
        return 10
    return 100


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
    cost_rate: float = 0.0001,   # 粗估费用率（展示用；精确模型见模拟账本）
    keep_reason: str = "在目标持仓内",
    sheet_id: Optional[str] = None,
) -> OrderSheet:
    """目标持仓 diff → 操作单（纯函数）。

    Args:
        current_positions: {symbol: 当前数量}
        target_positions: {symbol: 目标数量}（已过 ①-A 仓位上限与信号桥权重）
        t1_close_prices: {symbol: T-1 收盘价}——限价唯一来源，缺失的标的进 holds
        available_cash: 可用现金（买单受此约束）
        keep_reason: 持有条目的默认理由
    """
    sheet_id = sheet_id or f"{trade_date.isoformat()}_{uuid.uuid4().hex[:8]}"
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
                    reason="缺 T-1 收盘价，无法给出限价——人工处理"))
            continue

        diff = target - current
        if diff == 0:
            if current > 0:
                holds.append(HoldLine(
                    symbol=symbol, quantity=int(current), reason=keep_reason))
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
                reason="跌出目标持仓" if target < current and target == 0
                       else f"目标 {int(target)} < 当前 {int(current)}"))

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
                note = ("现金不足且账户未登记现金——若已转入请先 /持仓登记 记账"
                        if current <= 0 else "现金不足，本轮未加仓")
                holds.append(HoldLine(
                    symbol=symbol, quantity=int(current), reason=note))
                continue
            est = buy_qty * price
            budget -= est
            buy_value_total += est
            reason = (f"新建仓至目标 {int(target)}" if current == 0
                      else f"补仓至目标 {int(target)}")
            if buy_qty < need_qty:
                reason += f"（现金受限，目标 {int(need_qty)} 张）"
            orders.append(OrderLine(
                direction="buy", symbol=symbol, quantity=buy_qty,
                limit_price=round(price, 3),
                abandon_rule=f"开盘>{price * (1 + abandon_pct):.3f} 放弃",
                cancel_rule=f"{cancel_time} 未成交撤单",
                est_value=round(est, 2), reason=reason))

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
    """取指定日（缺省：最近有单的一天）的最新操作单"""
    root = SHEETS_DIR
    if not root.exists():
        return None
    day_dir = root / trade_date if trade_date else max(
        (d for d in root.iterdir() if d.is_dir()), default=None,
        key=lambda d: d.name)
    if day_dir is None or not day_dir.exists():
        return None
    candidates = sorted(day_dir.glob("*.json"))
    if not candidates:
        return None
    return OrderSheet.from_dict(
        json.loads(candidates[-1].read_text(encoding="utf-8")))


def mark_order_filled(
    sheet: OrderSheet, symbol: str, actual_price: float,
    actual_quantity: Optional[int] = None,
) -> OrderSheet:
    """回填成交：更新 fills 并在全部订单成交后推进状态（真账本由调用方写 PortfolioService）"""
    sheet.fills[symbol] = {
        "actual_price": actual_price,
        "actual_quantity": actual_quantity,
        "filled_at": datetime.now().isoformat(timespec="seconds"),
    }
    ordered_symbols = {o.symbol for o in sheet.orders}
    if ordered_symbols and ordered_symbols.issubset(set(sheet.fills)):
        sheet.status = SHEET_STATUS_FILLED
    save_order_sheet(sheet)
    return sheet


def mark_sheet_cancelled(sheet: OrderSheet, reason: str = "") -> OrderSheet:
    sheet.status = SHEET_STATUS_CANCELLED
    if reason:
        sheet.no_action_reason = reason
    save_order_sheet(sheet)
    return sheet


# ── 渲染（bot 推送格式）───────────────────────────────────────


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
            f"· {arrow} {o.symbol} @{o.limit_price:g} 限价 · {o.quantity}手"
            f" · {o.abandon_rule} · {o.cancel_rule}\n"
            f"  理由：{o.reason}（约 ¥{o.est_value:,.0f}）")
    for h in sheet.holds:
        qty_text = f"{h.quantity}手" if h.quantity > 0 else "未持有"
        lines.append(f"· 持有 {h.symbol} {qty_text} —— {h.reason}")
    if sheet.orders:
        lines.append(
            f"小计：买入 ¥{sheet.total_buy_value:,.0f} / 卖出 ¥{sheet.total_sell_value:,.0f}"
            f" / 费用约 ¥{sheet.estimated_cost:,.0f}")
    return "\n".join(lines)

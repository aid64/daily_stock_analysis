"""离散整数手优化（栀染文章要点二：请使用离散优化）

绝大多数回测框架解出连续权重 w_i ∈ [0,1]，交易员简单 round 到
100 股——在 A 股"一手 100 股 + 高价股"环境下，直接 round 会产生
巨大 tracking error，这本质是背包问题（knapsack）变种：
    目标组合权重 {symbol: w_i}，在"现金约束 + 一手单位"下，
    求解整数手数 x_i（单位手数整数），使实际市值最贴近目标权重。

工程实现：
- N < 100 时该问题可在毫秒级求解（文章用 cvxpy + ecos_bb）。
- 本项目 1 万本金 + ≤13 只 ETF，用"按目标权重取整手 + 买入现金
  约束等比缩减"即可精确到一手，且确定性可测。
- 每手成本 P_i·lot_i（债券 ETF 用 10 张申报单位）；买入预算 =
  (可用现金 + 卖出回笼) × 缓冲，超出则按比例缩减买入份额。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    """当前持仓（执行层视角）"""

    symbol: str
    quantity: int       # 持有数量（正数）
    avail: int          # 可卖数量
    cost_price: float = 0.0


@dataclass
class Target:
    """目标权重与成交参考价"""

    symbol: str
    weight: float       # 目标组合权重 ∈ [0,1]，归一化后使用
    price: float        # 参考成交价（买用卖一 / 卖用买一）
    lot_size: int = 100  # 申报单位（债券 ETF 用 10）


@dataclass
class AllocResult:
    target_quantity: dict[str, int]   # 目标持仓数量
    target_value: dict[str, float]    # 目标市值
    cash_left: float                   # 分配后剩余现金
    total_value: float                 # 分配基准总市值


def _price_of(targets: list[Target], symbol: str) -> float:
    for t in targets:
        if t.symbol == symbol:
            return t.price
    return 0.0


def integer_lot_allocate(
    targets: list[Target],
    cash: float,
    current: dict[str, Position] | None = None,
    total_value: float | None = None,
    lot_buffer: float = 0.98,
    extra_prices: dict[str, float] | None = None,
) -> AllocResult:
    """按目标权重做整数手分配（确定性可测）

    步骤：
    1. 目标手数 = floor(base_value × 权重 / 每手成本) × 手数
       —— 目标只取决于组合基准市值与权重，与当前现金解耦
       （否则持仓后再平衡会因现金不足错误地清仓）。
    2. 买入约束：新买成本 ≤ (可用现金 + 卖出回笼) × lot_buffer，
       超出则按比例缩减各买入份额（对齐到一手）。
    3. 返回值供执行引擎先卖后买。

    Args:
        targets: 目标权重列表（可未归一化）
        cash: 当前可用现金
        current: 当前持仓
        total_value: 分配基准总市值（默认 = 现金 + 当前持仓市值）
        lot_buffer: 现金缓冲系数（预留手续费/滑点）
    """
    current = current or {}
    prices = dict(extra_prices or {})
    prices.update({t.symbol: t.price for t in targets})
    base_value = total_value or (cash + sum(
        p.quantity * prices.get(p.symbol, 0.0) for p in current.values()
    ))
    if base_value <= 0 or not targets:
        return AllocResult({}, {}, cash, base_value)

    total_weight = sum(t.weight for t in targets) or 1.0
    lot_of = {t.symbol: int(t.lot_size) for t in targets}

    # 1) 目标整数手（floor 到一手）
    raw_qty: dict[str, int] = {}
    for t in targets:
        lot_cost = t.price * lot_of[t.symbol]
        if t.price <= 0 or lot_cost <= 0:
            continue
        desired = base_value * (t.weight / total_weight)
        raw_qty[t.symbol] = int(desired / lot_cost) * lot_of[t.symbol]

    # 2) 买入资金约束：超出则等比缩减买入份额
    #    可用现金 = (现金 + 全部卖出回笼) × 缓冲。卖出回笼必须遍历
    #    current——被清仓的非目标标的（如 AAA 切到 BBB）也要计回笼，
    #    否则会误判现金不足而把新目标缩成 0。
    buys: dict[str, int] = {}
    sell_proceeds = 0.0
    for sym, pos in current.items():
        cur_qty = pos.quantity
        tgt = raw_qty.get(sym, 0)
        if cur_qty > tgt:
            sell_proceeds += (cur_qty - tgt) * prices.get(sym, 0.0)
    for sym, tgt in raw_qty.items():
        cur = current.get(sym)
        cur_qty = cur.quantity if cur else 0
        if tgt > cur_qty:
            buys[sym] = tgt - cur_qty

    buy_cost = sum(buys[s] * prices[s] for s in buys)
    available = (cash + sell_proceeds) * lot_buffer
    if buy_cost > available and buy_cost > 0:
        scale = available / buy_cost
        for sym, b in buys.items():
            new_buy = int(b * scale)
            new_buy -= new_buy % lot_of[sym]  # 对齐一手
            cur = current.get(sym)
            cur_qty = cur.quantity if cur else 0
            raw_qty[sym] = cur_qty + new_buy

    # 3) 汇总（剔除 0 仓位）
    target_quantity = {s: q for s, q in raw_qty.items() if q > 0}
    target_value = {s: q * prices[s] for s, q in target_quantity.items()}
    actual_buy_cost = sum(
        max(0, q - (current.get(s).quantity if current.get(s) else 0)) * prices[s]
        for s, q in target_quantity.items()
    )
    cash_left = cash - actual_buy_cost
    return AllocResult(target_quantity, target_value, cash_left, base_value)

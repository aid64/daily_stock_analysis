"""A 股交易规则表（RuleBook）——"规则即数据"

重构 R2（2026-08-29）：交易规则从散落的代码常量/前缀硬编码收拢为
(品种 × 生效日期) 查表。rqalpha 商业版把印花税做成时间序列数据的
先例证明该模式可行；PRIMARY_SOURCE_RESEARCH.md 第 4 节指出的
"按代码前缀法/全局 T+1/统一印花税不安全"工程债由此收敛。

本表覆盖：
- 品种分类：stock / etf / cb（含板块细分：创业板/科创板/北交所）
- 涨跌停历史：创业板 2020-08-24 注册制后 10%→20%（原 get_limit_pct
  对历史日期也返回 20%，跨制度回测约束失真）；科创板 ETF 588xxx 与
  创业板 ETF 159915 为 20%（原实现按股票前缀误判为 10%）；转债
  2022-08-01 起沪深 ±20%
- 印花税历史：2023-08-28 起卖出 0.1%→0.05%
- T+1：股票/ETF T+1，可转债 T+0
- 过户费：0.001% 双边（2022-2023 降费后口径；仅沪市，简化全市场）

日期感知边界（诚实声明）：backtrader 的 CommInfo._getcommission 无
日期上下文，佣金/印花税按"运行结束日的现行规则"取值——对 ETF+转债
组合无影响（均免税），股票历史回测需注意；涨跌停/T+1 在撮合闸
（有日期上下文）按 (品种, 日期) 实时查表。全日期精确费用在 R5
两段式执行模拟器中落实。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class AssetRules:
    """单品种在指定日期的交易规则"""

    asset_type: str          # stock / etf / cb
    board: str               # main / gem(创业板) / star(科创板) / bse(北交所) / none
    commission_rate: float   # 双边佣金（绝对小数）
    min_commission: float    # 单笔最低佣金（元）
    stamp_duty_sell: float   # 卖出印花税
    transfer_fee: float      # 双边过户费
    limit_pct: float | None  # 涨跌停幅度；None = 无统一日线涨跌幅限制
    t_plus_1: bool


# ── 品种分类 ─────────────────────────────────────────────────

def asset_type(symbol: str) -> str:
    """按代码前缀分类品种（cb / etf / stock）

    - 沪市转债：100/110/111/112/113/118 开头；深市转债：123/127/128 开头
    - 沪市 ETF：51/56/58 开头；深市 ETF/LOF：15/16 开头
    - 其余为股票（含北交所）
    """
    code = symbol.split(".")[0] if "." in symbol else symbol
    if code.startswith(("100", "110", "111", "112", "113", "118", "123", "127", "128")):
        return "cb"
    if code.startswith(("51", "56", "58", "15", "16")):
        return "etf"
    return "stock"


def _board(symbol: str) -> str:
    code = symbol.split(".")[0] if "." in symbol else symbol
    if code.startswith(("300", "301")):
        return "gem"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("43", "83", "87", "88", "92")):
        return "bse"
    return "main"


# ── 日期生效规则表（闭开区间：[start, end)，None 表示负无穷/正无穷） ──

# 印花税（卖出单边，股票品种）：2023-08-28 减半
_STOCK_STAMP_DUTY = [
    (None, datetime.date(2023, 8, 28), 0.001),
    (datetime.date(2023, 8, 28), None, 0.0005),
]

# 涨跌停：创业板注册制 2020-08-24 起 20%
_GEM_LIMIT = [
    (None, datetime.date(2020, 8, 24), 0.10),
    (datetime.date(2020, 8, 24), None, 0.20),
]

# 转债日涨跌幅：2022-08-01 沪深统一 ±20%；此前无统一日线限制口径（不设闸）
_CB_LIMIT = [
    (None, datetime.date(2022, 8, 1), None),
    (datetime.date(2022, 8, 1), None, 0.20),
]

# 特例覆盖：ETF 涨跌幅跟随标的板块，前缀无法推断的已知品种显式列出
# （159915 创业板 ETF / 588xxx 科创板 ETF 为 ±20%，原实现按 10% 处理）
_SYMBOL_LIMIT_OVERRIDES: dict[str, float] = {
    "159915.SZ": 0.20,   # 创业板 ETF
    "159952.SZ": 0.20,   # 创业板 ETF
    "159957.SZ": 0.20,   # 创业板 ETF
    "159980.SZ": 0.20,   # 创业板 ETF（双创）
    "588000.SH": 0.20,   # 科创 50 ETF
    "588080.SH": 0.20,   # 科创 50 ETF
    "588090.SH": 0.20,   # 科创板 ETF
}

DEFAULT_COMMISSION_RATE = 0.0001   # 双边万 1
DEFAULT_MIN_COMMISSION = 5.0       # 单笔最低 5 元（1 万本金约束）
DEFAULT_TRANSFER_FEE = 0.00001     # 双边 0.001%（2022-07 降费后口径）


def _effective(rules: list[tuple], date: datetime.date | None) -> object:
    """取 date 生效的最新规则；date 为 None 时取现行（最后一条）规则"""
    if date is None:
        return rules[-1][2]
    result = rules[0][2]
    for start, end, value in rules:
        if (start is None or date >= start) and (end is None or date < end):
            result = value
    return result


def get_rules(symbol: str, date: datetime.date | None = None) -> AssetRules:
    """查 (品种, 日期) 的交易规则

    date=None 返回现行规则（供无日期上下文的费用计算）。
    """
    atype = asset_type(symbol)
    board = _board(symbol) if atype == "stock" else "none"

    if atype == "cb":
        stamp, transfer = 0.0, 0.0
        limit = _effective(_CB_LIMIT, date)
        t_plus_1 = False                     # 转债 T+0
    elif atype == "etf":
        stamp, transfer = 0.0, 0.0           # ETF 免印花税与过户费
        limit = _SYMBOL_LIMIT_OVERRIDES.get(symbol.upper(), 0.10)
        t_plus_1 = True
    else:
        stamp = _effective(_STOCK_STAMP_DUTY, date)
        transfer = DEFAULT_TRANSFER_FEE
        if board == "gem":
            limit = _effective(_GEM_LIMIT, date)
        elif board == "bse":
            limit = 0.30
        elif board == "star":
            limit = 0.20
        else:
            limit = 0.10
        t_plus_1 = True

    return AssetRules(
        asset_type=atype,
        board=board,
        commission_rate=DEFAULT_COMMISSION_RATE,
        min_commission=DEFAULT_MIN_COMMISSION,
        stamp_duty_sell=float(stamp),
        transfer_fee=float(transfer),
        limit_pct=limit,
        t_plus_1=t_plus_1,
    )


def get_limit_pct(symbol: str, date: datetime.date | None = None) -> float | None:
    """涨跌停幅度（兼容旧接口；新增 date 感知与 None=无限制语义）

    与旧实现的差异（均为修正而非破坏）：
    - 创业板 2020-08-24 前返回 0.10（旧实现一律 0.20）
    - 159915/588xxx 等 ETF 返回 0.20（旧实现 0.10）
    - 转债 2022-08-01 前返回 None（旧实现 0.10 近似）
    """
    return get_rules(symbol, date).limit_pct

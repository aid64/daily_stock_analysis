"""可转债双低轮动策略（P5）

社区共识（知乎《三年翻倍：双低可转债策略》《如何挑选可转债》+ 评论区）：
- 双低值 = 转债价格 + 转股溢价率（溢价率用百分数，如 +20 表示溢价 20%）
- 过滤：信用评级 ≥ 下限（默认 AA-）、转股溢价率 ≤ 上限（默认 20%）、
  价格不高于当日中位数（避开已大涨的高位转债）、上市满 N 天
- 轮动：按双低值升序取前 top_n（1 万本金约束建议 ≤5），月/半月调仓
- 评论区修正：双低值低 ≠ 价格低（近期银行转债处高位）、剔除即将到期
  或无想象力的标的；可转债佣金极低（沪市万 0.05），是 1 万本金
  缓解佣金磨损的最佳品种

数据源：AKShare bond_zh_cov 当日全市场快照（含债现价/转股溢价率/
信用评级/上市时间）。注意：免费源只有当日快照，无历史 PIT 溢价率，
历史回测只能做"固定快照 universe + 动态价格"的近似（见架构文档说明）。

评级排序：AAA > AA+ > AA > AA- > A+ > A > A- > BBB+ > ...，数字越小越优。

R-B（2026-09-03）拆分为子模块：选债链 picks.py / PIT 快照 snapshot.py /
回测策略 strategy.py / MWU 专家 experts.py。本 __init__ 保持原公共 API
不变，所有 `from quant_research.backtest.convertible import ...` 照旧。
"""

from __future__ import annotations

from .experts import ConvertibleDoubleLowExpert, PITConvertibleDoubleLowExpert
from .picks import (
    RATING_ORDER,
    _filter_double_low_pool,
    compute_double_low_scores,
    pit_snapshot_picks,
    rating_rank,
    snapshot_picks,
)
from .snapshot import (
    PIT_REQUIRED_COLUMNS,
    load_pit_snapshot,
    normalize_cb_symbol,
    validate_pit_snapshot,
)
from .strategy import ConvertibleDoubleLow

__all__ = [
    "ConvertibleDoubleLow",
    "ConvertibleDoubleLowExpert",
    "PITConvertibleDoubleLowExpert",
    "RATING_ORDER",
    "PIT_REQUIRED_COLUMNS",
    "_filter_double_low_pool",
    "compute_double_low_scores",
    "snapshot_picks",
    "pit_snapshot_picks",
    "rating_rank",
    "load_pit_snapshot",
    "normalize_cb_symbol",
    "validate_pit_snapshot",
]

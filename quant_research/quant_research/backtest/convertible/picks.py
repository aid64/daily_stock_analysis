"""双低选债链：候选池过滤 / 打分 / 快照选债 / PIT 选债（唯一过滤链）

R-B 拆分自 convertible.py；pit_snapshot_picks 是 PIT 模式的唯一过滤链，
专家（experts.py）与两段式回测（two_stage.py）共用。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .snapshot import normalize_cb_symbol

import pandas as pd

RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
                "BBB+", "BBB", "BBB-", "BB+", "BB", "B+"]


def rating_rank(rating: str) -> int:
    """评级转序数（越小越优）；未知评级返回末尾"""
    if not isinstance(rating, str):
        return len(RATING_ORDER)
    r = rating.strip().upper()
    return RATING_ORDER.index(r) if r in RATING_ORDER else len(RATING_ORDER)


def _filter_double_low_pool(
    bond_df: pd.DataFrame,
    min_rating: str = "AA-",
    max_premium: float = 20.0,
    exclude_new_days: int = 30,
    max_price_median: bool = True,
    price_col: str = "price",
    premium_col: str = "premium_rate",
    rating_col: str = "rating",
    list_date_col: str = "list_date",
    as_of: Optional[str] = None,
) -> pd.DataFrame:
    """双低候选池过滤（单一来源，供 compute 与池规模检查复用）。

    过滤链：评级 → 溢价率上限 → 价格中位数 → 上市满 N 天 → 双低升序。
    返回全部通过过滤的候选（不截断 top_n）。
    """
    if bond_df is None or len(bond_df) == 0:
        return pd.DataFrame()

    df = bond_df.copy()
    need = {"symbol", price_col, premium_col, rating_col}
    if not need.issubset(df.columns):
        missing = need - set(df.columns)
        raise ValueError(f"可转债快照缺必要列: {missing}")

    # 归一化代码
    df["symbol"] = df["symbol"].map(normalize_cb_symbol)

    # 每次过滤后都重新派生数值列，避免布尔掩码与过滤后的索引错位
    def _price(df_):
        return pd.to_numeric(df_[price_col], errors="coerce")

    def _premium(df_):
        return pd.to_numeric(df_[premium_col], errors="coerce")

    df["double_low"] = _price(df) + _premium(df)

    # 过滤：评级
    df["rating_rank"] = df[rating_col].map(rating_rank)
    min_rank = rating_rank(min_rating)
    df = df[df["rating_rank"] <= min_rank]

    # 过滤：溢价率上限（负溢价保留，是强折价机会）
    df = df[_premium(df) <= max_premium]

    # 过滤：价格不高于中位数
    if max_price_median:
        med = _price(df).median()
        df = df[_price(df) <= med]

    # 过滤：上市满 N 天（as_of 缺省时无法判定则跳过）
    if list_date_col in df.columns and as_of is not None:
        listed = pd.to_datetime(df[list_date_col], errors="coerce")
        ref = pd.Timestamp(as_of)
        df = df[listed.notna() & ((ref - listed).dt.days >= exclude_new_days)]

    return df.sort_values("double_low", ascending=True, na_position="last")


def compute_double_low_scores(
    bond_df: pd.DataFrame,
    top_n: int = 5,
    min_rating: str = "AA-",
    max_premium: float = 20.0,
    exclude_new_days: int = 30,
    max_price_median: bool = True,
    price_col: str = "price",
    premium_col: str = "premium_rate",
    rating_col: str = "rating",
    list_date_col: str = "list_date",
    as_of: Optional[str] = None,
) -> pd.DataFrame:
    """计算双低评分并返回按双低值升序的候选表（已加筛选列）

    Args:
        bond_df: 可转债快照（须含 symbol/price/premium_rate/rating）
        top_n: 返回前 N 只候选
        min_rating: 最低评级（如 AA-）
        max_premium: 最大转股溢价率（百分数，如 20 表示 20%）
        exclude_new_days: 剔除上市未满 N 天的次新债
        max_price_median: 价格高于当日中位数则剔除
        as_of: 参考日期（YYYY-MM-DD），用于次新债判定；缺省用 list_date 现价

    Returns:
        筛选后按双低升序的 DataFrame，新增列 double_low
    """
    df = _filter_double_low_pool(
        bond_df,
        min_rating=min_rating,
        max_premium=max_premium,
        exclude_new_days=exclude_new_days,
        max_price_median=max_price_median,
        price_col=price_col,
        premium_col=premium_col,
        rating_col=rating_col,
        list_date_col=list_date_col,
        as_of=as_of,
    )
    return df.head(top_n).reset_index(drop=True)


def snapshot_picks(
    bond_df: pd.DataFrame,
    top_n: int = 5,
    min_rating: str = "AA-",
    max_premium: float = 20.0,
    exclude_new_days: int = 30,
    max_price_median: bool = True,
    as_of: Optional[str] = None,
) -> list[str]:
    """返回双低筛选后的标的代码列表（供执行引擎使用）"""
    picks = compute_double_low_scores(
        bond_df,
        top_n=top_n,
        min_rating=min_rating,
        max_premium=max_premium,
        exclude_new_days=exclude_new_days,
        max_price_median=max_price_median,
        as_of=as_of,
    )
    return list(picks["symbol"])


def pit_snapshot_picks(
    snapshot: pd.DataFrame,
    *,
    top_n: int,
    min_rating: str = "AA-",
    max_premium: float = 20.0,
    exclude_new_days: int = 30,
    max_price_median: bool = True,
    as_of,
    max_conv_value: Optional[float] = None,
    min_maturity_days: Optional[int] = None,
    max_price: Optional[float] = None,
    min_candidates: Optional[int] = None,
    dd_median_upper: Optional[float] = None,
    dd_median_lower: Optional[float] = None,
) -> dict[str, float]:
    """PIT 双低选债的唯一过滤链（重构 R5：PIT 专家与两段式回测共用）

    输入为单日截面长表（含 symbol/price/premium_rate[/rating/list_date/
    maturity_date]），价格须为当日真实价（专家模式由 feed 覆盖、两段式
    模式来自 PIT 本身），非有限/非正价格行在此剔除。

    过滤顺序（与 2026-08-18 起的专家语义逐位一致）：
    1. 温度计门控（P1-1，2026-08-29；None 关闭）：全市场双低中位数
       （价格+溢价率，过滤前全部有效行）≥ dd_median_upper → 返回空
       （让位现金，yyb凌波全清仓线 170 的社区一手依据）；<
       dd_median_lower（140）→ 正常 top_n；中间带 → 缩量（top_n 减半）。
       事后性声明：门槛值取自社区公开规则，非本仓库网格扫描产物。
    2. v2 生命周期过滤（max_conv_value 转股价值上限 / min_maturity_days
       剩余期限下限 / max_price 价格硬顶；None 关闭）
    3. min_candidates 池规模下限（完整过滤链后候选不足 → 返回空 = 让位现金）
    4. 双低打分（评级/溢价率/中位数/新债过滤 + top_n 等权）
    """
    if snapshot is None or snapshot.empty:
        return {}
    current_ts = pd.Timestamp(as_of).normalize()
    table = snapshot.copy()
    table["price"] = pd.to_numeric(table["price"], errors="coerce")
    table = table[np.isfinite(table["price"]) & (table["price"] > 0)]
    if table.empty:
        return {}

    eff_top_n = int(top_n)
    if dd_median_upper is not None or dd_median_lower is not None:
        market_dl = (table["price"] + pd.to_numeric(table["premium_rate"], errors="coerce")).dropna()
        if market_dl.empty:
            return {}
        median = float(market_dl.median())
        if dd_median_upper is not None and median >= dd_median_upper:
            return {}  # 全市场高估 → 让位现金
        if dd_median_lower is not None and median >= dd_median_lower:
            eff_top_n = max(1, int(top_n) // 2)  # 中间带缩量

    # 2) v2 过滤（与 PITConvertibleDoubleLowExpert._apply_v2_filters 同序同义）
    if max_conv_value is not None:
        conv_value = table["price"] / (1.0 + table["premium_rate"] / 100.0)
        table = table[conv_value <= max_conv_value]
    if min_maturity_days is not None and "maturity_date" in table.columns:
        maturity = pd.to_datetime(table["maturity_date"], errors="coerce")
        remain_days = (maturity - current_ts).dt.days
        table = table[remain_days.isna() | (remain_days >= min_maturity_days)]
    if max_price is not None:
        table = table[table["price"] <= max_price]
    if table.empty:
        return {}

    as_of_str = current_ts.date().isoformat()
    # 3) 池规模下限（完整过滤链口径，与实际选债一致）
    if min_candidates is not None:
        pool = _filter_double_low_pool(
            table,
            min_rating=min_rating,
            max_premium=max_premium,
            exclude_new_days=exclude_new_days,
            max_price_median=max_price_median,
            as_of=as_of_str,
        )
        if len(pool) < min_candidates:
            return {}

    # 4) 双低打分选债
    picks = compute_double_low_scores(
        table,
        top_n=eff_top_n,
        min_rating=min_rating,
        max_premium=max_premium,
        exclude_new_days=exclude_new_days,
        max_price_median=max_price_median,
        as_of=as_of_str,
    )
    if picks.empty:
        return {}
    weight = 1.0 / len(picks)
    return {symbol: weight for symbol in picks["symbol"]}

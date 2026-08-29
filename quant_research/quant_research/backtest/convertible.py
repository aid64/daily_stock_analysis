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
"""

from __future__ import annotations

from typing import Optional

import backtrader as bt
import numpy as np
import pandas as pd

RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
                "BBB+", "BBB", "BBB-", "BB+", "BB", "B+"]


def load_pit_snapshot(path) -> pd.DataFrame:
    """读取并严格校验可转债 PIT 历史快照。"""
    path = str(path)
    if path.lower().endswith(".parquet"):
        table = pd.read_parquet(path)
    elif path.lower().endswith(".pkl") or path.lower().endswith(".pickle"):
        table = pd.read_pickle(path)
    elif path.lower().endswith(".csv"):
        table = pd.read_csv(path)
    else:
        raise ValueError("PIT 快照仅支持 .parquet/.pkl/.pickle/.csv")
    required = PITConvertibleDoubleLowExpert.REQUIRED_COLUMNS
    missing = required - set(table.columns)
    if missing:
        raise ValueError(f"PIT 可转债快照缺少字段: {sorted(missing)}")
    if table.empty:
        raise ValueError("PIT 可转债快照为空")
    return table


def rating_rank(rating: str) -> int:
    """评级转序数（越小越优）；未知评级返回末尾"""
    if not isinstance(rating, str):
        return len(RATING_ORDER)
    r = rating.strip().upper()
    return RATING_ORDER.index(r) if r in RATING_ORDER else len(RATING_ORDER)


def normalize_cb_symbol(symbol: str) -> str:
    """可转债代码补交易所后缀：11xxxx → .SH，12xxx/123xxx → .SZ"""
    s = str(symbol).strip().upper()
    if "." in s:
        return s
    if s.isdigit() and len(s) == 6:
        if s.startswith("11"):
            return f"{s}.SH"
        if s.startswith(("12", "123")):
            return f"{s}.SZ"
    return s


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


# ── 回测策略 ────────────────────────────────────────────────


class ConvertibleDoubleLow(bt.Strategy):
    """可转债双低轮动（Backtrader 实现）

    策略消费 bond_table（DataFrame 快照，含 symbol/price/premium_rate/
    rating/list_date）与候选标的的日线 feed。每个调仓周期：
      1. 从快照计算双低排序（价格用快照价——免费源无历史 PIT 溢价率，
         文档标注此为近似；price_col 可换成 feed 现价增强时序性）
      2. 买入 top_n（等权，100 张/手可转债申报单位，2% 现金缓冲）
      3. 排名跌出 top_n 的持仓卖出

    注意：双低溢价率与评级是"当日快照"，回测跨年时存在幸存者偏差与
    固定 universe 局限，仅作近期行情验证，不作为长期盈利承诺。
    """

    params = (
        ("bond_table", None),       # DataFrame：symbol/price/premium_rate/rating/list_date
        ("top_n", 5),
        ("min_rating", "AA-"),
        ("max_premium", 20.0),
        ("exclude_new_days", 30),
        ("rebalance_freq", "M"),    # M 月 / W 周（转债低频）
        ("lot_size", 10),           # 可转债申报单位 10 张
        ("price_col", "price"),
        ("premium_col", "premium_rate"),
        ("rating_col", "rating"),
        ("list_date_col", "list_date"),
    )

    _LOT_BUFFER = 0.98

    def __init__(self):
        if self.p.bond_table is None:
            raise ValueError("ConvertibleDoubleLow 需要 bond_table 参数")
        self._last_period: tuple | None = None
        self._rank: dict[str, int] = {}   # symbol -> 双低排名（越小越优）

    def _is_rebalance_day(self, dt) -> bool:
        freq = str(self.p.rebalance_freq).upper()
        if freq == "D":
            return True
        if freq == "M":
            key = (dt.year, dt.month)
        else:
            iso = dt.isocalendar()
            key = (iso[0], iso[1])
        if key != self._last_period:
            self._last_period = key
            return True
        return False

    def _open_order_symbols(self) -> set[str]:
        return {o.data._name for o in self.broker.get_orders_open()}

    def _compute_rank(self, dt) -> dict[str, int]:
        """基于快照 + 当前 feed 价格计算双低排名"""
        table = self.p.bond_table.copy()
        rows = []
        for d in self.datas:
            name = d._name
            row = table[table["symbol"] == name]
            if row.empty:
                continue
            r = row.iloc[0].to_dict()
            price = float(d.close[0])
            if not np.isfinite(price) or price <= 0:
                continue
            premium = float(pd.to_numeric(r.get(self.p.premium_col, np.nan), errors="coerce"))
            rating = r.get(self.p.rating_col, "")
            if not np.isfinite(premium):
                continue
            rank = rating_rank(rating)
            if rank > rating_rank(self.p.min_rating):
                continue
            if premium > self.p.max_premium:
                continue
            r["_price"] = price
            r["_premium"] = premium
            r["_rating_rank"] = rank
            r["_double_low"] = price + premium
            rows.append(r)

        if not rows:
            return {}
        cand = pd.DataFrame(rows)
        cand = cand.sort_values("_double_low", ascending=True)
        return {row["symbol"]: i for i, row in cand.reset_index(drop=True).iterrows()}

    def next(self):
        dt = self.datas[0].datetime.date(0)
        if not self._is_rebalance_day(dt):
            return
        self._rank = self._compute_rank(dt)
        targets = set(self._rank.keys())

        # 卖出：跌出前 top_n 的持仓
        top = set(list(targets)[: int(self.p.top_n)])
        open_syms = self._open_order_symbols()
        for d in self.datas:
            name = d._name
            if name in open_syms:
                continue
            pos = self.getposition(d)
            if pos.size <= 0:
                continue
            if name not in top:
                self.close(d)

        # 买入 top_n（等权，10 张/手）
        per_value = self.broker.getvalue() / max(1, len(top))
        for d in self.datas:
            name = d._name
            if name not in top or name in open_syms:
                continue
            pos = self.getposition(d)
            price = float(d.close[0])
            if not np.isfinite(price) or price <= 0:
                continue
            lot = int(self.p.lot_size)
            diff_value = per_value - pos.size * price
            lots = int(diff_value * self._LOT_BUFFER / price / lot)
            if lots > 0:
                self.buy(d, size=lots * lot)


# ── MWU 专家 ────────────────────────────────────────────────


class ConvertibleDoubleLowExpert:
    """可转债双低专家（P5，供 MWU 融合层使用）

    与 MomentumExpert / MeanReversionExpert / DefensiveExpert 遵循同一
    Expert 协议：select(datas) -> {symbol: 专家内目标权重}。

    消费 bond_table（当日快照，含 symbol/price/premium_rate/rating/
    list_date）与候选转债的日线 feed。每个 MWU 调仓周期：
      1. 按快照 + 当前 feed 现价计算双低排名（price 用现价增强时序性）
      2. 返回 top_n 等权建议（债券 10 张/手申报单位由执行层处理）
      3. 选不出（无满足过滤条件的转债）返回空 dict → MWU 层按现金处理

    与 ConvertibleDoubleLow 回测策略共用 _compute_double_low_rank 逻辑，
    保证"历史回测"与"实盘信号"一致。
    """

    def __init__(
        self,
        bond_table,
        top_n: int = 5,
        min_rating: str = "AA-",
        max_premium: float = 20.0,
        exclude_new_days: int = 30,
        max_price_median: bool = True,
        name: str = "convertible_double_low",
    ):
        self.name = name
        if bond_table is None or len(bond_table) == 0:
            raise ValueError("ConvertibleDoubleLowExpert 需要 bond_table")
        self._bond_table = bond_table.copy()
        self.top_n = int(top_n)
        self.min_rating = min_rating
        self.max_premium = float(max_premium)
        self.exclude_new_days = int(exclude_new_days)
        self.max_price_median = bool(max_price_median)

    def _compute_rank(self, view) -> dict[str, int]:
        """基于快照 + 当前 feed 现价计算双低排名（越小越优）"""
        table = self._bond_table.copy()
        rows = []
        for s in view.all_series():
            name = s.name
            row = table[table["symbol"] == name]
            if row.empty:
                continue
            r = row.iloc[0].to_dict()
            price = s.last_close()
            if not np.isfinite(price) or price <= 0:
                continue
            premium = float(pd.to_numeric(r.get("premium_rate", np.nan), errors="coerce"))
            rating = r.get("rating", "")
            if not np.isfinite(premium):
                continue
            if rating_rank(rating) > rating_rank(self.min_rating):
                continue
            if premium > self.max_premium:
                continue
            r["_price"] = price
            r["_premium"] = premium
            r["_double_low"] = price + premium
            rows.append(r)

        if not rows:
            return {}
        cand = pd.DataFrame(rows)
        cand = cand.sort_values("_double_low", ascending=True)
        return {row["symbol"]: i for i, row in cand.reset_index(drop=True).iterrows()}

    def select(self, view) -> dict[str, float]:
        rank = self._compute_rank(view)
        if not rank:
            return {}
        top = sorted(rank, key=rank.get)[: self.top_n]
        w = 1.0 / len(top)
        return {sym: w for sym in top}


class PITConvertibleDoubleLowExpert:
    """按调仓日历史快照选债的可转债双低专家。"""

    REQUIRED_COLUMNS = {
        "as_of", "symbol", "price", "premium_rate", "rating", "list_date",
    }

    def __init__(
        self,
        pit_table,
        top_n: int = 5,
        min_rating: str = "AA-",
        max_premium: float = 20.0,
        exclude_new_days: int = 30,
        max_price_median: bool = True,
        max_conv_value: Optional[float] = None,
        min_maturity_days: Optional[int] = None,
        max_price: Optional[float] = None,
        min_candidates: Optional[int] = None,
        dd_median_upper: Optional[float] = None,
        dd_median_lower: Optional[float] = None,
        name: str = "convertible_double_low_pit",
    ):
        if pit_table is None or len(pit_table) == 0:
            raise ValueError("PITConvertibleDoubleLowExpert 需要 pit_table")
        missing = self.REQUIRED_COLUMNS - set(pit_table.columns)
        if missing:
            raise ValueError(f"PIT 可转债快照缺少字段: {sorted(missing)}")
        table = pit_table.copy()
        table["as_of"] = pd.to_datetime(table["as_of"], errors="coerce").dt.normalize()
        table["list_date"] = pd.to_datetime(table["list_date"], errors="coerce")
        table["price"] = pd.to_numeric(table["price"], errors="coerce")
        table["premium_rate"] = pd.to_numeric(table["premium_rate"], errors="coerce")
        table["symbol"] = table["symbol"].map(normalize_cb_symbol)
        if "maturity_date" in table.columns:
            table["maturity_date"] = pd.to_datetime(table["maturity_date"], errors="coerce")
        table = table.dropna(subset=["as_of", "symbol", "price", "premium_rate"])
        if table.empty:
            raise ValueError("PIT 可转债快照没有有效记录")
        self.name = name
        self._pit_table = table
        self.top_n = int(top_n)
        self.min_rating = min_rating
        self.max_premium = float(max_premium)
        self.exclude_new_days = int(exclude_new_days)
        self.max_price_median = bool(max_price_median)
        # v2 生命周期/强赎风险过滤（B1，2026-08-18）。None = 关闭（向后兼容）
        self.max_conv_value = float(max_conv_value) if max_conv_value is not None else None
        self.min_maturity_days = int(min_maturity_days) if min_maturity_days is not None else None
        self.max_price = float(max_price) if max_price is not None else None
        # 池规模下限（P2，2026-08-18）：过滤后候选 < min_candidates 时专家退出
        # （返回空 → MWU 按现金处理），防止存量缩水期的集中度暴露。
        # 阈值建议取结构值 top_n×3，不做网格扫描（避免对失败折拟合）。
        self.min_candidates = int(min_candidates) if min_candidates is not None else None
        # 温度计门控（P1-1，2026-08-29）：全市场双低中位数 ≥ dd_median_upper
        # → 专家让位现金；< dd_median_lower → 正常；中间 → 缩量。None = 关闭。
        # 门槛值取社区公开规则（yyb凌波全清仓线 170 / 启用线 140），非网格扫描产物。
        self.dd_median_upper = float(dd_median_upper) if dd_median_upper is not None else None
        self.dd_median_lower = float(dd_median_lower) if dd_median_lower is not None else None

    def _apply_v2_filters(self, snapshot: pd.DataFrame, current_ts: pd.Timestamp) -> pd.DataFrame:
        """B1 行业标准过滤（在双低打分前应用）。

        - max_conv_value: 转股价值 = 价格 / (1 + 溢价率/100)（数学恒等，PIT 可直接
          推导），超限视为强赎风险区剔除（社区共识阈值 135，强赎触发线 130×15/30日）
        - min_maturity_days: 剩余期限不足剔除（临期债波动衰减 + 反向选择，咖啡侠/
          狐狸说投资共识：回避到期 1 年内）；maturity 缺失行宽容保留
        - max_price: 价格硬顶（债性保护底线，社区底线规则"价格<115"的宽松版）
        """
        if self.max_conv_value is not None and not snapshot.empty:
            conv_value = snapshot["price"] / (1.0 + snapshot["premium_rate"] / 100.0)
            snapshot = snapshot[conv_value <= self.max_conv_value]
        if (
            self.min_maturity_days is not None
            and not snapshot.empty
            and "maturity_date" in snapshot.columns
        ):
            remain_days = (snapshot["maturity_date"] - current_ts).dt.days
            snapshot = snapshot[remain_days.isna() | (remain_days >= self.min_maturity_days)]
        if self.max_price is not None and not snapshot.empty:
            snapshot = snapshot[snapshot["price"] <= self.max_price]
        return snapshot

    def _snapshot(self, view) -> pd.DataFrame:
        current_date = pd.Timestamp(view.current_date()).normalize()
        snapshot = self._pit_table[self._pit_table["as_of"] == current_date].copy()
        if snapshot.empty:
            return snapshot
        prices = {}
        for s in view.all_series():
            p = s.last_close()
            if np.isfinite(p) and p > 0:
                prices[s.name] = p
        snapshot = snapshot[snapshot["symbol"].isin(prices)].copy()
        snapshot["price"] = snapshot["symbol"].map(prices)
        return snapshot

    def select(self, view) -> dict[str, float]:
        snapshot = self._snapshot(view)
        if snapshot.empty:
            return {}
        current_ts = pd.Timestamp(view.current_date()).normalize()
        return pit_snapshot_picks(
            snapshot,
            top_n=self.top_n,
            min_rating=self.min_rating,
            max_premium=self.max_premium,
            exclude_new_days=self.exclude_new_days,
            max_price_median=self.max_price_median,
            as_of=current_ts,
            max_conv_value=self.max_conv_value,
            min_maturity_days=self.min_maturity_days,
            max_price=self.max_price,
            min_candidates=self.min_candidates,
            dd_median_upper=self.dd_median_upper,
            dd_median_lower=self.dd_median_lower,
        )


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


def validate_pit_snapshot(
    table: pd.DataFrame,
    start: Optional[str] = None,
    end: Optional[str] = None,
    requested_symbols: Optional[Iterable[str]] = None,
) -> dict:
    """PIT 可转债快照完整性校验

    硬性违规（抛 ValueError）：
    - 必需列不全 / 空表
    - as_of 全部不可解析
    - 存在 as_of 在回测区间之后（未来数据泄漏）

    软性统计（写入报告 dict，不抛错）：
    - 唯一交易日数、唯一标的数、总行数
    - 每日期有效标的数（min/max/均值）与覆盖率直方图
    - 评级覆盖率（有有效评级 / 有价格）
    - 价格 <= 0、溢价率 NaN 等异常行计数
    - 上市日（list_date）晚于 as_of 的未来上市行计数

    Returns:
        校验报告 dict（供脚本写 coverage.json）
    """
    if table is None or len(table) == 0:
        raise ValueError("PIT 可转债快照为空")
    required = PITConvertibleDoubleLowExpert.REQUIRED_COLUMNS
    missing = required - set(table.columns)
    if missing:
        raise ValueError(f"PIT 可转债快照缺少字段: {sorted(missing)}")

    df = table.copy()
    df["as_of"] = pd.to_datetime(df["as_of"], errors="coerce").dt.normalize()
    df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce").dt.normalize()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["premium_rate"] = pd.to_numeric(df["premium_rate"], errors="coerce")

    invalid_as_of = int(df["as_of"].isna().sum())
    if df["as_of"].isna().all():
        raise ValueError("PIT 可转债快照的 as_of 全部无法解析")

    report: dict = {}
    report["total_rows"] = int(len(df))
    report["invalid_as_of_rows"] = invalid_as_of
    report["distinct_dates"] = int(df["as_of"].dropna().nunique())
    actual_symbols = {
        normalize_cb_symbol(str(symbol))
        for symbol in df["symbol"].dropna()
        if str(symbol).strip()
    }
    report["distinct_symbols"] = len(actual_symbols)
    if requested_symbols is not None:
        requested = {
            normalize_cb_symbol(str(symbol))
            for symbol in requested_symbols
            if str(symbol).strip()
        }
        report["requested_symbols"] = len(requested)
        report["missing_symbols"] = sorted(requested - actual_symbols)
        report["universe_coverage"] = (
            round(len(requested & actual_symbols) / len(requested), 4)
            if requested
            else 0.0
        )

    # 区间检查（硬性：不得有未来数据）
    if end is not None:
        end_ts = pd.Timestamp(end).normalize()
        future = int((df["as_of"] > end_ts).sum())
        if future:
            raise ValueError(
                f"PIT 快照存在 {future} 行 as_of 晚于回测结束日 {end}（未来数据泄漏）"
            )
        report["future_rows"] = 0
    if start is not None:
        start_ts = pd.Timestamp(start).normalize()
        report["pre_window_rows"] = int((df["as_of"] < start_ts).sum())

    valid = df.dropna(subset=["as_of", "price", "premium_rate"])
    report["valid_rows"] = int(len(valid))

    # 每日期有效标的数
    per_date = valid.groupby("as_of")["symbol"].nunique()
    report["dates_covered"] = int(len(per_date))
    report["symbols_per_date"] = {
        "min": int(per_date.min()) if len(per_date) else 0,
        "max": int(per_date.max()) if len(per_date) else 0,
        "mean": round(float(per_date.mean()), 2) if len(per_date) else 0.0,
    }

    # 评级覆盖率：有价格且有有效评级 / 有价格
    rated = valid["rating"].notna().sum()
    report["rating_coverage"] = (
        round(float(rated / len(valid)), 4) if len(valid) else 0.0
    )

    # 异常行
    report["bad_price_rows"] = int((df["price"] <= 0).sum())
    report["bad_premium_rows"] = int(df["premium_rate"].isna().sum())

    # 未来上市行：list_date 晚于 as_of（该时点债券尚未上市）
    both = df.dropna(subset=["as_of", "list_date"])
    future_list = int((both["list_date"] > both["as_of"]).sum())
    report["future_listing_rows"] = future_list

    return report

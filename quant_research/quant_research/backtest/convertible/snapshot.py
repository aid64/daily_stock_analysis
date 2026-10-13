"""可转债 PIT 快照：加载 / 代码规范化 / 完整性校验

R-B 拆分自 convertible.py；选债链见 picks.py，MWU 专家见 experts.py。
"""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

# PIT 长表必需列（单一真源；PITConvertibleDoubleLowExpert.REQUIRED_COLUMNS 同值）
PIT_REQUIRED_COLUMNS = {
    "as_of", "symbol", "price", "premium_rate", "rating", "list_date",
}


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
    required = PIT_REQUIRED_COLUMNS
    missing = required - set(table.columns)
    if missing:
        raise ValueError(f"PIT 可转债快照缺少字段: {sorted(missing)}")
    if table.empty:
        raise ValueError("PIT 可转债快照为空")
    return table


def pit_prices_to_feed(table: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """单只转债的 PIT 价格序列 → OHLCV feed（供回测/MWU 探针的 backtrader data）。

    OHLC 用收盘价填充；volume/amount 置 0——债券行情只看收盘价，且
    amount=0 使动量/均值回归类专家的流动性过滤天然排除债券（债券只经
    PIT 双低专家进入组合，与 run_backtest 的 CB feed 口径一致）。

    无 feed 时 PITConvertibleDoubleLowExpert._snapshot 会把全部候选按
    "无价格"过滤，专家退化为永远空选——本函数是"H 槽四专家"声明的
    数据前提。
    """
    rows = table[table["symbol"] == symbol].copy()
    rows["price"] = pd.to_numeric(rows["price"], errors="coerce")
    rows = rows[rows["price"] > 0].sort_values("as_of")
    if rows.empty:
        return pd.DataFrame()
    rows["as_of"] = pd.to_datetime(rows["as_of"], errors="coerce")
    rows = rows.dropna(subset=["as_of"])
    price = rows["price"].astype(float).to_numpy()
    return pd.DataFrame({
        "open": price, "high": price, "low": price, "close": price,
        "volume": 0.0, "amount": 0.0, "openinterest": 0.0,
    }, index=pd.DatetimeIndex(rows["as_of"], name="date"))


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
    required = PIT_REQUIRED_COLUMNS
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

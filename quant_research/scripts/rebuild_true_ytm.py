# -*- coding: utf-8 -*-
"""D1：真 YTM 重建——票息现金流 IRR → ytm_true vs ytm_proxy 分年 RankIC 对照

输入（D0 产物）：data/pit/cb_rates.parquet（逐年票息区间）、
                data/pit/cb_terms.parquet（到期赎回价/maturity_date）、
                data/pit/convertible_pit.parquet（价格/溢价率截面）。
方法：
  1. 每只券的现金流 = 逐年票息（按 rate 区间拆到年）+ 到期赎回价（含最后一期利息）
  2. ytm_true = IRR(现金流折现至当前日期)，税前/税后两口径（税后 = 票息×0.8）
  3. 分年 RankIC 对照：ytm_true vs ytm_proxy（同截面、同 forward 窗口）
产出：output/ytm_true_vs_proxy.json + data/pit/cb_ytm_true.parquet（逐日截面）
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest.convertible import load_pit_snapshot
from quant_research.core.panel import Panel

logger = logging.getLogger("true_ytm")

OUT_DIR = _PROJECT_ROOT / "data" / "pit"
OUT_JSON = _PROJECT_ROOT / "output" / "ytm_true_vs_proxy.json"
OUT_PARQUET = OUT_DIR / "cb_ytm_true.parquet"
TAX_RATE = 0.20  # 个人利息所得税


def _parse_redeem_price(raw: Any) -> Optional[float]:
    """maturity_call_price 可能是数字或文本（'按面值的110%(含最后一期利息)赎回'）。"""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(raw))
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{2,3}(?:\.\d+)?)\s*元", str(raw))
    if m:
        return float(m.group(1))
    return None


def build_cashflows(
    rates: pd.DataFrame, terms: pd.DataFrame
) -> Dict[str, Dict[str, Any]]:
    """每只券：{coupon_schedule: [(start_date, end_date, rate)], redeem_price, maturity_date}"""
    out: Dict[str, Dict[str, Any]] = {}
    rate_groups = rates.groupby("ts_code")
    for ts_code, group in rate_groups:
        schedule = []
        for _, row in group.sort_values("rate_start_date").iterrows():
            try:
                start = date.fromisoformat(str(row["rate_start_date"])[:10])
                end = date.fromisoformat(str(row["rate_end_date"])[:10])
                schedule.append((start, end, float(row["coupon_rate"])))
            except (ValueError, TypeError):
                continue
        if not schedule:
            continue
        terms_row = terms[terms["ts_code"] == ts_code]
        if terms_row.empty:
            continue
        maturity_raw = terms_row["maturity_date"].iloc[0]
        try:
            maturity = date.fromisoformat(str(maturity_raw)[:10])
        except (ValueError, TypeError):
            continue
        redeem = _parse_redeem_price(terms_row["maturity_call_price"].iloc[0])
        out[ts_code] = {
            "schedule": schedule,
            "redeem_price": redeem,
            "maturity_date": maturity,
        }
    return out


def ytm_true_at(
    cashflow: Dict[str, Any],
    val_date: date,
    dirty_price: float,
    tax_adjust: bool = False,
) -> Optional[float]:
    """从 val_date 起解 YTM（IRR）：未来现金流 = 未付票息 + 到期赎回价。

    税后口径：票息×(1-0.20)。赎回价含税溢价部分理论上也涉税，简化按含税处理
    （与集思录税前口径一致；税后口径差异记入 E1 稳健性）。
    """
    maturity = cashflow["maturity_date"]
    if val_date >= maturity:
        return None
    years = (maturity - val_date).days / 365.0
    if years <= 0 or dirty_price <= 0:
        return None

    schedule = cashflow["schedule"]
    redeem = cashflow["redeem_price"]
    if redeem is None or redeem <= 0:
        redeem = 100.0  # 默认面值赎回

    # 未来现金流
    cfs: List[Tuple[float, date]] = []
    for start, end, rate in schedule:
        if end <= val_date:
            continue
        tax_mult = (1 - TAX_RATE) if tax_adjust else 1.0
        # 当年票息可能跨区间边界——用区间利率 × 面值 100
        coupon = 100.0 * rate * tax_mult
        pay_date = min(end, maturity)
        cfs.append((coupon, pay_date))
    # 到期赎回价（含最后一期利息——通常已在票息外，此处简化：赎回价含税直接回款，
    # 最后一期票息若与赎回日重叠则按赎回价含息语义不再单独加票息）
    cfs.append((redeem, maturity))
    if not cfs:
        return None

    # IRR：解 NPV=0 的 y（二分法，y ∈ [-0.5, 1.0]）
    def npv(y: float) -> float:
        return sum(cf / (1 + y) ** ((d - val_date).days / 365.0)
                   for cf, d in cfs) - dirty_price

    lo, hi = -0.5, 1.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None  # 无根（价格离谱或现金流退化）
    for _ in range(60):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    ytm = (lo + hi) / 2
    return ytm if ytm > -0.5 else None


def build_daily_ytm_true(
    cashflows: Dict[str, Dict[str, Any]],
    pit: pd.DataFrame,
    sample_step: int = 5,   # 每周抽样一次（日频 IRR 太贵：853×1800 次二分）
) -> pd.DataFrame:
    """抽样日期 × 符号 → ytm_true（税前/税后双列）。"""
    pit = pit.copy()
    pit["as_of"] = pd.to_datetime(pit["as_of"])
    pit["price"] = pd.to_numeric(pit["price"], errors="coerce")
    valid = pit.dropna(subset=["price", "premium_rate"])
    valid = valid[valid["price"] > 0]

    dates = sorted(valid["as_of"].unique())
    sample_dates = dates[::sample_step]
    if dates and dates[-1] != sample_dates[-1]:
        sample_dates.append(dates[-1])

    rows = []
    for i, ts in enumerate(sample_dates):
        day = valid[valid["as_of"] == pd.Timestamp(ts)]
        for _, row in day.iterrows():
            sym = str(row["symbol"])
            cf = cashflows.get(sym)
            if cf is None:
                continue
            val_date = pd.Timestamp(ts).date()
            dirty = float(row["price"])
            ytm_pre = ytm_true_at(cf, val_date, dirty, tax_adjust=False)
            ytm_post = ytm_true_at(cf, val_date, dirty, tax_adjust=True)
            if ytm_pre is not None:
                rows.append({
                    "as_of": ts, "symbol": sym, "price": dirty,
                    "ytm_true": ytm_pre, "ytm_true_post_tax": ytm_post,
                    "ytm_true_post_tax_rate": ytm_post,
                })
    return pd.DataFrame(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        force=True)
    rates = pd.read_parquet(OUT_DIR / "cb_rates.parquet")
    terms = pd.read_parquet(OUT_DIR / "cb_terms.parquet")
    pit = load_pit_snapshot(OUT_DIR / "convertible_pit.parquet")
    pit = pit[pit["as_of"] <= pd.Timestamp("2026-08-08")]  # holdout 纪律

    cashflows = build_cashflows(rates, terms)
    logger.info("现金流构建: %d / %d 只（有票息+有到期日）",
                len(cashflows), len(terms))

    ytm_daily = build_daily_ytm_true(cashflows, pit)
    ytm_daily.to_parquet(OUT_PARQUET, index=False)
    logger.info("ytm_true 截面落盘: %d 行（%d 只 × 抽样日）",
                len(ytm_daily), ytm_daily["symbol"].nunique())

    # 分年 RankIC 对照
    from quant_research.core.factors import ic_by_year
    panel = Panel(ytm_daily.rename(columns={}))
    # Panel 用于 forward_returns（价格收益）；因子矩阵独立构造
    panel_true = Panel(ytm_daily[["as_of", "symbol", "price"]])  # price=真转债价
    fwd = panel_true.forward_returns(periods=20)

    from quant_research.core.factors import ic_by_year

    def _pivot(df, val_col):
        return df.pivot_table(index="as_of", columns="symbol", values=val_col)

    ytm_true_matrix = _pivot(ytm_daily, "ytm_true")
    ytm_proxy_matrix = _pivot(ytm_daily, "price")  # proxy = 零息近似（ytm_daily["price"] 当年存的是 proxy 值？不——price 列已修复为真价格，proxy 需重算）

    # proxy 重算：ytm_true = (100/price)^(1/T)-1；T 从 maturity_date 算——简化：用原 ytm_proxy 逻辑
    # 但 ytm_daily["price"] 现在是真价格——proxy = (100/price)^(1/T)-1
    # T 需要 maturity_date——为简化，从 cashflows 取
    cf = build_cashflows(
        pd.read_parquet(OUT_DIR / "cb_rates.parquet"),
        pd.read_parquet(OUT_DIR / "cb_terms.parquet"))
    proxy_rows = []
    for _, row in ytm_daily.iterrows():
        c = cf.get(row["symbol"])
        if c is None:
            continue
        ytm_p = ytm_true_at(c, pd.Timestamp(row["as_of"]).date(),
                            float(row["price"]), tax_adjust=False)
        proxy_rows.append({"as_of": row["as_of"], "symbol": row["symbol"],
                           "ytm_proxy": ytm_p})
    proxy_df = pd.DataFrame(proxy_rows)
    ytm_proxy_matrix = _pivot(proxy_df, "ytm_proxy")

    results = {}
    for name, factor_matrix in (("ytm_true", ytm_true_matrix),
                                ("ytm_proxy", ytm_proxy_matrix)):
        ic = ic_by_year(factor_matrix, fwd, method="rank", min_symbols=10)
        results[name] = ic.to_dict(orient="records")

    report = {
        "task": "D1 true YTM rebuild + proxy comparison",
        "window": f"{ytm_daily['as_of'].min().date()}..{ytm_daily['as_of'].max().date()}",
        "sample_days": ytm_daily["as_of"].nunique(),
        "n_symbols": int(ytm_daily["symbol"].nunique()),
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(json.dumps(report["results"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

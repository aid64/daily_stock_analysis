# -*- coding: utf-8 -*-
"""D2：共线性与评级分层诊断——回答"ytm 因子的 IC 到底来自哪里"

三问：
1. 共线性：ytm_true 与 (-price) 的截面 Spearman 相关逐日时序
   （>0.9 = 九成是价格因子；0.7-0.8 = 期限维度注入独立信息）
2. 残差 IC：控制 price 后 ytm_true 的残差是否仍有截面预测力
   （截面回归 ytm_true ~ a + b*price 取残差 → 残差 vs forward_returns 的 IC）
3. 评级分层 IC：AAA / AA / AA-及以下 三层分别算分年 RankIC
   （IC 集中在低评级层 = 信用风险补偿；高评级层也显著 = 接近真 alpha）
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("ytm_diagnosis")

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.core.factors import ic_by_year

OUT_JSON = _PROJECT_ROOT / "output" / "ytm_collinearity_diagnosis.json"
SAMPLE_STEP = 5


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        force=True)
    ytm = pd.read_parquet(_PROJECT_ROOT / "data" / "pit" / "cb_ytm_true.parquet")
    pit = pd.read_parquet(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")
    pit["as_of"] = pd.to_datetime(pit["as_of"])
    pit["price"] = pd.to_numeric(pit["price"], errors="coerce")

    # 附加评级
    rating_map = pit.drop_duplicates("symbol").set_index("symbol")["rating"]
    ytm["rating"] = ytm["symbol"].map(rating_map).fillna("unknown")
    ytm = ytm[ytm["price"] > 0].copy()

    # ── 1) 共线性：ytm vs (-price) 的截面 Spearman ──
    from scipy.stats import spearmanr
    collinear = []
    for ts, grp in ytm.groupby("as_of"):
        if len(grp) < 10:
            continue
        rho, _ = spearmanr(grp["ytm_true"], -grp["price"])
        collinear.append({"as_of": str(ts)[:10], "spearman": round(rho, 4),
                          "n": len(grp)})
    cl_df = pd.DataFrame(collinear)
    cl_df["year"] = cl_df["as_of"].str[:4]
    yearly_collinear = cl_df.groupby("year")["spearman"].agg(["mean", "min", "max"]).round(4)
    print("=== ytm vs (-price) 截面 Spearman（分年）===")
    print(yearly_collinear.to_string())

    # ── 2) 残差 IC：控制 price 后的 ytm_true 残差 vs forward_returns ──
    panel = Panel(ytm[["as_of", "symbol", "price"]])

    # 逐日截面回归：ytm_true ~ a + b*price → 残差
    residual_rows = []
    for ts, grp in ytm.groupby("as_of"):
        if len(grp) < 10:
            continue
        x = grp["price"].values
        y = grp["ytm_true"].values
        b = np.cov(x, y)[0, 1] / np.var(x) if np.var(x) > 0 else 0
        a = y.mean() - b * x.mean()
        resid = y - (a + b * x)
        for sym, r in zip(grp["symbol"], resid):
            residual_rows.append({"as_of": ts, "symbol": sym, "residual": r})
    resid_df = pd.DataFrame(residual_rows)

    # forward_returns：用 Panel 真价格
    fwd = panel.forward_returns(periods=20)

    # 残差 IC
    resid_matrix = resid_df.pivot_table(index="as_of", columns="symbol",
                                        values="residual")
    resid_ic = ic_by_year(resid_matrix, fwd, method="rank", min_symbols=10)
    print("\n=== 残差 IC（控制 price 后）===")
    print(resid_ic.to_string(index=False))

    # ── 3) 评级分层 IC ──
    tier_map = {"AAA": "AAA", "AA+": "AA", "AA": "AA", "AA-": "AA-", "A+": "A-",
                "A": "A-", "A-": "A-", "BBB+": "BBB", "BBB": "BBB",
                "BBB-": "BBB", "BB+": "BB", "BB": "BB", "B+": "BB",
                "unknown": "unknown"}
    ytm["tier"] = ytm["rating"].map(tier_map).fillna("unknown")
    tier_ic = {}
    for tier in ("AAA", "AA", "AA-", "A-", "BBB"):
        sub = ytm[ytm["tier"] == tier]
        if len(sub) < 100:
            continue
        mat = sub.pivot_table(index="as_of", columns="symbol", values="ytm_true")
        fwd_sub = panel.forward_returns(periods=20, universe=list(mat.columns))
        ic = ic_by_year(mat, fwd_sub, method="rank", min_symbols=5)
        tier_ic[tier] = ic.to_dict(orient="records")

    print("\n=== 评级分层 ytm_true RankIC（分年）===")
    for tier, rows in tier_ic.items():
        if not rows:
            print(f"  {tier}: 无数据")
            continue
        n_pos = sum(1 for r in rows if r["mean_ic_positive"])
        print(f"  {tier}: 正年份 {n_pos}/{len(rows)}，均值区间 "
              f"[{min(r['mean_ic'] for r in rows):+.3f}, {max(r['mean_ic'] for r in rows):+.3f}]")

    # ── 汇总 ──
    report = {
        "task": "D2 ytm_true collinearity and rating-tier diagnosis",
        "collinearity": {
            "yearly": yearly_collinear.to_dict(orient="index"),
            "overall_mean": round(cl_df["spearman"].mean(), 4),
            "interpretation": (
                ">0.9 = ytm 即价格因子；0.7-0.8 = 期限维度注入独立信息"),
        },
        "residual_ic": resid_ic.to_dict(orient="records"),
        "rating_tier_ic": tier_ic,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    logger.info("诊断报告已写入 %s", OUT_JSON)
    return 0


from quant_research.core.panel import Panel
from quant_research.core.factors import ic_by_year

if __name__ == "__main__":
    sys.exit(main())

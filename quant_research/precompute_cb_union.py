# -*- coding: utf-8 -*-
"""预计算：全量 PIT 下月频调仓日的双低候选并集（限制回测 feed 规模）。

从 PIT 历史本身推导每个调仓日通过基础过滤（评级/溢价/新债）后的双低
top15 候选，输出并集清单。纯确定性计算（只用 PIT 历史数据，无未来信息），
用于把回测的 CB feed 从 849 只压缩到实际会入选的券。
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd

pit = pd.read_parquet("data/pit/convertible_pit.parquet")
pit["as_of"] = pd.to_datetime(pit["as_of"])

dates = pd.Series(sorted(pit["as_of"].unique()), name="d")
monthly = dates.groupby(dates.dt.to_period("M")).first().tolist()

RATING_ORDER = {"AAA": 0, "AA+": 1, "AA": 2, "AA-": 3, "A+": 4, "A": 5, "A-": 6}

union: set[str] = set()
per_month = []
for d in monthly:
    snap = pit[pit["as_of"] == d]
    ok_rating = snap["rating"].map(lambda r: RATING_ORDER.get(r, 9) <= RATING_ORDER["AA-"])
    b = snap[ok_rating & (snap["premium_rate"] <= 20.0)]
    if b.empty:
        continue
    med = b["price"].median()
    b = b[b["price"] <= med]
    # 上市满 30 天
    ld = pd.to_datetime(b["list_date"], errors="coerce")
    b = b[(pd.Timestamp(d) - ld).dt.days >= 30]
    top15 = b.assign(dl=b["price"] + b["premium_rate"]).nsmallest(15, "dl")
    union.update(top15["symbol"])
    per_month.append(len(top15))

syms = sorted(union)
with open("data/cb_candidate_union.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(syms) + "\n")

print(f"调仓月数 {len(per_month)}，候选并集 {len(syms)} 只 → data/cb_candidate_union.txt")
print(f"月度 top15 命中数: min {min(per_month)}, median {int(pd.Series(per_month).median())}")

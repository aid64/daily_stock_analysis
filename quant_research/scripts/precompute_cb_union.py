# -*- coding: utf-8 -*-
"""预计算：全量 PIT 下按调仓周期的双低候选并集（限制回测 feed 规模）。

从 PIT 历史本身推导每个调仓日通过全部过滤后的双低 top_n 候选，输出并集。
纯确定性计算（只用 PIT 历史行，无未来信息），用于把回测/WF 的 CB feed 从
全量池（849 只）压缩到实际可能入选的券。选债结果不受影响（并集 ⊇ 候选）。

与 PITConvertibleDoubleLowExpert 的过滤链严格一致：
  rating → premium → median → 新债 N 日 → v2(转股价值/临期/价格顶) → top_n

网格扫描注意：若扫参涉及 top_n / max_premium / v2 阈值，必须用覆盖整个
网格的"最宽参数"生成并集（top_n 取最大、上限阈值取最宽），否则不同组合
的 feed 池不一致，结果不可比。

用法：
    py scripts/precompute_cb_union.py                      # 默认（月频，top15，无 v2）
    py scripts/precompute_cb_union.py --freq W --top-n 20  # 周频宽并集
    py scripts/precompute_cb_union.py --top-n 5 --max-conv-value 135 \
        --min-maturity-days 365 --out data/cb_candidate_union_v2.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest.convertible import rating_rank  # noqa: E402

RATING_ORDER = {r: rating_rank(r) for r in ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-"]}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="预计算双低候选并集")
    p.add_argument("--pit", default="data/pit/convertible_pit.parquet")
    p.add_argument("--freq", default="M", choices=["M", "W"], help="调仓频率（月/周）")
    p.add_argument("--start", default="2019-01-01", help="起始日期")
    p.add_argument("--end", default=None, help="结束日期，默认 PIT 末日")
    p.add_argument("--top-n", type=int, default=15, help="每期候选数（网格扫描取最大值）")
    p.add_argument("--min-rating", default="AA-")
    p.add_argument("--max-premium", type=float, default=20.0, help="网格扫描取最宽值")
    p.add_argument("--exclude-new-days", type=int, default=30)
    p.add_argument("--max-conv-value", type=float, default=None, help="v2 转股价值上限")
    p.add_argument("--min-maturity-days", type=int, default=None, help="v2 剩余期限下限")
    p.add_argument("--max-price", type=float, default=None, help="v2 价格硬顶")
    p.add_argument("--out", default="data/cb_candidate_union.txt")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    pit = pd.read_parquet(_PROJECT_ROOT / args.pit)
    pit["as_of"] = pd.to_datetime(pit["as_of"])
    lo = pd.Timestamp(args.start)
    hi = pd.Timestamp(args.end) if args.end else pit["as_of"].max()
    pit = pit[(pit["as_of"] >= lo) & (pit["as_of"] <= hi)]

    dates = pd.Series(sorted(pit["as_of"].unique()), name="d")
    if args.freq == "M":
        rebalance_days = dates.groupby(dates.dt.to_period("M")).first().tolist()
    else:
        rebalance_days = dates.groupby(dates.dt.to_period("W")).first().tolist()

    min_rank = RATING_ORDER.get(args.min_rating, rating_rank(args.min_rating))
    union: set[str] = set()
    per_period: list[int] = []

    for d in rebalance_days:
        snap = pit[pit["as_of"] == d]
        ok_rating = snap["rating"].map(lambda r: RATING_ORDER.get(r, 99) <= min_rank)
        b = snap[ok_rating & (snap["premium_rate"] <= args.max_premium)]
        if b.empty:
            per_period.append(0)
            continue
        med = b["price"].median()
        b = b[b["price"] <= med]
        ld = pd.to_datetime(b["list_date"], errors="coerce")
        b = b[ld.notna() & ((pd.Timestamp(d) - ld).dt.days >= args.exclude_new_days)]
        # v2 过滤（与专家 _apply_v2_filters 一致，None = 关闭）
        if args.max_conv_value is not None and not b.empty:
            cv = b["price"] / (1.0 + b["premium_rate"] / 100.0)
            b = b[cv <= args.max_conv_value]
        if args.min_maturity_days is not None and not b.empty and "maturity_date" in b.columns:
            md = pd.to_datetime(b["maturity_date"], errors="coerce")
            remain = (md - pd.Timestamp(d)).dt.days
            b = b[remain.isna() | (remain >= args.min_maturity_days)]
        if args.max_price is not None and not b.empty:
            b = b[b["price"] <= args.max_price]
        top = b.assign(dl=b["price"] + b["premium_rate"]).nsmallest(args.top_n, "dl")
        union.update(top["symbol"])
        per_period.append(len(top))

    syms = sorted(union)
    out = _PROJECT_ROOT / args.out
    out.write_text("\n".join(syms) + "\n", encoding="utf-8")
    s = pd.Series(per_period)
    print(
        f"调仓期数 {len(per_period)}（{args.freq}），并集 {len(syms)} 只 → {out}\n"
        f"每期命中: min {int(s.min())} / median {int(s.median())} / max {int(s.max())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

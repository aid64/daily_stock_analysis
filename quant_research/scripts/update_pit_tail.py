# -*- coding: utf-8 -*-
"""增量更新 raw 缓存尾部：为已有 akshare 缓存的券拉取末日之后的新行。

与 backfill_cb_from_akshare.py 的区别：backfill 只处理"缓存缺失"的券；
本脚本处理"缓存存在但停在旧日期"的券——paper tracking 每次运行前调用。

机制：读 meta.json 的 akshare 来源标记，逐券拉 value_analysis，
append 末日之后的新行到对应年份 CSV（不改历史行）；无新行跳过。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("pit_tail_update")

_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "pit" / "raw"


def _fetch_history(code: str) -> Optional[pd.DataFrame]:
    import time

    import akshare as ak

    for attempt in range(3):
        try:
            df = ak.bond_zh_cov_value_analysis(symbol=code)
            break
        except Exception:  # noqa: BLE001 - 网络抖动重试（东财源间歇性 10054）
            if attempt == 2:
                return None
            time.sleep(2 ** attempt)
    if df is None or df.empty:
        return None
    out = pd.DataFrame({
        "date": pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d"),
        "close": pd.to_numeric(df["收盘价"], errors="coerce"),
        "premium_rate": pd.to_numeric(df["转股溢价率"], errors="coerce"),
    })
    return out.dropna(subset=["close"])


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="增量更新 PIT raw 缓存尾部")
    parser.add_argument("--raw-dir", default=str(_RAW_DIR))
    parser.add_argument("--throttle", type=float, default=0.8, help="逐券间隔秒")
    parser.add_argument("--limit", type=int, default=None, help="最多更新 N 只（调试用）")
    args = parser.parse_args(argv)

    raw_dir = Path(args.raw_dir)
    updated, skipped_current, failed, no_new = 0, 0, 0, 0
    sym_dirs = sorted(d for d in raw_dir.iterdir() if d.is_dir())
    logger.info("raw 缓存 %d 只", len(sym_dirs))

    for i, sym_dir in enumerate(sym_dirs, 1):
        symbol = sym_dir.name
        meta_path = sym_dir / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("source") != "akshare":
            continue  # iFinD 缓存不动（沿用 backfill 的来源保护语义）

        code = symbol.split(".")[0]
        # 找最新缓存的年份文件与末日
        year_files = sorted(sym_dir.glob("????.csv"))
        if not year_files:
            continue
        latest_file = year_files[-1]
        try:
            latest_df = pd.read_csv(latest_file)
            last_date = str(latest_df["date"].iloc[-1])
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: 读取失败 %s", symbol, exc)
            failed += 1
            continue

        hist = _fetch_history(code)
        if hist is None:
            failed += 1
            continue
        new_rows = hist[hist["date"] > last_date]
        if new_rows.empty:
            skipped_current += 1
        else:
            for _, row in new_rows.iterrows():
                year = str(row["date"])[:4]
                year_file = sym_dir / f"{year}.csv"
                if year_file.exists():
                    existing = pd.read_csv(year_file)
                    if row["date"] in existing["date"].values:
                        continue
                    row_df = pd.DataFrame([row])
                    combined = pd.concat([existing, row_df], ignore_index=True)
                    combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date")
                    combined.to_csv(year_file, index=False)
                else:
                    row_df = pd.DataFrame([row])
                    row_df.to_csv(year_file, index=False)
            updated += 1
            new_count = len(new_rows)
            logger.info("%s: +%d 行（%s → %s）", symbol, new_count,
                        last_date, str(new_rows["date"].iloc[-1]))

        if i % 50 == 0:
            logger.info("进度 %d/%d（更新 %d, 已最新 %d, 失败 %d）",
                        i, len(sym_dirs), updated, skipped_current, failed)
        time.sleep(args.throttle)
        # limit 计"处理过的 akshare 券"（含已最新/失败）——PIT 已最新时
        # limit 也要能快速退出，防管线遍历全量 914 只超时
        if args.limit and (updated + skipped_current + failed) >= args.limit:
            break

    logger.info("完成: 更新 %d, 已最新 %d, 失败 %d", updated, skipped_current, failed)
    # 管线友好：只要有一只成功（或无需更新）就 rc=0；
    # 全部失败才 rc=1（让 daily_pipeline 判定步骤失败并告警）
    if updated == 0 and failed > 0 and failed > no_new:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

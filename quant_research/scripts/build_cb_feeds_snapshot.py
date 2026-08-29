# -*- coding: utf-8 -*-
"""构建冻结的可转债日线 feeds 快照（pkl），供 Walk-Forward 复现使用。

与 build_feeds_snapshot.py（ETF）同哲学：WF 依赖冻结输入保证可复现。
标的清单来自 precompute_cb_union.py 的候选并集（v2 宽参数超集），
数据走 DataLoader 多源链（tushare cb_daily 主力，缓存优先）。

用法：
    py scripts/build_cb_feeds_snapshot.py \
        --symbols-file data/cb_candidate_union_v2.txt \
        --start 2018-01-01 --output output/cb_feeds_snapshot.pkl
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.config import load_config  # noqa: E402
from quant_research.data import DataLoader  # noqa: E402

logger = logging.getLogger("build_cb_feeds_snapshot")

REQUIRED_COLS = ("date", "open", "high", "low", "close", "volume")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="构建冻结的可转债 feeds 快照")
    p.add_argument("--config", default=None)
    p.add_argument("--symbols-file", required=True, help="候选并集清单（每行一代码）")
    p.add_argument("--start", required=True)
    p.add_argument("--end", default=None, help="默认今天")
    p.add_argument("--output", required=True)
    p.add_argument("--throttle", type=float, default=0.5)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)
    end = args.end or time.strftime("%Y-%m-%d")

    symbols = [
        line.strip().upper()
        for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not symbols:
        logger.error("清单为空")
        return 2

    feeds: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    cfg = load_config(args.config)
    with DataLoader(cfg) as loader:
        for sym in symbols:
            df = None
            for attempt in range(1, 4):
                try:
                    df = loader.get_convertible_bond_daily(sym, args.start, end)
                    break
                except Exception as e:  # noqa: BLE001 - 单券失败重试
                    logger.warning(f"{sym} 第 {attempt} 次失败: {e}")
                    time.sleep(1.5)
            if df is None or len(df) == 0:
                logger.warning(f"{sym}: 空数据，跳过")
                failures.append(sym)
                continue
            missing = [c for c in REQUIRED_COLS if c not in df.columns]
            if missing:
                logger.error(f"{sym}: 缺列 {missing}，跳过")
                failures.append(sym)
                continue
            feeds[sym] = df
            time.sleep(args.throttle)

    logger.info(f"采集完成: {len(feeds)}/{len(symbols)}（失败 {len(failures)}）")
    if len(feeds) < len(symbols) * 0.9:
        logger.error("成功率低于 90%，中止写入（请检查网络后重跑，缓存会续传）")
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(feeds, out)
    logger.info(f"快照已写入: {out}（{len(feeds)} 只, SHA-256 {_sha256(out)[:16]}...）")
    if failures:
        logger.warning(f"失败清单: {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""构建 / 更新冻结的 ETF feeds 快照（pkl）。

用途：
    run_grid / run_walk_forward 依赖冻结快照保证数据基线可复现。
    本脚本从策略配置的 universe 全量采集日线并写入 pkl，
    并可与旧快照做基线 diff（前复权数据在除权后全序列会平移，
    增量追加不严谨，因此采用全量重采 + diff 报告确认）。

用法：
    py scripts/build_feeds_snapshot.py \
        --output output/feeds_snapshot_v2.pkl \
        --baseline output/feeds_snapshot.pkl \
        --start 2012-01-01 --end 2026-08-17
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.config import load_config, load_strategy_config  # noqa: E402
from quant_research.data import DataLoader  # noqa: E402

logger = logging.getLogger("build_feeds_snapshot")

REQUIRED_COLS = ("date", "open", "high", "low", "close", "volume")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="构建冻结的 ETF feeds 快照")
    p.add_argument("--config", default=None, help="主配置文件路径")
    p.add_argument(
        "--strategy-config",
        default=None,
        help="策略配置 yaml（取其 universe）；默认 config/strategy/etf_momentum.yaml",
    )
    p.add_argument("--symbols", default=None, help="逗号分隔，覆盖策略配置的 universe")
    p.add_argument("--start", required=True, help="采集起始日期（YYYY-MM-DD）")
    p.add_argument("--end", default=None, help="结束日期，默认今天")
    p.add_argument("--output", required=True, help="输出 pkl 路径")
    p.add_argument(
        "--source",
        default=None,
        choices=["akshare", "tushare", "baostock"],
        help="强制指定数据源（绕过 DataLoader 默认链）。ETF 份额分拆复权以东财"
        "（akshare）口径为准：tushare 未正确处理分拆会产生假断点",
    )
    p.add_argument(
        "--baseline",
        default=None,
        help="旧快照 pkl：采集后对共同 symbol 的重叠日期做 close diff 报告（不阻断）",
    )
    p.add_argument(
        "--baseline-tolerance",
        type=float,
        default=0.005,
        help="基线 diff 的相对误差容忍阈值（默认 0.5%，前复权除权会引入正常偏移）",
    )
    return p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _diff_baseline(
    new_feeds: dict[str, pd.DataFrame],
    baseline: dict[str, pd.DataFrame],
    tolerance: float,
) -> int:
    """对新旧快照共同 symbol 的重叠日期收盘价做相对误差对比。

    返回超出容忍阈值的 (symbol, 日期数) 列表的条数；异常本身不阻断写入，
    仅提示人工核对（前复权基准变化、数据源修复都会造成合法偏移）。
    """
    flagged = []
    for sym, new_df in sorted(new_feeds.items()):
        old_df = baseline.get(sym)
        if old_df is None or old_df.empty:
            continue
        merged = pd.merge(
            new_df[["date", "close"]],
            old_df[["date", "close"]],
            on="date",
            suffixes=("_new", "_old"),
        )
        if merged.empty:
            continue
        old = merged["close_old"].abs().replace(0, pd.NA)
        rel = (merged["close_new"] - merged["close_old"]).abs() / old
        bad = rel.dropna() > tolerance
        if bad.any():
            flagged.append((sym, int(bad.sum()), float(rel.max())))
    if flagged:
        logger.warning("基线 diff：以下 symbol 存在超阈值偏移（核对是否为除权/数据源修复）:")
        for sym, n, mx in flagged:
            logger.warning(f"  {sym}: {n} 日超阈值, 最大相对偏差 {mx:.4%}")
    else:
        logger.info("基线 diff：全部共同 symbol 重叠日期收盘价一致（阈值内）")
    return len(flagged)


def _scan_breaks(feeds: dict[str, pd.DataFrame], threshold: float = 0.15) -> list[tuple]:
    """收益率断点扫描：|日收益|>threshold 的日期（ETF 正常涨跌停为 10%）。

    分拆/折算未复权会在分拆日产生 -50%~-75% 假跳变；真实行情仅
    创业板/科创板 20cm 品种在极端日会超阈值。返回 [(sym, date, ret)]。
    """
    out = []
    for sym, df in sorted(feeds.items()):
        df = df.sort_values("date").reset_index(drop=True)
        r = df["close"].pct_change()
        for i, x in r.items():
            if pd.notna(x) and abs(x) > threshold:
                out.append((sym, df["date"].iloc[i].strftime("%Y-%m-%d"), round(float(x), 3)))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)
    end = args.end or time.strftime("%Y-%m-%d")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        strat_cfg = load_strategy_config(
            args.strategy_config
            or _PROJECT_ROOT / "config" / "strategy" / "etf_momentum.yaml",
            name="etf_momentum",
        )
        symbols = [s.strip().upper() for s in (strat_cfg.universe or [])]
    if not symbols:
        logger.error("universe 为空")
        return 2

    logger.info(f"采集 {len(symbols)} 只: {symbols[0]}~{symbols[-1]}，{args.start} ~ {end}")

    feeds: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    cfg = load_config(args.config)
    with DataLoader(cfg) as loader:
        client = None
        adj = cfg.data.adjust.value  # 与 loader.get_etf_daily 默认口径一致
        if args.source:
            client = next(
                (c for c in loader.chain if c.name == args.source), None
            )
            if client is None:
                logger.error(f"数据链中不存在源: {args.source}")
                return 2
            logger.info(f"强制数据源: {client.name}（adjust={adj}）")
        for sym in symbols:
            df = None
            for attempt in range(1, 4):  # 东财接口偶发断连，重试 3 次
                try:
                    if client is not None:
                        df = client.get_etf_daily(sym, args.start, end, adj)
                        if df is not None and len(df) > 0:
                            from quant_research.data.loader import normalize_ohlc  # type: ignore

                            df = normalize_ohlc(df)
                    else:
                        df = loader.get_etf_daily(sym, args.start, end)
                    break
                except Exception as e:  # 单源失败重试后不拖垮整体
                    logger.warning(f"{sym} 采集第 {attempt} 次失败: {e}")
                    if attempt == 3:
                        logger.error(f"{sym} 采集失败（重试耗尽）")
                    df = None
            if df is None or len(df) == 0:
                logger.warning(f"{sym}: 空数据，跳过")
                failures.append(sym)
                continue
            missing = [c for c in REQUIRED_COLS if c not in df.columns]
            if missing:
                logger.error(f"{sym}: 缺少必要列 {missing}，跳过")
                failures.append(sym)
                continue
            feeds[sym] = df
            logger.info(
                f"{sym}: {len(df)} 行, "
                f"{pd.Timestamp(df['date'].min()).date()} ~ "
                f"{pd.Timestamp(df['date'].max()).date()}"
            )

    if not feeds:
        logger.error("无任何数据采集成功")
        return 1
    if failures:
        logger.warning(f"失败标的 {len(failures)}: {failures}（快照仍写入，但需补采）")

    if args.baseline:
        base_path = Path(args.baseline)
        if base_path.exists():
            baseline = pd.read_pickle(base_path)
            _diff_baseline(feeds, baseline, args.baseline_tolerance)
        else:
            logger.warning(f"基线快照不存在: {base_path}")

    breaks = _scan_breaks(feeds)
    if breaks:
        logger.warning(f"收益率断点 {len(breaks)} 处（|日收益|>15%，核对是否为真实行情）:")
        for sym, d, ret in breaks:
            logger.warning(f"  {sym} @{d}: {ret:+.1%}")
    else:
        logger.info("收益率断点扫描：0 处异常")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    pd.to_pickle(feeds, tmp)
    os.replace(tmp, out)
    logger.info(
        f"快照已写入: {out}（{len(feeds)} 只, SHA-256 {_sha256(out)[:16]}...）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

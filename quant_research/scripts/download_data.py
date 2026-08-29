#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据下载脚本

用法：
    # 下载默认 ETF 池 10 年日线
    python scripts/download_data.py

    # 下载指定标的
    python scripts/download_data.py --symbols 600519.SH,000001.SZ

    # 指定日期范围
    python scripts/download_data.py --start 2010-01-01 --end 2024-12-31

    # 不使用缓存（强制重新下载）
    python scripts/download_data.py --no-cache

    # 仅下载股票基础信息（universe 构建）
    python scripts/download_data.py --basic-only

特性：
1. 默认下载 config/strategy/etf_momentum.yaml 中的 ETF 池
2. 支持多源 fallback
3. Parquet 缓存 + 增量更新
4. 进度日志
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

# 让脚本不依赖 sys.path 时也能 import quant_research
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.config import load_config, load_strategy_config, AppConfig
from quant_research.data import DataLoader


logger = logging.getLogger("download")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="quant_research 数据下载脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--config", "-c",
        default=None,
        help="配置文件路径，默认 config/default.yaml",
    )
    p.add_argument(
        "--symbols", "-s",
        default=None,
        help="标的代码列表，逗号分隔。不指定时下载配置中 strategy.universe",
    )
    p.add_argument(
        "--start",
        default=None,
        help="起始日期 YYYY-MM-DD，默认从 backtest.start 读取",
    )
    p.add_argument(
        "--end",
        default=None,
        help="结束日期 YYYY-MM-DD，默认今天",
    )
    p.add_argument(
        "--adjust",
        default=None,
        choices=["hfq", "qfq", "none"],
        help="复权方式，默认从配置读取",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="禁用缓存，强制重新下载",
    )
    p.add_argument(
        "--basic-only",
        action="store_true",
        help="仅下载股票基础信息（用于 universe 构建）",
    )
    p.add_argument(
        "--index",
        default=None,
        help="下载指数日线，逗号分隔，如 000300.SH,000905.SH",
    )
    p.add_argument(
        "--strategy-config",
        default=None,
        help="策略配置 yaml 路径（读取 universe），如 config/strategy/etf_momentum.yaml",
    )
    p.add_argument(
        "--etf",
        action="store_true",
        help="使用 ETF 专用数据接口下载（如 510300.SH）",
    )
    return p


def _resolve_symbols(cfg: AppConfig, symbols_arg: Optional[str], strat_universe: Optional[list[str]]) -> list[str]:
    if symbols_arg:
        return [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]
    # 优先级：策略 yaml universe > 主配置 universe
    universe = strat_universe or cfg.strategy.universe or []
    if not universe:
        logger.warning("策略 universe 为空且未指定 --symbols，将仅下载沪深 300 作为示例")
        return ["000300.SH"]
    return [s.strip().upper() for s in universe]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # 加载配置
    try:
        cfg = load_config(args.config)
    except FileNotFoundError as e:
        print(f"配置文件不存在: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"配置加载失败: {e}", file=sys.stderr)
        return 2

    # 日期默认
    end = args.end or time.strftime("%Y-%m-%d")
    start = args.start or cfg.backtest.start

    logger.info("=" * 60)
    logger.info(f"quant_research 数据下载")
    logger.info(f"  日期范围: {start} ~ {end}")
    logger.info(f"  复权方式: {args.adjust or cfg.data.adjust.value}")
    logger.info(f"  缓存目录: {cfg.data.cache_dir}")
    logger.info(f"  主数据源: {cfg.data.primary.value}")
    logger.info(f"  Fallbacks: {[s.value for s in cfg.data.fallbacks]}")
    logger.info("=" * 60)

    with DataLoader(cfg) as loader:
        # 1. 股票基础信息（universe 必需）
        if args.basic_only:
            logger.info("仅下载股票基础信息")
            try:
                basic = loader.get_stock_basic(use_cache=not args.no_cache)
                logger.info(f"股票基础信息下载完成: {len(basic)} 条")
                # 简单统计
                if "exchange" in basic.columns:
                    exchange_counts = basic["exchange"].value_counts()
                    for ex, cnt in exchange_counts.items():
                        logger.info(f"  {ex}: {cnt} 只")
                return 0
            except Exception as e:
                logger.error(f"下载股票基础信息失败: {e}", exc_info=True)
                return 1

        # 2. 股票基础信息（universe 验证用）
        try:
            basic = loader.get_stock_basic(use_cache=not args.no_cache)
            logger.info(f"股票基础信息已就绪: {len(basic)} 条")
        except Exception as e:
            logger.warning(f"获取股票基础信息失败（不影响日线下载）: {e}")

        # 3. 指数日线
        if args.index:
            indices = [s.strip().upper() for s in args.index.split(",") if s.strip()]
            for idx_code in indices:
                try:
                    df = loader.get_index_daily(idx_code, start, end)
                    logger.info(f"指数 {idx_code}: {len(df)} 行 [{df['date'].min() if len(df) else 'N/A'} ~ {df['date'].max() if len(df) else 'N/A'}]")
                except Exception as e:
                    logger.error(f"下载指数 {idx_code} 失败: {e}")

        # 4. 个股 / ETF 日线
        strat_universe: Optional[list[str]] = None
        if args.strategy_config:
            try:
                strat_cfg = load_strategy_config(args.strategy_config)
                strat_universe = strat_cfg.universe
            except FileNotFoundError as e:
                logger.error(f"策略配置不存在: {e}")
                return 2

        symbols = _resolve_symbols(cfg, args.symbols, strat_universe)
        logger.info(f"待下载标的数: {len(symbols)}")

        success = 0
        failed: list[tuple[str, str]] = []
        for i, sym in enumerate(symbols, 1):
            try:
                if args.etf:
                    df = loader.get_etf_daily(
                        sym, start, end,
                        adjust=args.adjust,
                        use_cache=not args.no_cache,
                    )
                else:
                    df = loader.get_daily_bars(
                        sym, start, end,
                        adjust=args.adjust,
                        use_cache=not args.no_cache,
                    )
                if len(df) > 0:
                    logger.info(
                        f"[{i}/{len(symbols)}] {sym}: {len(df)} 行 "
                        f"[{df['date'].min().date()} ~ {df['date'].max().date()}]"
                    )
                    success += 1
                else:
                    logger.warning(f"[{i}/{len(symbols)}] {sym}: 空数据")
                    failed.append((sym, "空数据"))
            except Exception as e:
                logger.error(f"[{i}/{len(symbols)}] {sym} 失败: {e}")
                failed.append((sym, str(e)))

        # 5. 汇总
        logger.info("=" * 60)
        logger.info(f"下载完成: 成功 {success}/{len(symbols)}")
        if failed:
            logger.warning(f"失败 {len(failed)} 个:")
            for sym, err in failed:
                logger.warning(f"  {sym}: {err}")
        logger.info("=" * 60)
        return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

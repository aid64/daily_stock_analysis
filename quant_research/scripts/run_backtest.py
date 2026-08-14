#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回测脚本

用法：
    # 单标的 SMA 回测
    python scripts/run_backtest.py --symbol 600519.SH --start 2020-01-01 --end 2024-12-31

    # 多标的
    python scripts/run_backtest.py --symbols 600519.SH,000001.SZ --start 2020-01-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.config import load_config, AppConfig
from quant_research.data import DataLoader
from quant_research.backtest import BacktestEngine
from quant_research.backtest.strategies import SMACross


logger = logging.getLogger("backtest_run")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="quant_research 回测脚本")
    p.add_argument("--config", default=None, help="配置文件路径")
    p.add_argument("--symbol", default="600519.SH", help="单标的代码")
    p.add_argument("--symbols", default=None, help="多标的，逗号分隔")
    p.add_argument("--start", default="2020-01-01", help="起始日期")
    p.add_argument("--end", default=None, help="结束日期，默认今天")
    p.add_argument("--strategy", default="sma", choices=["sma"], help="策略")
    p.add_argument("--fast", type=int, default=5, help="SMA 快线周期")
    p.add_argument("--slow", type=int, default=20, help="SMA 慢线周期")
    p.add_argument("--etf", action="store_true", help="使用 ETF 专用接口")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    import time
    args = build_parser().parse_args(argv)

    cfg = load_config(args.config)
    end = args.end or time.strftime("%Y-%m-%d")

    symbols = []
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = [args.symbol.upper()]

    logger.info(f"回测标的: {symbols}, 日期: {args.start} ~ {end}")

    with DataLoader(cfg) as loader:
        data_feeds = {}
        for sym in symbols:
            try:
                if args.etf:
                    df = loader.get_etf_daily(sym, args.start, end)
                else:
                    df = loader.get_daily_bars(sym, args.start, end)
                if len(df) > 0:
                    data_feeds[sym] = df
                    logger.info(f"{sym}: {len(df)} 行")
                else:
                    logger.warning(f"{sym}: 空数据，跳过")
            except Exception as e:
                logger.error(f"{sym} 数据获取失败: {e}")

        if not data_feeds:
            logger.error("无可用数据，无法回测")
            return 1

        # 运行回测
        engine = BacktestEngine(cfg)
        strategy_params = {"fast_period": args.fast, "slow_period": args.slow}

        result = engine.run(SMACross, strategy_params, data_feeds)

        print()
        print(result.summary())
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())

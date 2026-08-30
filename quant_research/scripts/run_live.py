"""实盘运行入口：PaperBroker 默认 / TDXJKBroker 实盘

用法：
    # 模拟运行（默认，PaperBroker，不依赖实盘 DLL）
    python scripts/run_live.py --mode paper

    # 实盘运行（需要配置 .env 的 TDXJK_* 参数 + 下载 TJyaid.dll）
    python scripts/run_live.py --mode tdxjk

    # 单次调仓
    python scripts/run_live.py --mode paper --once
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.config import load_config
from quant_research.backtest.mwu import build_experts
from quant_research.runner import latest_snapshot_date
from quant_research.trade import (
    PaperBroker,
    TDXJKBroker,
    Executor,
    ExecutorConfig,
    MarketClock,
)
from quant_research.trade.pipeline import LiveMWU


logger = logging.getLogger("run_live")

# 专家需要的最长窗口（MA250 / 动量 60 / RSI2+MA200）→ 取 300 根足够
_WINDOW = 300


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="quant_research 实盘运行脚本")
    p.add_argument(
        "--mode", default="paper", choices=["paper", "tdxjk"],
        help="运行模式：paper 模拟 / tdxjk 实盘（需配置 .env + TJyaid.dll）",
    )
    p.add_argument("--once", action="store_true", help="单次调仓后退出")
    p.add_argument(
        "--interval", type=int, default=3600,
        help="定时轮询间隔（秒），默认 3600（1h）",
    )
    p.add_argument("--capital", type=float, default=10000.0, help="初始资金（模拟模式）")
    p.add_argument("--as-of", default=None,
                   help="参考行情截止日期（默认取快照内最新日期）")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    load_config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )

    # 构建专家（与回测 etf_mwu.yaml 同参数，保证信号一致）
    experts = build_experts({
        "momentum": {"lookback": 60, "top_k": 3, "ma_filter": 250},
        "mean_reversion": {"rsi_period": 2, "rsi_buy": 30, "ma_filter": 200, "top_k": 3},
        "defensive": {"symbol": "511010.SH"},
    })
    mwu = LiveMWU(experts, eta=0.1, loss_scale=0.05, share_alpha=0.02)

    # 构建交易执行引擎
    if args.mode == "paper":
        broker = PaperBroker(initial_cash=args.capital)
        broker.login()
    else:
        import os
        broker = TDXJKBroker(
            qs=os.getenv("TDXJK_QS", ""),
            ip=os.getenv("TDXJK_IP", ""),
            port=int(os.getenv("TDXJK_PORT", "0")),
            account=os.getenv("TDXJK_ACCOUNT", ""),
            password=os.getenv("TDXJK_PASSWORD", ""),
        )
        broker.login()

    executor = Executor(ExecutorConfig(
        broker=broker,
        total_value=args.capital,
        defensive_symbol="511010.SH",
        defensive_lot_size=10,
    ))
    clock = MarketClock()

    logger.info("运行模式: %s, 专家: %s", args.mode, [e.name for e in experts])

    # 预加载行情窗口（Paper 模式复用快照；TDX 模式走 GetQuote 单点行情）。
    # Bug-3 修复：--as-of 未指定时从快照数据推导最新日期（此前硬编码
    # 2026-08-08，快照更新后 paper 模式仍重放旧日期）。
    if args.as_of:
        as_of = pd.Timestamp(args.as_of)
    else:
        raw_feeds = pd.read_pickle(_PROJECT_ROOT / "output" / "feeds_snapshot.pkl")
        as_of = latest_snapshot_date(raw_feeds)
        if as_of is None:
            logger.error("快照中无有效日期，无法确定重放截止日")
            return 1
        logger.info("未指定 --as-of，使用快照最新日期 %s", as_of.date())
    price_series = _load_price_window(as_of)
    if not price_series:
        logger.error("无可用行情数据，请确认 output/feeds_snapshot.pkl 存在")
        return 1

    round_no = 0
    while True:
        round_no += 1
        now = time.localtime()
        logger.info("── 第 %d 轮 (%04d-%02d-%02d %02d:%02d) ──",
                    round_no, now.tm_year, now.tm_mon, now.tm_mday,
                    now.tm_hour, now.tm_min)

        if not clock.is_open():
            logger.info("非交易时段，跳过")
        else:
            # 最新参考价 = 窗口末收盘价（Paper）；TDX 可替换为 GetQuote
            latest = {
                sym: float(closes[-1]) for sym, (closes, _amt) in price_series.items()
            }
            target_weights = mwu.update(price_series)
            action = executor.rebalance(target_weights, latest)
            logger.info("调仓结果: %s - %s", action.kind, action.message)
            logger.info("账户: %s", executor.snapshot())

        if args.once:
            break
        time.sleep(args.interval)

    broker.logout()
    return 0


def _load_price_window(as_of: pd.Timestamp) -> dict[str, tuple]:
    """从快照加载每标的最近 _WINDOW 根收盘价与近 20 日成交额

    返回 {symbol: (closes, amounts)}，与 LiveMWU.update 的输入契约一致。
    """
    snapshot_path = _PROJECT_ROOT / "output" / "feeds_snapshot.pkl"
    if not snapshot_path.exists():
        return {}

    feeds = pd.read_pickle(snapshot_path)
    out: dict[str, tuple] = {}
    for sym, df in feeds.items():
        df = df[df["date"] <= as_of]
        if len(df) < 2:
            continue
        closes = df["close"].to_numpy(dtype=float)[-_WINDOW:]
        if "amount" in df.columns:
            amount = df["amount"].to_numpy(dtype=float)[-min(20, len(df)):]
        else:
            amount = np.full(min(20, len(df)), 1e8)
        out[sym] = (closes, amount)
    return out


if __name__ == "__main__":
    sys.exit(main())
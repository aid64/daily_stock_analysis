"""真实数据双低回测验证（固定快照 universe + 动态价格）"""
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from quant_research.config import load_config
from quant_research.data import DataLoader
from quant_research.backtest import BacktestEngine, ConvertibleDoubleLow
from quant_research.backtest.convertible import compute_double_low_scores

cfg = load_config()
logger = logging.getLogger("cb_backtest")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s", force=True)

with DataLoader(cfg) as loader:
    bond_df = loader.get_convertible_bond_list()
    picks = compute_double_low_scores(bond_df, top_n=8, min_rating="AA-", max_premium=20.0,
                                      as_of="2026-08-15")
    symbols = list(picks["symbol"])
    print("回测标的（今日双低前 8）:", symbols)

    # 下载近半年日线（不复权）
    start, end = "2025-09-01", "2026-08-08"
    feeds = {}
    for sym in symbols:
        try:
            df = loader.get_convertible_bond_daily(sym, start, end)
            if len(df) > 0:
                feeds[sym] = df
                print(f"  {sym}: {len(df)} 行")
            else:
                print(f"  {sym}: 无数据")
        except Exception as e:
            print(f"  {sym}: 失败 {e}")

    if not feeds:
        print("无可用数据")
        sys.exit(1)

    # 可转债免印花税与过户费
    params = {
        "bond_table": picks,
        "top_n": 5,
        "min_rating": "AA-",
        "max_premium": 20.0,
        "rebalance_freq": "M",
        "lot_size": 10,
    }
    engine = BacktestEngine(cfg, commission_overrides={"stamp_duty": 0.0, "transfer_fee": 0.0},
                            logger=logger)
    result = engine.run(ConvertibleDoubleLow, params, feeds)
    print("\n=== 可转债双低回测结果 ===")
    print(result.summary())
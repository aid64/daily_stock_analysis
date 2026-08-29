"""真实可转债快照 → 双低选债演示"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from quant_research.config import load_config
from quant_research.data import DataLoader
from quant_research.backtest.convertible import compute_double_low_scores

cfg = load_config()
with DataLoader(cfg) as loader:
    df = loader.get_convertible_bond_list()
    print("全市场可转债:", len(df))
    picks = compute_double_low_scores(
        df, top_n=10, min_rating="AA-", max_premium=20.0, as_of="2026-08-15"
    )
    print("\n今日双低前 10：")
    cols = [c for c in ("symbol", "name", "price", "premium_rate", "rating", "double_low")
            if c in picks.columns]
    print(picks[cols].to_string(index=False))
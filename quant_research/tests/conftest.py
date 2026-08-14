"""pytest 公共 fixture"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让 tests 目录可以 import quant_research
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """临时缓存目录"""
    p = tmp_path / "cache"
    p.mkdir(exist_ok=True)
    return p


@pytest.fixture
def sample_ohlc_df():
    """样本 OHLC DataFrame（未标准化）"""
    import pandas as pd
    return pd.DataFrame({
        "trade_date": ["20240101", "20240102", "20240103"],
        "open": [10.0, 10.5, 10.3],
        "high": [10.8, 10.9, 10.6],
        "low": [9.9, 10.2, 10.0],
        "close": [10.5, 10.3, 10.4],
        "vol": [100000, 120000, 95000],
        "amount": [1050000, 1236000, 988000],
    })


@pytest.fixture
def sample_stock_basic_df():
    """样本股票基础信息"""
    import pandas as pd
    return pd.DataFrame({
        "ts_code": ["600519.SH", "000001.SZ", "400001.BJ"],
        "name": ["贵州茅台", "平安银行", "退市公司A"],
        "list_date": ["20010827", "19910403", "20100101"],
        "delist_date": [None, None, "20230115"],
        "exchange": ["SSE", "SZSE", "BSE"],
    })

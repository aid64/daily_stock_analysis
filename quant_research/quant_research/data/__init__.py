"""数据层入口

用法：
    from quant_research.config import load_config
    from quant_research.data import DataLoader

    cfg = load_config()
    loader = DataLoader(cfg)
    df = loader.get_daily_bars("510300.SH", "2015-01-01", "2024-12-31")
"""

from .clients.base import AbstractDataClient, DataClientError
from .clients.tushare_client import TushareClient
from .clients.baostock_client import BaostockClient
from .clients.akshare_client import AkshareClient
from .cache import ParquetCache
from .universe import UniverseBuilder
from .loader import DataLoader, build_client_chain, normalize_ohlc
from .normalizer import normalize_ohlc, STANDARD_OHLC_COLUMNS

__all__ = [
    "AbstractDataClient",
    "DataClientError",
    "TushareClient",
    "BaostockClient",
    "AkshareClient",
    "ParquetCache",
    "UniverseBuilder",
    "DataLoader",
    "build_client_chain",
    "normalize_ohlc",
    "STANDARD_OHLC_COLUMNS",
]

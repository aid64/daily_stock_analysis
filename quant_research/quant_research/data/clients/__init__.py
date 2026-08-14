"""数据源 Client 模块

每个 Client 必须实现 AbstractDataClient 接口。
Loader 按 chain 顺序调用，失败 fallback。
"""

from .base import AbstractDataClient, DataClientError
from .tushare_client import TushareClient
from .baostock_client import BaostockClient
from .akshare_client import AkshareClient

__all__ = [
    "AbstractDataClient",
    "DataClientError",
    "TushareClient",
    "BaostockClient",
    "AkshareClient",
]

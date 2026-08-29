# 量化研究项目软件架构深度调研报告

> 调研范围：GitHub 开源项目（qlib / backtrader / vnpy / Lean / QUANTAXIS / NautilusTrader / zvt / rqalpha）、知乎量化社区、聚宽 / 米筐 / 优矿等云端平台、散户 1 万本金手动下单场景。
>
> 输出目标：为 `quant_research` 独立项目输出一份「每个模块该由什么构成、该如何写代码」的可落地工程指南。

---

## 0. 执行摘要

通过对 9 个主流开源量化项目源码目录、知乎 6 份深度调研 JSON、聚宽 / 米筐等云端平台做法的横向比对，得出以下核心结论：

1. **架构共识**：所有成熟项目（qlib / vnpy / Lean / QUANTAXIS）都遵循「数据层 → 策略层 → 回测/执行层 → 分析层 → 风控层」的分层架构，差异主要在抽象粒度和事件驱动方式上。
2. **散户最小可用集合**：1 万本金 + 笔记本 + 手动下单场景下，**不需要 vnpy 这种实盘对接层**，但需要**严格还原 A 股 T+1 / 涨跌停 / 退市股 universe 的回测真实性**，这是回测与实盘偏差的最大来源（知乎多次提及的"幸存者偏差"和"隐形税"）。
3. **数据层是成败关键**：Tushare Pro 单源不足以覆盖退市股和可转债全部字段，必须 Baostock 补退市股 universe，AKShare 补可转债/ETF 行情，建立 **多源 fallback + Parquet 缓存**机制。
4. **避免重复造轮子**：回测引擎建议基于 Backtrader 封装（A股社区资料最丰富、Broker 模型成熟），而不是从零写事件驱动撮合引擎；分析层直接复用 QuantStats / empyrical，不要自研 Sharpe/Calmar 计算。
5. **过拟合防护是工程问题不是数学问题**：Walk-Forward / 参数平原 / 样本外验证必须**固化成可复跑的脚本**而非靠人工记忆，否则 10 年回测一定会过拟合到年化 15%+ 的假象。

---

## 1. 项目定位与硬约束

### 1.1 定位

| 维度 | 取值 |
| --- | --- |
| 用户画像 | 散户，1 万本金，普通券商账户手动下单 |
| 设备 | 单台 Windows 笔记本 |
| 数据源 | Tushare Pro（15000 积分）+ Baostock（免费）+ AKShare（免费） |
| 回测目标 | 年化 15%+，Calmar > 1，最大回撤 < 40%，10 年窗口 |
| 交易频率 | 低频（周 / 月调仓），避免高频冲击成本 |
| 是否独立项目 | 独立为 `quant_research/`，与主分析系统共享通知 API |

### 1.2 硬约束

1. **资金约束**：1 万本金无法分散到 20+ 标的，单标的仓位 20%-30% 是上限，需要小容量策略（可转债、ETF、小市值轮动）。
2. **成本约束**：佣金最低万 1（互联网券商）、印花税单边 0.05%、过户费 0.001%，单笔交易成本至少 0.15%，月度调仓年成本约 1.8%，策略年化必须 > 5% 才有意义。
3. **流动性约束**：1 万本金对流动性影响小，但**手动下单**意味着每个标的买卖耗时 30 秒以上，单日调仓标的 ≤ 10 只。
4. **算力约束**：笔记本 + Python，10 年全 A 股日频回测（5000 股 × 2500 日）需用向量化 + Parquet 缓存，事件驱动逐股回测不可行。

---

## 2. 整体架构设计

### 2.1 七层架构（参考 qlib / backtrader / vnpy 抽象）

```
┌──────────────────────────────────────────────────────────────┐
│  0. Config Layer   Hydra + YAML + Pydantic 校验               │
├──────────────────────────────────────────────────────────────┤
│  1. Data Layer     多源 Client → Normalizer → Cache → Loader  │
├──────────────────────────────────────────────────────────────┤
│  2. Strategy Layer BaseStrategy 抽象 + 信号生成 + 状态机      │
├──────────────────────────────────────────────────────────────┤
│  3. Backtest Layer Backtrader 封装 + A股 Broker + 撮合        │
├──────────────────────────────────────────────────────────────┤
│  4. Risk Layer     仓位管理 + 止损止盈 + 回撤熔断             │
├──────────────────────────────────────────────────────────────┤
│  5. Analyze Layer  QuantStats 报告 + Walk-Forward + 失效检测  │
├──────────────────────────────────────────────────────────────┤
│  6. Notify Layer   复用主项目 bot API（飞书/TG/企微）         │
└──────────────────────────────────────────────────────────────┘
                              ▲
                              │  Jupyter / CLI / Web（可选）
                              ▼
                       notebooks/ + scripts/
```

### 2.2 目录骨架

```
quant_research/
├── config/                    # YAML 配置
│   ├── default.yaml
│   ├── strategy/
│   │   ├── convertible_double_low.yaml
│   │   └── etf_momentum.yaml
│   └── env/
│       ├── dev.yaml
│       └── prod.yaml
├── data/                      # 缓存（不入 git）
│   ├── cache/                 # Parquet
│   ├── universe/              # 标的池（含退市股）
│   └── raw/                   # 原始下载
├── quant_research/            # Python 包
│   ├── __init__.py
│   ├── config/                # 配置加载（Hydra + Pydantic）
│   │   ├── __init__.py
│   │   ├── schema.py          # Pydantic 模型
│   │   └── loader.py
│   ├── data/                  # 数据层
│   │   ├── __init__.py
│   │   ├── clients/
│   │   │   ├── base.py        # AbstractClient
│   │   │   ├── tushare_client.py
│   │   │   ├── baostock_client.py
│   │   │   └── akshare_client.py
│   │   ├── normalizer.py      # 多源字段标准化
│   │   ├── cache.py           # Parquet 读写
│   │   ├── universe.py        # 标的池管理（含退市股还原）
│   │   └── loader.py          # 对外统一入口
│   ├── strategies/            # 策略层
│   │   ├── __init__.py
│   │   ├── base.py            # BaseStrategy
│   │   ├── signals.py         # 通用信号库
│   │   └── impl/
│   │       ├── convertible_double_low.py
│   │       ├── etf_momentum.py
│   │       └── small_cap_rotation.py
│   ├── backtest/              # 回测层
│   │   ├── __init__.py
│   │   ├── engine.py          # Backtrader 封装
│   │   ├── broker.py          # A股佣金/T+1/涨跌停
│   │   ├── slippage.py        # 滑点模型
│   │   └── analyzers.py       # 自定义分析器
│   ├── risk/                  # 风控层
│   │   ├── __init__.py
│   │   ├── position_sizer.py  # 仓位计算
│   │   ├── stop_loss.py       # 止损
│   │   └── portfolio.py       # 组合层风控
│   ├── analyze/               # 分析层
│   │   ├── __init__.py
│   │   ├── report.py          # QuantStats 封装
│   │   ├── walkforward.py     # Walk-Forward
│   │   └── overfit_audit.py   # 过拟合审计
│   └── notify/                # 通知层
│       └── __init__.py        # 复用主项目 bot API
├── notebooks/                 # Jupyter 探索
│   ├── 01_data_quality.ipynb
│   ├── 02_strategy_prototype.ipynb
│   └── 03_walkforward_audit.ipynb
├── scripts/                   # CLI 入口
│   ├── download_data.py
│   ├── run_backtest.py
│   └── walkforward_audit.py
├── tests/
├── .env.example
├── pyproject.toml             # 用 uv / poetry
└── README.md
```

---

## 3. 各模块深度设计

### 3.1 配置层（Config Layer）

#### 3.1.1 设计原则（参考 qlib + Hydra 最佳实践）

| 原则 | 说明 |
| --- | --- |
| 单一真源 | 所有运行参数集中在 `config/`，代码不写死任何参数 |
| 类型校验 | 用 Pydantic 校验配置合法性，启动即失败而非运行中崩溃 |
| 命令行覆盖 | Hydra 支持 `python scripts/run_backtest.py strategy.name=etf_momentum` |
| 环境隔离 | `dev.yaml` / `prod.yaml` 切换数据源、Tushare token、缓存路径 |
| 版本化 | 配置随 git 一起管理，每个回测结果绑定 commit hash |

#### 3.1.2 Pydantic Schema 实现指南

```python
# quant_research/config/schema.py
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional
from enum import Enum

class DataSource(str, Enum):
    TUSHARE = "tushare"
    BAOSTOCK = "baostock"
    AKSHARE = "akshare"

class TushareConfig(BaseModel):
    token: str = Field(..., min_length=20, description="从 .env 注入")
    points: int = Field(15000, ge=2000)
    rate_limit_per_min: int = 200

class DataConfig(BaseModel):
    primary: DataSource = DataSource.TUSHARE
    fallbacks: list[DataSource] = [DataSource.BAOSTOCK, DataSource.AKSHARE]
    cache_dir: str = "data/cache"
    cache_format: str = "parquet"
    adjust: str = "hfq"  # 后复权，避免未来函数

class StrategyConfig(BaseModel):
    name: str
    params: dict
    rebalance_freq: str = "W"  # W=周, M=月
    max_positions: int = 5

class BacktestConfig(BaseModel):
    start: str = "2015-01-01"
    end: str = "2024-12-31"
    initial_capital: float = 10000.0
    commission_rate: float = 0.0001  # 万 1
    stamp_duty: float = 0.0005  # 印花税单边
    slippage: float = 0.001  # 0.1%
    allow_t_plus_1: bool = True
    respect_limit_up_down: bool = True

class RiskConfig(BaseModel):
    max_single_position: float = 0.3
    max_drawdown_circuit_breaker: float = 0.15
    stop_loss_atr_mult: float = 2.0

class AppConfig(BaseModel):
    data: DataConfig
    tushare: TushareConfig
    strategy: StrategyConfig
    backtest: BacktestConfig
    risk: RiskConfig
```

#### 3.1.3 Hydra Loader 实现指南

```python
# quant_research/config/loader.py
import hydra
from omegaconf import OmegaConf
from .schema import AppConfig

def load_config(cfg_path: str = "config/default.yaml") -> AppConfig:
    """加载 YAML → dict → Pydantic 校验 → AppConfig"""
    raw = OmegaConf.load(cfg_path)
    raw_dict = OmegaConf.to_container(raw, resolve=True)
    return AppConfig(**raw_dict)

# 入口
@hydra.main(config_path="../../config", config_name="default", version_base=None)
def main(cfg):
    app_cfg = load_config(...)
    ...
```

**社区依据**：qlib 用类似的 `qlib.config.C` 全局配置；vnpy 用 `.json` 文件 + dataclass；Hydra 是学术界 / 机器学习项目主流做法（参见知乎「量化框架对比」调研 round2）。

---

### 3.2 数据层（Data Layer）

#### 3.2.1 核心挑战（知乎调研汇总）

1. **幸存者偏差**：数据商只提供"现存股票"历史数据，退市股会被自动剔除，导致小市值策略回测虚高（知乎"聚宽最传奇策略"案例）。
2. **未来函数**：财报数据必须按 **PIT（Point-in-Time）对齐**，使用 `ann_date` 而非 `end_date`；复权用后复权（hfq）避免信号前视。
3. **字段差异**：Tushare 字段 `ts_code` / Baostock 字段 `code` / AKShare 字段 `symbol`，需要统一 normalizer。
4. **限流**：Tushare 15000 积分约 500 次/分钟，Baostock 单连接串行，需要并发控制 + 重试 + 退避。

#### 3.2.2 抽象接口设计

```python
# quant_research/data/clients/base.py
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd

class AbstractDataClient(ABC):
    """统一数据源接口，所有 client 必须实现"""

    @abstractmethod
    def get_daily_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        """日 K 线，列：date, open, high, low, close, volume, amount"""

    @abstractmethod
    def get_stock_basic(self) -> pd.DataFrame:
        """股票基础信息，含上市/退市日期"""

    @abstractmethod
    def get_trade_calendar(self, start: str, end: str) -> list[str]:
        """交易日历"""

    @abstractmethod
    def get_index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        """指数日线"""
```

#### 3.2.3 Tushare Client 实现要点

```python
# quant_research/data/clients/tushare_client.py
import tushare as ts
import time
import pandas as pd
from datetime import datetime
from .base import AbstractDataClient

class TushareClient(AbstractDataClient):
    def __init__(self, token: str, rate_limit_per_min: int = 200):
        ts.set_token(token)
        self.pro = ts.pro_api()
        self._min_interval = 60.0 / rate_limit_per_min
        self._last_call = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.time()

    def get_daily_bars(self, symbol, start, end, adjust="hfq"):
        self._throttle()
        # 用 pro_bar 而非 daily，支持复权
        df = ts.pro_bar(
            ts_code=symbol,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adj=adjust,
        )
        df = df.rename(columns={"trade_date": "date", "vol": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def get_stock_basic(self):
        # list_status='D' 包含退市股，list_status='L' 仅现存
        all_stocks = []
        for status in ("L", "D", "P"):  # 上市/退市/暂停
            self._throttle()
            df = self.pro.stock_basic(list_status=status)
            all_stocks.append(df)
        return pd.concat(all_stocks, ignore_index=True)
```

**关键点**：`stock_basic(list_status='D')` 是退市股还原的关键，**Tushare 退市股数据有限**，需用 Baostock 补全退市前的日线。

#### 3.2.4 Baostock 退市股补全

```python
# quant_research/data/clients/baostock_client.py
import baostock as bs
import pandas as pd

class BaostockClient(AbstractDataClient):
    def __init__(self):
        bs.login()

    def get_daily_bars(self, symbol, start, end, adjust="hfq"):
        # symbol 转换：600519.SH → sh.600519
        bs_code = self._convert(symbol)
        flag = "2" if adjust == "hfq" else "1" if adjust == "qfq" else "3"
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start, end_date=end,
            frequency="d", adjustflag=flag,
        )
        # Baostock 优势：包含已退市股的历史日线
        return self._rs_to_df(rs)
```

**社区依据**：知乎调研 round3 多份回答指出 Baostock 是免费源中**唯一完整保留退市股历史数据**的，这对小市值策略回测真实性至关重要。

#### 3.2.5 多源 Fallback 与 Normalizer

```python
# quant_research/data/loader.py
from typing import Optional
import pandas as pd
from .clients.tushare_client import TushareClient
from .clients.baostock_client import BaostockClient
from .clients.akshare_client import AkshareClient
from .cache import ParquetCache

class DataLoader:
    """统一数据入口，按优先级 fallback"""

    def __init__(self, config):
        self.clients = self._build_chain(config)
        self.cache = ParquetCache(config.data.cache_dir)

    def _build_chain(self, config):
        chain = []
        if "tushare" in config.data.primary:
            chain.append(TushareClient(config.tushare.token))
        chain.append(BaostockClient())
        chain.append(AkshareClient())
        return chain

    def get_daily_bars(self, symbol, start, end, adjust="hfq"):
        # 1. 查缓存
        cached = self.cache.get(symbol, start, end, adjust)
        if cached is not None:
            return cached

        # 2. 按 chain 顺序尝试，失败 fallback
        last_err = None
        for client in self.clients:
            try:
                df = client.get_daily_bars(symbol, start, end, adjust)
                if df is not None and len(df) > 0:
                    df = self._normalize(df)
                    self.cache.put(symbol, df, adjust)
                    return df
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"All data sources failed for {symbol}: {last_err}")

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """统一列名、单位、复权标记"""
        return df[["date", "open", "high", "low", "close", "volume", "amount"]]
```

#### 3.2.6 退市股 Universe 还原（关键反幸存者偏差）

```python
# quant_research/data/universe.py
from datetime import datetime
import pandas as pd

class UniverseBuilder:
    """还原历史时点的真实可投资标的池"""

    def __init__(self, data_loader):
        self.loader = data_loader

    def build_point_in_time_universe(
        self,
        date: str,
        filters: dict | None = None,
    ) -> list[str]:
        """
        返回该日期可买入的所有股票：
        - 已上市（list_date <= date）
        - 未退市（delist_date > date 或为空）
        - 非 ST（可选）
        - 非涨跌停（可选，需调日线确认）
        """
        basic = self.loader.get_stock_basic()
        date_dt = pd.to_datetime(date)
        mask = (
            (basic["list_date"] <= date_dt)
            & (
                basic["delist_date"].isna()
                | (basic["delist_date"] > date_dt)
            )
        )
        if filters and filters.get("exclude_st"):
            mask &= ~basic["name"].str.contains("ST", na=False)
        return basic.loc[mask, "ts_code"].tolist()
```

**社区依据**：知乎"我解剖了聚宽最传奇策略"明确指出"退市的小盘股在回测数据里经常不存在"，这是小市值策略失效的核心原因之一。本模块的 PIT universe 是反幸存者偏差的工程实现。

#### 3.2.7 Parquet 缓存设计

```python
# quant_research/data/cache.py
import pyarrow.parquet as pq
import pyarrow as pa
import pandas as pd
from pathlib import Path

class ParquetCache:
    """按 symbol + adjust 分区存储，支持增量更新"""

    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, symbol: str, adjust: str) -> Path:
        # 按交易所分区，避免单目录文件过多
        exchange = symbol.split(".")[-1].lower()
        return self.root / exchange / adjust / f"{symbol}.parquet"

    def get(self, symbol, start, end, adjust) -> pd.DataFrame | None:
        p = self._path(symbol, adjust)
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        return df[(df["date"] >= start) & (df["date"] <= end)]

    def put(self, symbol, df, adjust):
        p = self._path(symbol, adjust)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 增量合并：已有则 append + dedup
        if p.exists():
            old = pd.read_parquet(p)
            df = pd.concat([old, df]).drop_duplicates("date").sort_values("date")
        df.to_parquet(p, compression="zstd")
```

**为什么 Parquet 不 SQLite**：列式存储读取 OHLC 5 列快 5-10 倍；无需数据库进程；知乎调研中 qlib、vectorbt 都用 Parquet。

---

### 3.3 策略层（Strategy Layer）

#### 3.3.1 设计原则

| 原则 | 来源 |
| --- | --- |
| 策略无状态、参数化 | backtrader `Strategy` 基类 |
| 信号生成与订单执行分离 | qlib `Signal` ↔ `Strategy` 分离 |
| 事件钩子（next/start/stop） | backtrader 标准 |
| 日志可观测 | vnpy `BaseEngine` 日志规范 |
| 状态机（避免重复信号） | zvt 量价策略实践 |

#### 3.3.2 BaseStrategy 基类

```python
# quant_research/strategies/base.py
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd
import logging
from dataclasses import dataclass, field

@dataclass
class Signal:
    symbol: str
    direction: str  # "long" / "short" / "close"
    strength: float = 1.0
    reason: str = ""
    metadata: dict = field(default_factory=dict)

class BaseStrategy(ABC):
    """所有策略的抽象基类"""

    name: str = "base"

    def __init__(self, params: dict, logger: Optional[logging.Logger] = None):
        self.params = params
        self.logger = logger or logging.getLogger(self.name)
        self._state = {}  # 内部状态，避免重复信号

    @abstractmethod
    def generate_signals(self, date: pd.Timestamp, universe: list[str], data: pd.DataFrame) -> list[Signal]:
        """
        在 date 当日根据 data（multi-index DataFrame）生成信号。
        data 索引：field × symbol × date
        必须只使用 date 当日及之前的数据。
        """

    def on_bar(self, date, signal, position):
        """事件钩子：信号生成后、订单发出前，可修改或拒绝"""
        return signal

    def on_fill(self, fill):
        """事件钩子：订单成交后回调"""
        pass

    def reset(self):
        """Walk-Forward 切片时重置状态"""
        self._state.clear()
```

#### 3.3.3 策略实现示例：可转债双低轮动

```python
# quant_research/strategies/impl/convertible_double_low.py
import pandas as pd
from ..base import BaseStrategy, Signal

class ConvertibleDoubleLow(BaseStrategy):
    """可转债双低轮动（改良版）

    双低 = 价格 + 溢价率 × 100
    1. 排除已发强赎提示
    2. 排除剩余期限 < 0.5 年
    3. 排除信用评级 < AA-
    4. 按双低值升序取前 N
    """
    name = "convertible_double_low"

    def generate_signals(self, date, universe, data):
        n = self.params.get("top_n", 5)
        min_rating = self.params.get("min_rating", "AA-")
        exclude_force_redeem = self.params.get("exclude_force_redeem", True)

        # data 包含：price, premium_rate, force_redeem, rating, maturity
        snapshot = data.xs(date, level="date")
        snapshot = snapshot[snapshot["rating"] >= min_rating]
        if exclude_force_redeem:
            snapshot = snapshot[~snapshot["force_redeem"]]
        snapshot = snapshot[snapshot["maturity"] > 0.5]
        snapshot["double_low"] = snapshot["price"] + snapshot["premium_rate"] * 100

        top = snapshot.nsmallest(n, "double_low")
        return [
            Signal(
                symbol=sym,
                direction="long",
                strength=1.0 / n,
                reason=f"double_low={row.double_low:.2f}",
            )
            for sym, row in top.iterrows()
        ]
```

**社区依据**：知乎调研中"双低轮动"是最多被讨论的散户策略，原始版本年化约 20% 但 2022 年后失效，改良点（信用过滤 / 强赎剔除 / 期限过滤）来自知乎 round1 调研。

#### 3.3.4 ETF 动量轮动

```python
# quant_research/strategies/impl/etf_momentum.py
import pandas as pd
from ..base import BaseStrategy, Signal

class ETFMomentum(BaseStrategy):
    """ETF 动量轮动 + 双动量过滤

    1. 计算过去 N 日收益率（动量）
    2. 双动量：绝对动量 > 0 且 相对动量排名前 K
    3. 均线过滤：当前价 > MA250
    4. 等权配置
    """
    name = "etf_momentum"

    def generate_signals(self, date, universe, data):
        lookback = self.params.get("lookback", 60)
        top_k = self.params.get("top_k", 3)
        ma_filter = self.params.get("ma_filter", 250)

        closes = data["close"].unstack("symbol")  # date × symbol
        if date not in closes.index:
            return []
        loc = closes.index.get_loc(date)
        if loc < max(lookback, ma_filter):
            return []

        ret = closes.iloc[loc] / closes.iloc[loc - lookback] - 1
        ma = closes.iloc[loc - ma_filter + 1 : loc + 1].mean()
        price_now = closes.iloc[loc]

        # 双动量
        candidates = ret[(ret > 0) & (price_now > ma)]
        top = candidates.nlargest(top_k)
        return [
            Signal(symbol=sym, direction="long", strength=1.0 / top_k,
                   reason=f"mom={ret[sym]:.2%}")
            for sym in top.index
        ]
```

---

### 3.4 回测层（Backtest Layer）

#### 3.4.1 事件驱动 vs 向量化（社区调研结论）

| 维度 | 事件驱动（Backtrader/vnpy） | 向量化（vectorbt/empyrical） |
| --- | --- | --- |
| 真实性 | 高（逐 bar 撮合） | 低（假设全可成交） |
| 速度 | 慢（10 年单股约 5-10 秒） | 快（10 年全 A 股 < 1 秒） |
| T+1 / 涨跌停 | 易实现 | 难实现 |
| 复杂策略（止损/加仓） | 友好 | 需 vectorize 技巧 |
| 调试 | 直观 | 反直觉 |

**结论**：1 万本金 + 周月调仓 + ≤5 标的场景，**Backtrader 事件驱动足够**，速度可接受（10 年单策略 < 30 秒）。vectorbt 留作参数扫描阶段使用。

#### 3.4.2 Backtrader A 股 Broker 实现

```python
# quant_research/backtest/broker.py
import backtrader as bt
from datetime import date

class AShareCommission(bt.CommInfoBase):
    """A股佣金 + 印花税 + 过户费"""

    params = (
        ("commission", 0.0001),    # 双边万 1
        ("stamp_duty", 0.0005),    # 卖出单边 0.05%
        ("transfer_fee", 0.00002), # 双边 0.002%
        ("min_commission", 5.0),   # 单笔最低 5 元
    )

    def _getcommission(self, size, price, pseudoexec):
        amount = abs(size * price)
        comm = max(amount * self.p.commission, self.p.min_commission)
        stamp = amount * self.p.stamp_duty if size < 0 else 0
        transfer = amount * self.p.transfer_fee
        return comm + stamp + transfer


class TPlus1Broker(bt.brokers.BackBroker):
    """T+1 限制：买入当日不能卖出"""

    def _submit(self, order):
        if order.is_sell():
            # 检查持仓中是否有 T 日之前的股票
            position = self._positions.get(order.data._name)
            if position is None or position.today_bought > 0:
                order.reject()
                return
        super()._submit(order)


class LimitUpDownFilter:
    """涨跌停过滤：涨停不能买、跌停不能卖"""

    def __init__(self, limit_pct: float = 0.1):
        self.limit_pct = limit_pct

    def check(self, order, bar):
        prev_close = bar.close_prev
        change = (bar.close - prev_close) / prev_close
        if order.is_buy() and change >= self.limit_pct - 0.001:
            return False  # 涨停无法买入
        if order.is_sell() and change <= -self.limit_pct + 0.001:
            return False  # 跌停无法卖出
        return True
```

#### 3.4.3 引擎封装

```python
# quant_research/backtest/engine.py
import backtrader as bt
import pandas as pd
from .broker import AShareCommission, TPlus1Broker
from .slippage import FixedSlippage

class BacktestEngine:
    def __init__(self, config):
        self.config = config
        self.cerebro = bt.Cerebro()
        self._setup_broker()
        self._setup_analyzers()

    def _setup_broker(self):
        cfg = self.config.backtest
        self.cerebro.broker = TPlus1Broker()
        self.cerebro.broker.setcash(cfg.initial_capital)
        self.cerebro.broker.addcommissioninfo(AShareCommission())
        self.cerebro.broker.set_slippage_perc(cfg.slippage)

    def _setup_analyzers(self):
        self.cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe",
                                  timeframe=bt.TimeFrame.Days, annualize=True)
        self.cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        self.cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
        self.cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
        self.cerebro.addanalyzer(bt.analyzers.PyFolio, _name="pyfolio")

    def run(self, strategy_cls, params, data_feeds: dict):
        for symbol, df in data_feeds.items():
            feed = bt.feeds.PandasData(dataname=df, name=symbol)
            self.cerebro.adddata(feed, name=symbol)
        self.cerebro.addstrategy(strategy_cls, **params)
        results = self.cerebro.run()
        return self._extract_metrics(results[0])

    def _extract_metrics(self, strat):
        return {
            "sharpe": strat.analyzers.sharpe.get_analysis()["sharperatio"],
            "max_drawdown": strat.analyzers.drawdown.get_analysis()["max"]["drawdown"],
            "annual_return": strat.analyzers.returns.get_analysis()["rnorm100"],
            "trades": strat.analyzers.trades.get_analysis(),
        }
```

**社区依据**：Backtrader 是知乎调研 round2 中提及最多的开源框架（A股资料丰富），其 Broker 抽象允许自定义佣金/T+1。vnpy 更适合实盘对接，1 万本金手动下单场景用不上。

---

### 3.5 风控层（Risk Layer）

#### 3.5.1 三层仓位法（社区共识 + 1 万本金适配）

```
总仓位 100%
├── 底仓 50%      ← 长期持有（ETF / 红利低波）
├── 机动仓 30%    ← 策略信号（可转债 / 动量轮动）
└── 现金 20%      ← 应急/补仓
```

#### 3.5.2 PositionSizer 实现

```python
# quant_research/risk/position_sizer.py
import pandas as pd
import numpy as np

class ThreeTierPositionSizer:
    """三层仓位法 + ATR 波动率倒数"""

    def __init__(self, total_capital: float, config: dict):
        self.capital = total_capital
        self.tiers = {
            "base": config.get("base_ratio", 0.5),
            "tactical": config.get("tactical_ratio", 0.3),
            "cash": config.get("cash_ratio", 0.2),
        }
        self.risk_per_trade = config.get("risk_per_trade", 0.01)  # 单笔 1%

    def size_by_atr(self, symbol, atr: float, entry_price: float, stop_price: float) -> float:
        """
        ATR 波动率倒数仓位：
        仓位 = (总资金 × 风险比例) / (入场价 - 止损价)
        止损价 = 入场价 - N × ATR
        """
        risk_amount = self.capital * self.risk_per_trade
        per_share_risk = abs(entry_price - stop_price)
        if per_share_risk <= 0:
            return 0.0
        shares = risk_amount / per_share_risk
        # 单笔上限：机动仓的 1/3
        max_shares = (self.capital * self.tiers["tactical"] / 3) / entry_price
        return min(shares, max_shares)

    def size_equal_weight(self, n_positions: int, tier: str = "tactical") -> float:
        tier_capital = self.capital * self.tiers[tier]
        return tier_capital / n_positions
```

#### 3.5.3 止损与回撤熔断

```python
# quant_research/risk/stop_loss.py
class ATRStopLoss:
    def __init__(self, atr_mult: float = 2.0, trailing: bool = True):
        self.atr_mult = atr_mult
        self.trailing = trailing
        self._highest = {}  # 用于移动止损

    def update(self, symbol, high, atr):
        if self.trailing:
            self._highest[symbol] = max(self._highest.get(symbol, high), high)

    def get_stop(self, symbol, atr) -> float:
        if symbol not in self._highest:
            return None
        return self._highest[symbol] - self.atr_mult * atr


# quant_research/risk/portfolio.py
class DrawdownCircuitBreaker:
    """组合层回撤熔断：触发后强制减仓至 30%"""

    def __init__(self, threshold: float = 0.15):
        self.threshold = threshold
        self.peak_equity = 0.0
        self.triggered = False

    def check(self, current_equity: float) -> dict:
        self.peak_equity = max(self.peak_equity, current_equity)
        drawdown = 1 - current_equity / self.peak_equity
        if drawdown >= self.threshold:
            self.triggered = True
            return {"action": "reduce_to", "ratio": 0.3}
        return {"action": "none"}
```

**社区依据**：知乎调研中"凯利公式 + ATR 止损"被多个回答提及；三层仓位法是 1 万本金小资金场景的工程化适配（避免一次性满仓风险）。

---

### 3.6 分析层（Analyze Layer）

#### 3.6.1 QuantStats 报告集成

```python
# quant_research/analyze/report.py
import quantstats as qs
import pandas as pd
from pathlib import Path

class PerformanceReport:
    def __init__(self, returns: pd.Series, benchmark: pd.Series | None = None):
        self.returns = returns
        self.benchmark = benchmark

    def generate_html(self, output_path: str, title: str = "Strategy Report"):
        qs.reports.html(
            self.returns,
            benchmark=self.benchmark,
            title=title,
            output=output_path,
            strategy_title=title,
        )

    def get_metrics(self) -> dict:
        return {
            "sharpe": qs.stats.sharpe(self.returns),
            "calmar": qs.stats.calmar(self.returns),
            "max_drawdown": qs.stats.max_drawdown(self.returns),
            "cagr": qs.stats.cagr(self.returns),
            "sortino": qs.stats.sortino(self.returns),
            "volatility": qs.stats.volatility(self.returns),
        }
```

#### 3.6.2 Walk-Forward 验证（反过拟合核心）

```python
# quant_research/analyze/walkforward.py
import pandas as pd
import numpy as np

class WalkForwardValidator:
    """滚动窗口验证：训练 → 样本外测试 → 滑动"""

    def __init__(self, train_window: int = 504, test_window: int = 63, step: int = 63):
        self.train_window = train_window  # 2 年训练
        self.test_window = test_window    # 3 个月测试
        self.step = step

    def split(self, dates: pd.DatetimeIndex):
        """生成 (train_start, train_end, test_start, test_end) 序列"""
        n = len(dates)
        start = 0
        while start + self.train_window + self.test_window <= n:
            train = dates[start : start + self.train_window]
            test = dates[start + self.train_window : start + self.train_window + self.test_window]
            yield train, test
            start += self.step

    def validate(self, strategy_factory, data: pd.DataFrame) -> dict:
        """对每个切片跑训练+测试，返回样本内/外收益对比"""
        results = []
        for train_idx, test_idx in self.split(data.index.unique()):
            train_data = data[data.index.get_level_values("date").isin(train_idx)]
            test_data = data[data.index.get_level_values("date").isin(test_idx)]
            # 1. 训练：在 train_data 上找最优参数
            best_params = self._optimize(strategy_factory, train_data)
            # 2. 测试：用 best_params 在 test_data 跑回测
            strat = strategy_factory(best_params)
            test_returns = self._run(strat, test_data)
            results.append({
                "train_period": (train_idx[0], train_idx[-1]),
                "test_period": (test_idx[0], test_idx[-1]),
                "best_params": best_params,
                "test_return": test_returns.sum(),
            })
        return results
```

#### 3.6.3 过拟合审计 6 步法

```python
# quant_research/analyze/overfit_audit.py
class OverfitAudit:
    """
    过拟合审计 6 步法（社区共识）：
    1. 样本内/外收益比 < 2（> 2 警惕）
    2. 参数平原检验：参数 ±20% 收益变化 < 30%
    3. 多周期鲁棒性：周/月调仓收益相关性 > 0.7
    4. Walk-Forward 一致性：> 60% 切片正收益
    5. 交易成本敏感性：扣 2 倍成本后仍盈利
    6. 不同市场环境表现：牛/熊/震荡分别验证
    """

    def __init__(self, backtest_func):
        self.backtest = backtest_func

    def run_all(self, strategy_cls, base_params: dict, data) -> dict:
        return {
            "sample_in_out_ratio": self._step1(strategy_cls, base_params, data),
            "param_plateau": self._step2(strategy_cls, base_params, data),
            "period_robustness": self._step3(strategy_cls, base_params, data),
            "walkforward_consistency": self._step4(strategy_cls, base_params, data),
            "cost_sensitivity": self._step5(strategy_cls, base_params, data),
            "regime_robustness": self._step6(strategy_cls, base_params, data),
        }
```

#### 3.6.4 策略失效检测

```python
# 策略监控：实盘后每月评估
class StrategyDecayDetector:
    """策略失效信号：连续 N 个月跑输基准"""

    def __init__(self, threshold_months: int = 6, underperform_pct: float = -0.02):
        self.threshold = threshold_months
        self.underperform = underperform_pct

    def check(self, live_returns: pd.Series, benchmark: pd.Series) -> dict:
        excess = (live_returns - benchmark).rolling(self.threshold).sum()
        decay = excess < self.underperform
        return {
            "is_decaying": decay.iloc[-1],
            "consecutive_underperform": int(decay.sum()),
            "latest_excess": float(excess.iloc[-1]),
        }
```

**社区依据**：知乎"我解剖了聚宽最传奇策略"详细分析了策略失效信号，"年化收益连续三个阶段单调递减"是失效的强信号；Walk-Forward 是所有量化教科书的标准做法。

---

### 3.7 通知层（Notify Layer）

#### 3.7.1 复用主项目 Bot API

```python
# quant_research/notify/__init__.py
import requests
import os

class Notifier:
    """复用主项目 daily_stock_analysis 的 bot API"""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.getenv("DSA_BOT_API", "http://localhost:8000")

    def send_signal(self, signals: list, strategy_name: str):
        payload = {
            "type": "quant_signal",
            "strategy": strategy_name,
            "signals": [s.__dict__ for s in signals],
            "timestamp": pd.Timestamp.now().isoformat(),
        }
        resp = requests.post(
            f"{self.base_url}/api/v1/bot/notify",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
```

**优势**：复用主项目已有的飞书 / Telegram / 企业微信配置，不重复实现多渠道发送。

---

## 4. 社区 / GitHub 来源汇总

### 4.1 GitHub 开源项目对比

| 项目 | 架构亮点 | 借鉴点 | 局限 |
| --- | --- | --- | --- |
| [qlib](https://github.com/microsoft/qlib) | 配置驱动 + 因子表达式 + Dataset 抽象 | 配置层、数据层、因子框架 | 学习曲线陡，实盘对接弱 |
| [backtrader](https://github.com/mementum/backtrader) | 事件驱动 + Strategy/Broker/Analyzer 三件套 | 回测引擎、A 股 Broker 自定义 | 维护停滞 |
| [vnpy](https://github.com/vnpy/vnpy) | 模块化 + 网关抽象 + 事件引擎 | 实盘对接思路（本项目暂不需要） | 偏实盘，回测弱 |
| [Lean](https://github.com/QuantConnect/Lean) | C# + 算法交易引擎 + 多市场 | 多市场扩展思路 | C# 生态，Python 兼容性一般 |
| [QUANTAXIS](https://github.com/QUANTAXIS/QUANTAXIS) | 全栈 + 数据爬虫 + 回测 + Web | 数据采集思路 | 维护停滞、文档少 |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | Rust + Python 混合 | 高性能架构思路 | 偏实盘高频 |
| [rqalpha](https://github.com/ricequant/rqalpha) | 米筐开源 + 日级回测 | A 股回测细节 | 维护减弱 |
| [zvt](https://github.com/zvicky/zvt) | 量价因子 + 记录器 | 因子计算思路 | 小众 |
| [vectorbt](https://github.com/polaber/vectorbt) | 向量化 + 参数扫描 | 参数扫描、性能 | 不适合复杂策略 |

### 4.2 知乎调研关键来源

| 调研文件 | 核心结论 |
| --- | --- |
| `docs/zhihu_quant_research.json` | 双低轮动、动量轮动、均值回归等经典策略实现细节 |
| `docs/zhihu_deep_research_round1.json` | 散户量化的真实约束：自我欺骗、样本外纪律、1 万本金可行性 |
| `docs/zhihu_deep_research_round2.json` | 聚宽最传奇策略失效案例：壳价值归零、拥挤交易、幸存者偏差 |
| `docs/zhihu_deep_research_round3.json` | Tushare / Baostock / AKShare 数据源对比，退市股数据是 Baostock 独家 |
| `docs/zhihu_targeted_research.json` | 国内散户写量化策略的人数、个人量化是否靠谱 |
| `docs/zhihu_q2_research.json` | 趋势跟踪、均值回归、动量策略实现逻辑 |

### 4.3 关键知乎洞察（影响架构设计）

1. **"回测告诉你的童话"**：滑点 + 冲击成本 + 退市股缺失 → 回测必然虚高，必须工程化对冲。
2. **"策略没变，市场变了"**：聚宽小市值策略 2008-2024 年化从 146% 单调下降到负值，说明**策略生命周期管理**比策略本身更重要。
3. **"幸存者偏差在小市值策略里比大市值严重得多"**：退市股 universe 还原是数据层的硬要求。
4. **"5 千克隆里的人，每个人都在跟你抢同一批股票"**：拥挤度检测应纳入策略监控。

---

## 5. 实施路线图（1 万本金适配）

### 5.1 阶段划分

| 阶段 | 目标 | 验证标准 |
| --- | --- | --- |
| P0 骨架（1 周） | 配置层 + 数据层 + 缓存 | 能下载 10 年 ETF 日线并 Parquet 缓存 |
| P1 第一策略（2 周） | ETF 动量轮动回测跑通 | 10 年回测出 Sharpe / Calmar / 最大回撤 |
| P2 真实性增强（1 周） | A 股 Broker + T+1 + 涨跌停 + 印花税 | 回测与实盘偏差 < 3% |
| P3 第二策略（2 周） | 可转债双低轮动改良版 | 双策略组合夏普 > 单策略 |
| P4 过拟合审计（1 周） | Walk-Forward + 6 步法 | 样本内/外收益比 < 2 |
| P5 实盘验证（持续） | 模拟盘 → 小仓位实盘 | 连续 3 月跑赢基准 |

### 5.2 1 万本金的特殊调整

1. **标的池缩小**：ETF 轮动用 5-10 只宽基 + 行业 ETF；可转债取双低前 5 名。
2. **调仓频率降低**：周度 → 月度，年交易成本从 1.8% 降到 0.45%。
3. **单笔最低佣金**：1 万 × 30% = 3000 元，万 1 佣金 = 0.3 元 < 5 元最低，**实际成本是 5 元**，回测必须用 `min_commission=5`。
4. **手动下单耗时**：单日调仓 ≤ 5 只标的，策略 max_positions 设为 5。

---

## 6. 验证情况

### 6.1 已验证

- 配置层 / 数据层 / 策略层 / 回测层 / 风控层 / 分析层 / 通知层的接口设计与社区主流做法对齐（qlib / backtrader / vnpy 源码目录比对）。
- 数据源 fallback 机制覆盖知乎调研 round3 中提到的 Tushare / Baostock / AKShare 三源。
- 退市股 universe 还原、PIT 对齐、Parquet 缓存等反幸存者偏差工程实现已有明确代码骨架。
- 1 万本金约束下的成本模型（最低佣金 5 元）已纳入 Broker 实现。

### 6.2 未验证项

- ~~代码骨架尚未在本项目实际运行，需后续 P0 阶段落地后跑通。~~ **已验证（P0 + P2）**：配置层、数据层、回测层均已在本机跑通，69 项单元测试全部通过，600519.SH 2020-2024 回测成功输出。
- ~~Backtrader T+1 / 涨跌停的 Broker 子类化在 A 股 2024 注册制后涨跌停规则（主板 10% / 创业板科创板 20%）需要按标的分段处理。~~ **已验证**：`get_limit_pct()` 按标的代码分段返回涨跌停幅度（主板 10% / 创业板 300/301 20% / 科创板 688/689 20% / 北交所 43/83/87 30%），`LimitUpDownFilter` + `TPlus1Broker` 已在回测中验证。
- ~~ETF 专用接口（Tushare fund_daily / AKShare fund_etf_hist_em）和可转债接口（Tushare cb_basic / AKShare bond_zh_cov）已实现但尚未用真实 token 跑通端到端验证（需配置 TUSHARE_TOKEN）。~~ **已验证（P1）**：ETF 端到端跑通（见 6.1），Tushare 通道仍待配置 token 验证，可转债接口留待 P3。
- QuantStats 在中国 A 股日历（含节假日）的年化计算需自定义 trading_days=252。（P3 分析层启用时处理）

### 6.2.1 P1 ETF 动量轮动端到端验证记录（2026-08-15）

**验证内容**：12 只 ETF（3 只宽基 + 9 只行业）约 11.5 年（2013-11 ~ 2026-08，含 250 日均线预热期）真实数据端到端回测，83 项单元测试全部通过。

**数据层发现与修复**（对回测结果影响按严重度排序）：

1. **ETF 份额折算跳变（致命）**：AKShare 东财源（fund_etf_hist_em）在部分网络环境 TLS 持续失败，降级新浪源（fund_etf_hist_sina，不复权）后，ETF 份额拆分/合并产生远超涨跌停的价格跳变（实测 510500 2015-04-15 +248.6%、512480 2026-07-03 -50.7% 等 5 只 6 次），动量/均线计算完全失真（策略收益虚亏 -63.9%）。修复：`adjust_etf_share_split()` 以 30% 阈值检测跳变（真实涨跌停 ≤20%，折算跳变 ≥49%，安全区分）做前复权调整。
2. **ETF 费率错误（中等）**：A 股 ETF 现货免印花税与过户费，引擎原统一按股票费率收取。修复：`BacktestEngine(commission_overrides=...)` 支持按品种覆盖费率。
3. **缓存静默截断（中等）**：Parquet 缓存 get_bars 不校验区间覆盖，请求 2013 年数据静默返回 2015 年起切片，导致均线窗口数据不足、回测第一年完全空转。修复：覆盖校验（容差 10 天）不满足则 miss 重新下载。
4. **数据预热（轻微）**：数据起点 = 回测起点时，ma_filter=250 导致首年无信号。修复：run_backtest.py 自动前移数据加载起点（最长窗口 ×1.7 日历日）。

**修复后回测结果**（初始资金 1 万，佣金万 1 最低 5 元 + 滑点 0.1%，免印花税）：

- 总收益 **-51.0%**，年化 **-5.6%**，夏普 -0.045，最大回撤 **74.7%**（2015 股灾满仓进场 + 2022-2026 震荡市磨损），162 笔交易，胜率 32.1%。
- 年度分解：2015 股灾重创（牛市顶满仓）、2019-2021 牛市 +21%/+25%/+7%（动量有效）、2022-2026 连亏（-9%~-27%，熊市反弹反复止损）。
- 持仓探测验证：均线过滤生效（2022 持仓日占比 36.8%、2024 40.5%，熊市明显减仓），交易规则（T+1、整手、涨跌停）按预期工作。

**结论**：P1 基础设施（数据质量、交易规则、多标的对齐、配置、测试）全部达标；**60 日动量 + 年线过滤参数组在 A 股 12 只 ETF 上长期无效**（A 股动量弱反转强的市场特性 + 小资金最低佣金磨损），符合预期——P1 目标是跑通端到端而非策略盈利，参数优化与策略改良（换手率控制、月度调仓、止损、多窗口动量、可转债双低）归属 P2-P4。

**遗留局限**：新浪源无复权（宽基 ETF 分红少影响有限，行业 ETF 有分红的需注意）；Tushare token 未配置，复权数据通道未验证。

### 6.2.2 P2 参数优化验证记录（2026-08-15）

**验证内容**：ETFMomentum P2 增强（国债 ETF 511010 防御轮动、多窗口动量、波动率调整排序、止损带）+ 参数优化流程（OFAT 单维度扫描 → 组合精调 → 一次性样本外验证）。数据基线用快照冻结（`output/feeds_snapshot.pkl`，13 只 ETF，2013-04 ~ 2026-08），消除晚上市 ETF 反复拉取导致的多源版本漂移。89 项单元测试全部通过。

**本轮新发现的基础设施缺陷与修复**（先于扫描结论，此前所有扫描结果均被污染）：

1. **存续期内数据洞导致 NaN 伪回撤（致命）**：数据源缺失个别交易日（实测 510500 缺 2015-04-13/14、512010 缺 2021-06-25），多标的 union 对齐后产生 NaN bar，Backtrader 对 NaN 收盘价的持仓**按 0 估值**，单日伪回撤达 -95% ~ -98.7%（净值 = 纯现金），次日自动"恢复"。top_k=1/5 时伪回撤直接盖过真实表现。修复：engine 对齐时对标的 `[上市日, 最后交易日]` 区间内的洞前向填充（上市前保持 NaN 给策略层排除晚上市标的），并新增回归测试 `test_in_life_gap_forward_filled`（禁用修复时伪回撤 89.9% 断言失败）。
2. **run_grid.py full 段未切片（严重）**：无 `--split-date` 时 "full" 段直接使用快照全量数据，名义上的"训练段扫描"实际回测到 2026-08（含测试期，未来数据泄漏）。修复：所有段统一按 `[起, 止]` 切片。

**OFAT 扫描结论**（训练段 2013-11 ~ 2021-12，修复后重跑全部 7 个维度）：

- lookback：40 最优（+8.93%，修复前伪影曾使 20/60 排序翻转，可见 NaN 污染之深）
- ma_filter：120 显著优于 200/250（+16.7% vs +8.9%）
- 调仓频率：W +12.6% vs M +10.1%（收益），但 M 交易数仅其 55%
- top_k：2/5 略优于 3，1 最差（集中度风险）
- 止损 -8%：收益持平、回撤 49.3%→46.0%
- 波动率调整排序：收益 +0.6pp 且回撤 46.0%→39.7%（唯一同时改善两端的增强）
- 多窗口动量（(20,40)/(20,60)/(40,60)/(40,120)）：全部劣于单窗口 40

**组合精调**（36 组合，训练段）：`top_k=5 + 月调仓` 族最稳健（6 个邻域参数组合年化全部 ≥9.9%，分布最紧），最优 lb30/ma100/k5/M 达 +16.89%、DD 38.8%、Sharpe 0.759、11.8 笔/年。

**样本外验证（一次性，2022-01 ~ 2026-08，预注册规则：测试段年化 > 0 且训练/测试收益比 < 2）**：

| 候选（均 k5/M/止损-8%/波动率调整） | 训练段年化 | 测试段年化 | 测试段最大回撤 | 胜率 |
| --- | --- | --- | --- | --- |
| lb30/ma120 | +15.36% | **-15.69%** | 60.0% | 20% |
| lb60/ma120 | +13.45% | **-14.94%** | 57.3% | 20% |

**按预注册规则全部否决，调优参数不予采纳**。

**事后归因**（诊断性对照，非调参依据）：

| 配置（2022-01 ~ 2026-08） | 年化 | 最大回撤 |
| --- | --- | --- |
| P1 原始配置（lb60/ma250/k3/W，无防御/止损） | -15.98% | 63.0% |
| lb60/ma120/k5/M 去掉止损与波动率调整 | -13.81% | 60.7% |
| 沪深300ETF 被动持有 | -1.17% | -36.1% |
| 国债 ETF 被动持有 | **+2.42%** | **-1.6%** |

结论：磨损来自**动量轮动本身**——2022-2026 A 股为多次 V 型反转的震荡下行市（2022-04、2022-11、2024-02、2024-09 急涨急跌），动量策略反复在反弹尾部进场、回撤中离场（胜率 20%，盈亏比 0.3），远差于被动持有；防御轮动形同虚设（震荡市总有行业 ETF 短暂通过 ma120+动量过滤，策略持续再入场）。止损与波动率调整仅贡献约 1pp 差异，无法改变结局。

**P2 最终结论**：

1. 参数优化流程按防过拟合纪律执行完毕，结论为**负**：该策略族（月/周度 ETF 动量轮动 + 均线过滤）在测试期样本外完全失效，任何参数化都无法拯救，`etf_momentum.yaml` 维持 P1 保守默认。
2. 测试段（2022-2026）已消耗，后续任何策略迭代**不得**再用该区间调参，只能作为最终验收。
3. P3 方向建议：换策略族（可转债双低轮动、均值回归）或加市场状态门控（宽基指数趋势/波动率识别震荡市，直接持债不参与），而非继续调参。

### 6.2.3 P3/P4 验证记录（2026-08-15）

**验证内容**：P3 策略转向（MWU 多策略融合）+ P4 执行模块（券商接入 / 离散优化下单 / 订单执行引擎 / tdxjk 通达信实盘接口）。124 项单元测试全部通过，PaperBroker 全链路（MWU 权重 → 目标组合 → 整数手分配 → 下单成交 → 账户对账）跑通。

**方法论来源**：知乎《谈量化交易中被严重低估的执行模块》（栀染，174 赞）：
- 要点一 **MWU 策略融合**：机构用 XGBoost/LightGBM 做多因子融合是 batch learning，在 A 股这种分布剧烈漂移（regime switch）的环境下"假设未来重复过去"是自杀；MWU 有 worst-case regret 保证（R_T ≤ 2√(T·ln N)+√(T/2)），唯一超参是学习率 η，长期收敛于事后最优专家。
- 要点二 **离散优化**：连续权重 round 到 100 股在 A 股高价股环境产生巨大 tracking error，本质是背包问题变种。
- 要点三 **拆单**：大单有 market impact（Almgren-Chriss 模型，临时冲击 ∝ √速度），需要 TWAP 拆单。
- **评论区修正**（对 1 万本金散户）："取整那点换手率影响要是对净值有影响，说明规模太小了"（整数规划对小资金是小优化）；"组合优化是真正的 alpha 放大器"（认可策略融合/仓位调度价值）；"执行只有 trade-off 没有最优"（不追求最优执行）。

**P3 落地：MWU 多策略融合（`quant_research/backtest/mwu.py`）**
- 三类专家：`MomentumExpert`（双动量+均线过滤，与 ETFMomentum 复用同一 `momentum_scores`）、`MeanReversionExpert`（RSI(2) 超卖 + MA200 趋势过滤，社区共识参数）、`DefensiveExpert`（国债 ETF）。
- `ETFMWU`（bt.Strategy）与 `LiveMWU`（实盘信号层）共用乘性更新逻辑：按专家纸面收益 `w_i ← w_i(1-η·l_i)` 更新权重，含 **Fixed Share 回拉**（`w_i ← (1-α)·w_i/Z + α/N`）——标准 MWU 只罚不奖，专家权重被长期熊市压到 0 后即使后续最优也无法恢复（2015 股灾 → 2019-21 结构牛实测锁死在国债 99%）；Fixed Share 是 regime switch 的标准修正。
- **发现并修复的执行缺陷**：同一 bar 多个买单按提交顺序撮合，先提交的大额单挤占现金导致后续小额单连环 Margin 拒单（实测拒单率 ~1/3）。修复：买入按目标差额升序提交（小额先成交）。

**回测对比**（13 只 ETF，快照冻结 2026-08-14，统一 2013-11 起点；训练段 2013-11~2021-12、测试段 2022-01 起冷启动）：

| 配置 | 全期总收益 | 训练段(13-21) | 测试段(22-26) | 最大回撤 |
| --- | --- | --- | --- | --- |
| P1 动量单策略（lb60/ma250/k3/W） | -47.7% | +47.7% | **-54.1%** | 72.3% |
| MWU 三专家（Fixed Share α=0.02） | +2.4% | +21.9% | **-12.5%** | 29.5% |

> 注：早前表格动量行用 2015 起点（-2.6%/+32.0%/-43.6%），与 MWU 的 2013-11 起点口径不一致；上表已统一为同一快照、同一分段的 apples-to-apples 对照。动量训练段靠 2015/2019-21 结构牛拿到 +47.7% 总收益，但样本外 2022 起 -54.1%（回撤 72%）；MWU 训练段 +21.9%（被防御/均值回归摊薄），但样本外仅 -12.5%（回撤 29.5%）——MWU 的价值主要在风控而非收益。

诚实结论：MWU 框架本身工作正常（权重随市场动态调整、测试段回撤从 53%→20% 大幅收敛），但**该 ETF 池上动量/均值回归专家纸面收益都跑不赢债券**，Fixed Share 强制回拉后反而拖累组合。缺的是真正有效的风险专家——按 P2 结论，应接入**可转债双低**这类有正期望的策略族，而非继续在 ETF 池内调专家。

**P4 落地：执行模块（`quant_research/trade/`）**

| 模块 | 职责 | 文章要点映射 |
| --- | --- | --- |
| `broker.py` | `AbstractBroker` 统一接口 + `PaperBroker` 模拟账户（T+1/手续费/滑点/涨跌停）+ `TDXJKBroker` 通达信 DLL 接入 | 执行层统一券商抽象 |
| `sizer.py` | `integer_lot_allocate`：目标权重 → 整数手（债券 ETF 10 张/手），买入现金约束等比缩减 | 要点二 离散优化 |
| `executor.py` | `Executor` 先卖后买 + 5% 容忍 + 交易时段风控 + `OrderSplitter` 拆单 + 对账 | 要点三 拆单（预留） |
| `pipeline.py` | `LiveMWU` + `PriceSeries`：把回测专家复用到实时行情 | 策略→执行桥接 |
| `scripts/run_live.py` | paper 模拟（默认）/ tdxjk 实盘入口 | 普通人量化软件入口 |

**tdxjk 通达信接口（TJyaid.dll）调研结论**：
- 9 个导出函数：`Logon`（券商名/服务器 IP/端口/营业部/账号/密码 → ClientID）、`Logoff`、`GetQuote`（五档行情）、`QueryData`（0 资金/1 股份/2 当日委托/3 当日成交/4 可撤单/5 股东代码）、`QueryHistoryData`、`SendOrder`（0 买/1 卖，PriceType 0 限价，1-6 市价）、`CancelOrder`、`Repay`、`GetTradableQuantity`。
- 32 位 DLL，字符串入参 GBK 编码，返回 `\n`/`\t` 分隔表格，Python 用 ctypes `WinDLL` 加载。
- 登录需真实券商交易服务器地址（可从官方 tdxw.exe 演示版获知），试用包需联系站方客服获取。
- `TDXJKBroker` 已按文档完整绑定并实现表格解析（资金/股份/行情/委托编号），未加载 DLL 时构造不报错、login() 才抛错，保证无实盘文件机器 CI 可过。

**Paper 全链路实测**（初始 1 万，2026-08-08 快照，交易时段注入）：MWU 选出 512010/512480/588000 三只动量 ETF（权重各 0.111）+ 511010 国债防御（0.333），Executor 整数手分配后 4 笔委托全部成交（含 511010 按 10 张/手申报 30 张），账户现金与持仓对账正确。

**P3/P4 结论**：
1. MWU + Fixed Share 的执行框架已就绪，可测试、可复现；策略盈利取决于专家质量，下一步 P5 应接入可转债双低专家（社区共识双低公式：价格 + 转股溢价率排序）。
2. 执行模块打通了"策略信号 → 券商账户"的完整链路，paper 模式可全链路演练，tdxjk 实盘仅差 DLL + 真实账户 + .env 参数。
3. 知乎搜索 API（developer.zhihu.com）的 access secret 验证失败（Code 20001），当前调研依赖公开页面抓取；若需恢复该通道需重新核发 secret。

**社区调研佐证（2026-08-15，知乎 5 关键词 × 10 篇高赞文章 + 评论区）**：
- **可转债双低（P5 首选方向）**：双低值=转债价格+转股溢价率×100；20 只轮动、每只 5% 仓位、双低前 10% 买入 / 后 20% 卖出、半月或月调仓；排除溢价率>20%、价格高于当日中位数；130 元以上回落 1% 或强赎卖出；可转债佣金极低（沪市万 0.05，评论区券商从业者实报），大幅缓解 1 万本金佣金磨损。评论区警示：双低值低≠价格低（近期银行转债处高位）、排除即将到期/无想象力的标的。
- **ETF 轮动失效是社区共识**：国金证券智量工坊《为什么很多人量化 ETF 轮动长期超额很低甚至为负》（频繁轮动反而不如一直持有）——直接佐证本项目 P2 负结论；DeepSeek 回测 6000%+ 实测是未来函数（取数 end_date 设为当日收盘）+ 固定候选池幸存者偏差（验证法 `avoid_future_data`），警示 AI 生成策略的回测陷阱。
- **散户量化四要素**（小栗子量化 102 赞）：低频不盯盘、交易时间不敏感、方便实操、收益反馈好；均值回归被定位为"散户量化、小资金流派"，但注册制后退市归零是边界（均值不存在）。
- **成本是散户量化生命线**：1 万本金 5 只×2000 元调仓一次手续费约 75 元，年化 20% 赚 2000 扣完手续费只剩 1100（评论区）——支持降低调仓频率 + 低佣账户（转债/ETF 免印花税）。
- **执行模块不卷拆单**（《执行模块》高赞评论）："执行模块比量化策略还复杂"（4 赞）、"冲击成本建模拆单没啥必要强行上 RL"（4 赞）——本项目的 OrderSplitter 默认 1 段、资金放大后再启用是正确的；"alpha 预测不准，取整换手率对净值有影响说明规模太小"（高赞）——整数规划对 1 万本金是小优化，保持现有 lot 取整即可。

### 6.2.4 P5 可转债双低验证记录（2026-08-16）

**验证内容**：P5 可转债双低轮动策略落地（数据通道 + 双低评分 + 回测策略 + run_backtest 接入）。

**数据通道验证**：AKShare `bond_zh_cov` 当日全市场快照拉取成功（1049 只可转债，含债现价/转股溢价率/信用评级/上市日期）；Tushare `cb_daily` 可转债日线拉取成功（候选 8 只 2026-01~08 各 187 行）。已验证无 token 环境下 AKShare 通道可用。

**双低评分验证**：`compute_double_low_scores` 按"价格+溢价率×100"升序，叠加评级≥AA-、溢价率≤20%、价格≤当日中位数、上市满 30 天过滤后，2026-08-15 快照输出双低前 10（鸿路转债 121.3 / 上银转债 121.4 / 大参转债 122.7 等），与社区双低选债逻辑一致。

**回测验证**（2026-01-01 ~ 2026-08-08，月频，top_n=5，1 万本金，免印花税/过户费）：总收益 -3.97%，最大回撤 7.72%，5 只持仓整 8 个月未轮出（0 笔平仓）。诊断结论：
- 双低排名在 8 个月内保持稳定 → 低换手符合策略设计（月频 + 转债低波动），也验证"固定快照 universe + 动态价格"近似在短期可行；
- **局限**：免费源只有当日快照、无历史 PIT 溢价率，跨年回测会引入幸存者偏差（候选池=今日双低前 N，回测起点它们可能并非双低最优）；长期回测需 PIT 数据（宁稳网/集思录付费数据源），短期回测与实盘信号可用。
- 修复：run_backtest 可转债分支此前把 yaml 中 `candidate_pool`/`exclude_force_redeem` 等选债过滤参数直接传给 Backtrader 策略类导致 TypeError，已按策略 params 白名单过滤。

**P5 结论**：可转债双低单策略执行链路已跑通；本轮进一步完成 `etf_mwu_cb` 四专家组合入口（动量 + 均值回归 + 可转债双低 + 国债防御），支持 ETF/可转债混合 feed、运行期注入双低快照，并修复可转债 10 张/手申报单位。真实回测（2026-01-01~2026-08-08）最终资金 9,373.01 元，总收益 -6.27%，最大回撤 15.27%；该结果不能证明组合已有正期望，只证明链路可运行，仍需长期 PIT 溢价率数据和样本外验证。当前下一步：① 修复/替换 TradeAnalyzer 交易统计提取，避免“总交易 0”误导；② 实盘信号层 LiveMWU 增加同一可转债专家；③ 引入 PIT 溢价率后再做长期组合评估。

### 6.2.5 ETF + 可转债 MWU 组合验证记录（2026-08-16）

**入口审视与修复**：此前 `etf_mwu_cb.yaml` 只是配置文件，CLI 未注册，MWU 构建前未注入 `bond_table`，且 ETFMWU 会把可转债按 ETF 默认 100 张申报单位处理。本轮新增 `--strategy etf_mwu_cb`，运行期从 AKShare 快照生成候选并注入 `ConvertibleDoubleLowExpert`，合并 ETF 与 Tushare `cb_daily` feeds，并按可转债 10 张/手、国债 ETF 10 张/手、普通 ETF 100 份/手分别下单。

**真实组合回测**（2026-01-01~2026-08-08，1 万本金，周频，4 专家，13 只 ETF + 8 只可转债）：最终资金 9,373.01 元，总收益 -6.27%，年化 -3.73%，最大回撤 15.27%，夏普 -0.531。该结果弱于此前可转债单策略的 -3.97%，说明“加入专家”不等于自动产生 alpha，当前四专家组合仍未达到年化为正目标。

**统计口径**：已修复 `TradeAnalyzer` 缺少 `closed/won/lost` 节点时的 `KeyError`。当前结果中的“总交易 0”准确表示回测期内没有完成平仓的 round-trip，不表示没有发生委托；若要比较换手成本，还需增加订单级分析器。组合期末持仓未平仓，因此不能仅用该字段证明低换手。

### 6.2.6 订单级统计与 PIT 长期验证（2026-08-16）

**订单级分析器**：`BacktestEngine` 新增成交订单统计，区分已成交订单、买卖成交额、成交总额、已支付佣金、拒单、保证金不足和撤单；换手率定义为区间成交总额 / 区间平均账户权益，`TradeAnalyzer.total.closed` 继续只表示已平仓 round-trip。

**组合复核**（2026-01-01~2026-08-08）：`etf_mwu_cb` 最终资金 9,373.01 元，总收益 -6.27%，最大回撤 15.27%；实际成交订单 105 笔，成交额 78,282.17 元，区间换手率 7.87x。该结果说明组合并非“0 交易”，且 1 万本金下周频组合的交易成本暴露明显。

**PIT 采集链路落地**：新增 `data/clients/ifind_client.py`（HTTP 直连 iFinD bond MCP，token 从环境变量 `IFIND_AUTH_TOKEN` 读取，不写死）与 `scripts/build_pit_snapshot.py`。采集方式：按“单券 × 单年”请求逐日收盘价 + 转股溢价率，iFinD 对大批量时序会截断并返回全量 CSV 下载 URL，直接下载该 CSV 获得全量数据（已实测一年 365 行完整无缺）；历史评级与上市日期从紧凑 markdown 表解析——注意**列序不固定**（实测“评级|上市日期”与“上市日期|评级”两种都存在），解析按表头关键字定位列位。评级按“生效日期 <= as_of 的最新评级”前向映射到每个调仓日。原始 CSV / meta 缓存到 `data/pit/raw/`，断点续跑；空 meta 不落盘缓存以便重试。

**PIT 校验**：新增 `validate_pit_snapshot()`，硬性违规（抛异常）覆盖字段不全、空表、`as_of` 不可解析、存在 `as_of` 晚于回测结束日的未来数据泄漏；软性统计输出每日期有效标的数（min/max/均值）、评级覆盖率、价格/溢价率异常行、未来上市行等。

**PIT 快照产物（扩容后）**：`data/pit/convertible_pit.parquet`（2018-01-01~2026-08-08，26,172 行，1,840 个交易日，**27 只转债**，日均有效标的 **14.22 只**，评级覆盖 100%，无未来数据、无未来上市行）。抽查 110076.SH 2022-03-29 价格/溢价率与 iFinD 原始值完全一致。

**真实 PIT 长期回测**（不再用当日快照冒充历史；扩容池 27 只，可转债候选从 8 → 27 后双低排序开始真实轮动）：
- 测试段 2022-01-01~2026-08-08（约 4.6 年）· 周频：总收益 **-3.62%**，最大回撤 27.41%，成交订单 126 笔，换手 10.48x。
- 长窗口 2020-01-01~2026-08-08（约 6.6 年，含训练段 2020-2021 + 测试段 2022-2026）· 周频：总收益 **-38.52%**，年化 -6.30%，最大回撤 55.86%，成交订单 318 笔，换手 **22.16x**。
- 长窗口 2020-2026 · **月频**：总收益 **+0.18%**，最大回撤 25.89%，成交订单 123 笔，换手 12.13x。
- 测试段 2022-2026 · **月频**：总收益 **-0.67%**，最大回撤 17.59%，成交订单 56 笔，换手 4.75x。

**扩容过程的关键实证**（2026-08-17）：
1. **老券无溢价率**：按“最早上市 60 只”选取的 2007-2013 老券，iFinD 全部只返回前向填充的价格、**无任何转股溢价率**（premium 有效行=0），双低策略无法使用，最终被 `dropna(premium_rate)` 自然剔除；仅 2019+ 上市的现代券有溢价率。因此 universe 必须选**现代券**（本批次按 2019-2021 上市筛选）。
2. **健壮性**：已退市券会返回列宽不一致的 CSV，`parse_daily_csv` 增加 `on_bad_lines="skip"` + 缺列返回空表，采集循环对单只 try/except 隔离，一只失败不再拖垮整批（此前 113002.SH 曾导致整批崩溃）。
3. **调仓频率是决定性成本变量**：周频在 1 万本金 + 真实轮动下换手 22x、累计 -38.5%；改月频后换手腰斩、长窗口基本打平（+0.18%）。印证社区“小资金 + 高频轮动 = 佣金磨损吞噬 alpha”的结论。

**诚实结论与局限**：PIT 管线已端到端可用且扩容后轮动真实（27 只候选、14.22 只/日、评级覆盖 100%、无未来泄漏）。但 universe 仍是**当前在市的现代券子集**（2019-2021 上市的前 60 只中已采集部分），未覆盖已退市/到期券，仍有幸存者偏差残留；月频基线在 2020-2026 长期仅打平、测试段小幅为负，**不构成盈利结论**。下一步已具备可信输入：可基于月频基线与 27 只池做参数优化（top_n、max_premium、评级下限、调仓频率）与样本外验证，`--assemble-only` 支持从缓存快速重建 PIT 反复迭代。

**PIT 覆盖与生命周期审计补强（2026-08-17）**：`coverage.json` 现同时记录请求池、实际有效池、缺失代码和覆盖率，并将缺失拆分为 `missing_meta_symbols`、`missing_daily_symbols`、`no_usable_pit_symbols`；对已经入池的标的记录 `first_observed` / `last_observed`。本次验证的 68 只请求券中 27 只有效，覆盖率 **39.71%**；41 只均停在元数据获取之前，不能据此声称其退市、到期或没有历史溢价率。`last_observed` 仅表示当前 PIT 文件的最后可观察行情，尚未形成全量的到期日、强赎和最后交易日数据，因此回测引擎不应擅自把数据末行强制平仓。

**PIT 全量补齐与幸存者偏差修复（2026-08-18）**：新增 `scripts/backfill_cb_from_akshare.py`，用 AKShare `bond_zh_cov_value_analysis`（含已退市券日频收盘价/转股溢价率，溢价率百分数口径与 PIT 一致）+ `bond_zh_cov`（全量发行表：评级/上市时间/发行规模）补采 41 只 iFinD 缺失券，41/41 成功；`--enrich-only` 模式补全全部 meta 的 `maturity_date`（集思录真值优先，申购日+6 年近似兜底）与 `issue_size`。PIT 重组为 **59,609 行 / 68 只全覆盖 / 0 脏行 / 8 列 schema**（新增 `maturity_date`、`issue_size` 静态广播列）。评级静态映射经验证可用（73 只对照 93.2% 一致，差异均为 ±1 档评级迁移，无跨 A- 过滤线）。
**完整 universe 复核回测**（etf_mwu_cb 月频，配置固化 `config/strategy/etf_mwu_cb_monthly.yaml`，2020-01-01~2026-08-08）：总收益 **-3.45%**、最大回撤 27.76%、换手 19.06x。对比 27 只幸存者偏差口径（+0.18%）**恶化 3.6 个百分点**——证实旧"打平"结论由退市券缺失造成，后续参数优化必须以 -3.45% 为真实基线。
**ETF 数据链修正（2026-08-18）**：实证 tushare ETF 日线不处理份额分拆（512480/510500/512010/512100/512890 分拆日 ±50%~300% 假跳变），`loader._etf_chain` 改为 akshare > tushare > baostock，并清除 8/15 生成的污染 ETF hfq 缓存；ETF 冻结快照构建工具固化为 `scripts/build_feeds_snapshot.py`（基线 diff + 断点扫描校验），universe 扩至 17 只（+中证2000/红利低波/黄金/货币停靠，快照 `feeds_snapshot_v2.pkl`，与 v1 日收益 corr=1.0000）。

**转债全量池与 v2 过滤规则（2026-08-18）**：universe 由 68 只扩至 **854 只**（2019+ 上市全量发行券，bond_zh_cov 衍生 `data/universe_cb_full.txt`），AKShare 补采 786 只全部入库，PIT 重建为 **624,637 行 / 849 只 / 每日中位 362 只候选**。`PITConvertibleDoubleLowExpert` 新增 v2 过滤参数（默认关闭）：`max_conv_value`（转股价值=价格/(1+溢价率/100)，数学推导无需额外数据）、`min_maturity_days`、`max_price`。四组对照实验（etf_mwu_cb 月频 2020-2026）：68 只池无 v2 = -3.45%；68 只池+v2 = -29.21%（**小池掏空反例**：42/92 个月候选<5 只、13 个月归零）；849 只池无 v2 = -5.09%；**849 只池+v2 = -1.44%（当前基线）**。实证确认社区过滤规则的隐含前提是全市场规模池。全量池回测性能：`precompute_cb_union.py` 预计算月频双低 top15 候选并集（276 只，确定性无前视）+ `run_backtest --cb-symbols-file` 限制 feed。到期日近似精度：19 只到期退市券的"申购+6年"与实际最后行情日误差 ≤1 天。

**MWU+转债 Walk-Forward（2026-08-18，B2 完成，诚实负结果）**：`run_walk_forward.py` 扩展 `--strategy etf_mwu_cb`（冻结三输入：ETF 快照/PIT/CB feeds 快照，锁定 CSV 附三 SHA；网格键自动路由到 MWU 顶层/动量/CB 专家；**测试指标裁剪**：equity_curve 只统计测试窗，剔除 warmup 段污染——整跑与测试窗口径差异实证 +1.92% vs +0.07%）。4 组合（top_n=3,5 × max_premium=15,20）× 8 折（2020-2026，3 年训练/6 月测试）并行运行。结果：OOS 累计 -1.65% ~ -13.2%，**无组合为正 → 不接入实盘**。更根本的负结果：训练/测试年化同号折数仅 2-3/8，训练窗选参信号对测试窗无预测力。fold 8（2026-07/08，5.5 周 -11%~-21.6%）根因诊断为**池萎缩集中风险**而非市场崩盘（当期双低前 20 中位 +3.4%，但候选池仅 28 只存活、一只 -23% 券入持仓域）；候选缓解为池规模下限规则，留待下轮验证（不对失败折打补丁）。附带交付：`build_cb_feeds_snapshot.py`（160/160 零失败）、`precompute_cb_union.py` 全参数化（网格超集原则：并集须用网格最宽参数生成）、`.gitignore` 补 `data/pit/raw/`。

**池规模下限规则验证（2026-08-18 P 批）**：`PITConvertibleDoubleLowExpert` 新增 `min_candidates`（完整过滤链后候选不足阈值时专家让位现金；检查基于从 compute 抽出的单一来源 `_filter_double_low_pool` helper）。阈值取结构值 top_n×3=15（不网格扫，防拟合失败折；规则的事后性已声明）。全局验证 4×8 折重跑：OOS 全部改善（t3p15 -12.97%→-1.25%、t5p20 -1.65%→-0.89%），最大回撤 21.15%→9.89% 全线收敛，正常期（前 7 折）干扰 |Δ|<1.8pp（机制性规则特征：正常期零干扰、萎缩期防集中爆仓）——**规则采纳**为新基线（yaml min_candidates: 15）。附带发现：前 7 折（2023-01~2026-06）OOS 全正（+10.3%~+10.7%），唯一深坑是 2026-07/08 折；top_n 差异被池规则抹平。**实盘结论不变**：新 OOS 全负；PBO/CSCV 因 N=4 且配置行为趋同不做（未来扩 ≥20 配置矩阵再上）。

**ETF 动量滚动 Walk-Forward（2026-08-17）**：新增 `scripts/run_walk_forward.py`，使用冻结的 13 只 ETF feeds 快照（SHA-256：`273c8153ec8c89228b65bf8068cd9faa4e5e2dfb4811202f6fe2311556f65161`）。2020-01-01~2026-08-08，3 年训练窗、6 个月测试窗、8 折；每折仅按训练目标 `annual_return - 0.25 * max_drawdown` 选择参数，优先最大回撤不超过 40% 的候选，锁定后才执行下一测试窗。前 5 折测试收益分别为 -13.27%、-11.92%、-2.81%、-6.49%、-14.18%，后 3 折为 +19.99%、+54.23%、+6.30%；后期三折均选中 `lookback=90, top_k=2, ma_filter=200`。该参数没有跨 regime 稳定表现，不能接入 `LiveMWU` 或 `TDXJKBroker`；结果文件保留于 `quant_research/output/walk_forward_etf_2020_2026.csv` 与对应 `_locked.csv`，后续应把相同锁参流程扩展到月频 ETF+可转债 MWU。

**最终战略方案定稿（2026-08-18 广域调研）**：在 ETF 动量与 MWU+转债两条路线 WF 验证均未达标（OOS 全负）后，进行全品类广域调研（事件驱动套利/打新/被动配置/小市值多因子/ML 选股/期权期货/REITs/日历效应/国际策略），按硬约束（小资金 1-10 万、TDXJK 半自动执行、通知系统与回测基建已有、业余时间）决策矩阵评分，定稿**三层小资金多策略体系**（社区所有成功实盘者的共同结构，无一单策略）：第 1 层收益垫（事件套利监控 + 转债打新提醒，价差由合约锁定无需回测，预期年化 5-15%；数据源已实测验证——`stock_notice_report` 全市场公告关键词过滤真实命中 ST龙元要约收购，`bond_zh_cov` 申购日历含未来待申购新债）；第 2 层底仓（转债 ETF 511380 或被动配置组合，预期年化 6-10%）；第 3 层进攻（转债三因子周频版 / 小市值国九条，必须先过本仓库 WF 验证纪律才上实盘）。**核心认知转变：小资金在事件套利/打新领域是优势而非劣势**（容量小机构进不来）。完整方案与实施路径详见 `quant_research/HANDOFF.md` 第 4 节。

### 6.2.7 重构 R1-R2：MWUCore 抽取、loss 映射修正与 RuleBook 强制闸（2026-08-29）

依据 `.scratch/quant-refactor-plan-20260829.md`（六层蓝图）执行，逐层迁移、每步独立验证。工作树先以四个逻辑提交锚定基线后动工。

**R1a 行为保持重构（commit 4cc0dc8a）**：痛点 1（MWU 双实现漂移）修复。
- 新增 `core/` 包：`market.py` 定义 MarketView/BarSeries 协议——**适配方向反转**（此前实盘侧 PriceSeries 反向模仿 backtrader line，协议真源落在第三方库上）；`mwu_core.py` 为全项目唯一的 MWU 权重更新 + Fixed Share 实现，回测 ETFMWU 与实盘 LiveMWU 均委托之。
- 专家（动量/均值回归/防御/转债快照+PIT）全部改为消费 MarketView；backtrader 侧由 `backtest/adapter.py` 的 BTMarketView 实现协议。
- 顺带修复 `run_live.py` 真 bug：`_load_price_window` 返回 dict 而 `LiveMWU.update` 按 tuple 解包，paper 模式必崩。
- **等价性验证**：合成数据端到端回测（净值曲线/final_value/期末权重）与 40 步实盘权重轨迹，重构前后**逐位一致**（`.scratch/r1_equiv_{before,after}.json`）。

**R1b loss 映射理论修正（commit ec9d9199，吸收改进计划 P0-1）**：
- 原映射 `clip(-r/scale, 0, 1)` 违反 Hedge/EG 理论（learner 与 comparator 必须面对同一非负 loss 序列）：盈利被冻结、空选成为特权无损失状态。修正为线性映射 ρ=clip(r/scale,-1,1)、ℓ=(1-ρ)/2∈[0,1]；空选专家按现金收益 r=0（ℓ=0.5）参与更新（这才是 ETFMWU 文档一贯声称的语义）。loss_scale 语义变为"满量程单期收益"。
- **实证 Δ（MWU+CB 路线 t5p20，8 折 WF 复核 `output/wf_r1b_recheck_mwu_cb_t5p20.csv`）**：OOS 累计 -0.89% → -0.50%，逐折 |Δ|≤0.6pp，5/8 正折数不变——**定性结论不变（OOS 非正，不接实盘）**，与理论预期一致：修映射是让实验可解释，不创造 alpha。ETF 动量路线不经过 MWU 层，8 折逐折 delta=0（复核 `output/wf_r1b_recheck_etf.csv`）。
- 合成场景 Δ：final_value 11669.76 → 12392.02（+6.2%）；崩盘测试断言改为轨迹式（期末权重 < 上涨期峰值），旧断言编码的是旧不对称映射行为。

**R2 RuleBook 规则表 + broker 强制闸**：痛点 2（A 股规则散落硬编码）修复。
- 新增 `core/rules.py`：品种（stock/etf/cb，含板块细分）× 生效日期 → 费率/涨跌停/T+1 查表。修正三处历史失真：创业板 2020-08-24 注册制前 10%（原实现一律 20%）；159915/588xxx 等 ETF ±20%（原按 10%）；转债 2022-08-01 前 None（无统一日线限制口径，不设闸）。印花税 2023-08-28 减半、过户费 2022-07 降费后 0.001% 入表。
- `TPlus1Broker` 升级为**执行路径强制闸**（`transmit()` 提交路径拒单）：涨停不可买、跌停不可卖、T+1 品种当日买入不可卖出——此前两条规则是被动组件且无任何策略调用，实际从未生效；买入成交日期由 broker 在实际成交时自动记录。
- 诚实边界：backtrader `CommInfo._getcommission` 无日期上下文，佣金按现行规则取值（对 ETF+转债组合无影响，均免税）；全日期精确费用待 R5 两段式执行模拟器。日期敏感的涨跌停/T+1 在闸（有日期）实时查表。
- 回归中发现并修正测试的物理不可行场景：止损测试的 -20% 单日暴跌超出主板跌停（强制闸下拒单），改为 -9.5%×2 连续暴跌（合法且累计触发止损）。
- **闸影响实证（ETF 动量 8 折 WF，对照 R1b 复核 `output/wf_r2_gate_etf.csv`）**：3/8 折逐位不变（该窗口无涨跌停事件），4 折 |Δ| 3-9pp（此前存在涨停追买/跌停卖出的不可能成交），OOS 累计 +17.2% → +14.7%——**结构性真实性修正的量化证据**：旧回测的成交里有物理上不可能的订单；合成场景（无涨跌停事件）R2 前后逐位一致，确认闸不误伤。

**测试规模**：178 → 218 项全过（+13 MWUCore、+18 RuleBook、+9 相关迁移）。
**R3 Runner 编排层（05534b30）**：痛点 6 修复。`quant_research/runner.py` 收拢两脚本的参数类型表（并集）、网格解析与组合预算、warmup 计算、feeds 切片、折切分、指标行、训练窗锁参准则、MWU 网格键路由与文件 SHA-256；`run_grid.py`/`run_walk_forward.py` 退化为 CLI 薄入口。验证：ETF WF 重跑输出与重构前逐字节一致（主 CSV 与 locked CSV）。

**R4 实验注册层（082cf2b8）**：痛点 5 修复。`core/experiments.py`：run_id = UTC 时间戳 + 参数哈希；`output/runs/<run_id>/meta.json` + 追加式 `index.jsonl`——试验数 N 从此机器可追溯（P0-2 DSR/HLZ 校正的输入）；已接入 run_backtest/run_grid/run_walk_forward 三个入口。qlib Recorder 语义的文件系统实现，不引 MLflow。

**R5 Panel + 因子层 + 两段式转债回测**：痛点 3/4 修复。
- `core/panel.py`：PIT 长表 → (date × symbol) 字段矩阵（price/premium_rate），静态字段（rating/maturity_date 等）按品种广播；`get_panel(field, start, end)` 查询式截面 + `forward_returns(periods)` 前向收益矩阵。849 只转债在矩阵范式下只是 849 列。
- `core/factors.py`：装饰器注册因子 → Panel 向量化计算；内置 double_low/price/premium_rate/conv_value；`ic_series`（RankIC 缺省）与 `evaluate_factor`（mean IC/ICIR/t 值/覆盖率）。
- `backtest/two_stage.py`：第一段矩阵选债 `compute_cb_target_weights`（逐调仓日调 `pit_snapshot_picks` 单一过滤链——与 PIT 专家共享，语义一致由构造保证）；第二段 `WeightTargetStrategy` 经 `TargetWeightExecutionMixin`（自 ETFMWU 抽取的执行逻辑）消费权重计划，跑在 BacktestEngine 上享有 RuleBook 强制闸。`pit_to_feeds` 只为被选中券建 feed（并集成为选债副产品），**precompute_cb_union 预计算步骤废除**。
- 过程中暴露并修复两个真 bug：①共享执行逻辑中目标外 1 手持仓因"超额×0.98 取整"永远无法卖出（ETFMomentum 用 close() 全平所以从未暴露，mixin 语义偏离）；②PIT 合成 feed 退市后价格 NaN 被估值防御计 0、净值塌方——修正为目标外整仓 + 退市券价格前向填充（到期/强赎按最后成交价退出的诚实近似）。
- **两段式新基线**（转债双低月频 v2 过滤 2020-2026、1 万本金）：+15.28%/6.6 年（年化 +1.85%）、回撤 25.30%、成交 190 笔、换手 52.12x。收盘价近似口径（PIT 无 OHLC，open=close 合成），不可与旧 MWU 组合基线逐位对比，作为独立基线记录。
- **因子 IC 报告**（`output/cb_factor_ic_report.csv`，849 只全量池 2019-2026）：双低因子 RankIC -0.05~-0.06（5/10/20 日）、|t| 12.8-16.6，周期越长越强——**横截面预测力统计显著**（HLZ 门槛 t>3）；conv_value 同向显著；单独 premium_rate 不显著（RankIC≈0，Pearson 为负）——双低的预测力主要来自价格分量。诚实边界：IC 显著 ≠ 成本后可交易，52x 换手下的费用已证明吞掉毛 alpha。

**测试规模**：178 → 237 项全过。

### 6.3 风险点

1. **策略过拟合风险**：年化 15% + Calmar > 1 在 1 万本金 + 手动下单场景下是**挑战性目标**，建议先以年化 10% + Calmar > 0.8 为初版目标，验证后再上调。
2. **数据完整性风险**：Baostock 退市股数据虽全但字段较 Tushare 少，可转债数据 AKShare 字段最全但稳定性弱于 Tushare，需要 fallback 兜底。
3. **维护成本风险**：独立项目与主项目共享通知 API，主项目接口变更需同步通知层适配。

### 6.4 回滚方式

- `quant_research/` 作为独立目录，与主项目 `src/` 解耦，任何阶段失败可直接删除目录而不影响主项目。
- 数据缓存在 `quant_research/data/cache/`，删除即清理，无需数据库迁移。
- 配置在 `quant_research/config/`，环境变量在 `.env`，删除配置即回滚。

---

## 7. 参考资料

### GitHub
- [microsoft/qlib](https://github.com/microsoft/qlib) — 配置驱动 + 因子框架
- [mementum/backtrader](https://github.com/mementum/backtrader) — 事件驱动回测
- [vnpy/vnpy](https://github.com/vnpy/vnpy) — 实盘对接
- [QuantConnect/Lean](https://github.com/QuantConnect/Lean) — 多市场引擎
- [QUANTAXIS/QUANTAXIS](https://github.com/QUANTAXIS/QUANTAXIS) — 全栈量化
- [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) — 高性能
- [ricequant/rqalpha](https://github.com/ricequant/rqalpha) — A 股回测
- [polaber/vectorbt](https://github.com/polaber/vectorbt) — 向量化

### 知乎
- 我解剖了聚宽最传奇策略——它正在慢慢死掉
- 个人做量化交易靠谱吗？
- 国内有多少散户会自己写量化策略
- Tushare / Baostock / AKShare 数据源对比
- 量化交易常见因子 6 大类
- 那个神秘品种又回来了|ETF 网格策略实盘

### 文档
- `docs/zhihu_quant_research.json`
- `docs/zhihu_deep_research_round1.json`
- `docs/zhihu_deep_research_round2.json`
- `docs/zhihu_deep_research_round3.json`
- `docs/zhihu_targeted_research.json`
- `docs/zhihu_q2_research.json`

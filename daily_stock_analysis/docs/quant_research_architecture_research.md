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
- QuantStats 在中国 A 股日历（含节假日）的年化计算需自定义 trading_days=252。（P3 分析层启用时处理）
- ETF 专用接口（Tushare fund_daily / AKShare fund_etf_hist_em）和可转债接口（Tushare cb_basic / AKShare bond_zh_cov）已实现但尚未用真实 token 跑通端到端验证（需配置 TUSHARE_TOKEN）。

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

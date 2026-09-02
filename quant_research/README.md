# quant_research

独立个人量化研究子项目：PIT 数据管线、因子注册、成本感知回测、过拟合审计。

## 状态

252 项测试全过。当前研究结论与下一步见 [`HANDOFF.md`](HANDOFF.md) 与 [`docs/ROADMAP.md`](docs/ROADMAP.md)。

## 快速开始

```bash
# 1) 安装（锁版本，见下方"可复现性"）
pip install -r requirements.lock.txt
pip install -e .

# 2) 测试
py -m pytest -q

# 3) 回测（两段式转债双低，季频，真实费率）
py scripts/run_cb_backtest.py --pit data/pit/convertible_pit.parquet \
    --start 2020-01-01 --end 2026-09-01
```

## 目录结构

```text
quant_research/
├── quant_research/       # 主包
│   ├── core/             # 数据面板 / 因子注册 / MWU 融合 / RuleBook / 过拟合审计 / 实验注册
│   ├── data/             # 多源数据客户端 (akshare/tushare/baostock/iFinD) + parquet 缓存
│   ├── backtest/         # 引擎 (backtrader) / 转债选债 / 两段式回测 / RuleBook 强制闸
│   ├── trade/            # PaperBroker / TDXJK / 执行器
│   └── config/           # Hydra + pydantic 配置
├── scripts/              # CLI 入口（回测 / 数据采集 / 审计 / paper tracking）
├── tests/                # 252 项测试
├── data/                 # PIT 快照 + raw 缓存（gitignore，可重建）
└── docs/                 # ROADMAP / 预注册文档
```

## 可复现性

1. 依赖锁定：`pip install -r requirements.lock.txt`（版本精确）。
2. 随机种子：回测/选债路径为确定性（无随机调用）；含随机数的研究（CSCV 置换、bootstrap）使用固定 seed 并记录到实验注册。
3. 数据版本：PIT 快照与冻结 feed 均记录 SHA-256（HANDOFF.md / 实验注册）。
4. 一键重建：`bash scripts/rebuild_all.sh`（raw 缓存 → PIT → 快照）。

## 免责声明

仅供学习和研究使用，不构成投资建议。

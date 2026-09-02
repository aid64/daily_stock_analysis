# quant_research 路线图 V2.2（2026-09-02 深化定稿）

> 输入：V2.1 F 系列框架 + 本轮多网站调研（akshare 官方文档直读、vectorbt/rqalpha GitHub 生态、知乎 6 组追加：下修数据分布/复权 PIT 偏差/最低佣金规则/YTM 防守口径/2026 拥挤度）。
> 本文件取代 V2.1 成为**执行真源**。深化方式：每个 F 项细化到"改哪个文件、新增什么函数、验收断言怎么写"，达到可直接开工的粒度。

## 〇、本轮调研的关键修正与确认（对 V2.1 的增量）

1. **下修数据规模确认**（知乎《下修次数全扫描》）：下修过的券平均发行规模 8.3 亿 vs 未下修 12.7 亿（小盘更积极）；下修幅度中位数 20.5%，海优转债累计 3 次共 -118%。→ F7 的 adj_logs 采集有明确预期规模，且"下修后多久能强赎"是该帖关注的核心问题——正是我们派生列要回答的。
2. **前复权 PIT 偏差获得理论背书**（知乎《A 股回测中的复权与 PIT 偏差》）：事后前复权是"用最新股本结构反推历史等效价格"——本项目 ETF v3 用真实价+分拆修正的口径选择被确认正确；转债 PIT 用原始价的路径同样安全。
3. **YTM 口径确认**（akshare 官档）：`bond_zh_cov_value_analysis` 有"纯债价值"列（我们的 raw 缓存已在拉它但未入库！）——**纯债价值 = 债底的直接代理，比自算 YTM 更省事**：F8 可改为"纯债溢价率因子"（价格/纯债价值-1），数据已在手，零新增采集。
4. **最低 5 元规则细节**（知乎专文）：单笔 <5 元按 5 元收是监管通行规则；ETF 部分券商单笔最低 0.2 元起——转债若同 ETF 档位则策略可行性大增（F6 扫描应加"最低佣金 5 元 vs 1 元"敏感性档）。
5. **vectorbt 官档确认**：9k star、参数扫描"hours→seconds"、无 A 股原生支持（T+1/涨跌停需自写）——R-C 否决维持。
6. **开源同构项目发现**：`cb-strategy-mcp`（知乎 5 赞，一键跑 6 大转债策略含下修/强赎）——可作 F7/F8 的交叉验证参照，不引入依赖。

## 一、F 系列执行规格（可直接开工）

### 第 1 周：诚实性四件套

**F1 C2 三表实现**（~1 天）
- 改 `scripts/run_paper_tracking.py`：增 `--record` 参数与 `record_quarterly()` 函数。
- 数据落 `output/paper_tracking/<YYYYQn>.json`：`{as_of, targets, equity, commission_paid, predicted_return, realized_return, executed_weight, planned_weight}`。
- 三表 CSV 由 `build_paper_tables.py`（新脚本）从累积 JSON 生成：`predicted_vs_realized.csv` / `executed_vs_planned.csv` / `cost_vs_model.csv`。
- 验收：连续两次 `--record` 运行数据累积；三表含至少一行。

**F2 holdout 隔离** ✅ 已完成（`bac147c5`）。剩余：四个入口接线（run_grid/run_walk_forward/run_cb_backtest/run_overfit_audit 的 main() 首行 `assert_holdout_clean(args.end, task=...)`）——归入 F1 批次一起提交。

**F3 total_trades 口径**（~0.5 天）
- 改 `quant_research/backtest/engine.py` 摘要提取：新增 `closed_trades` 字段（原 TradeAnalyzer.total.closed），`total_trades` 改为 `completed_orders` 语义。
- 全仓 grep `total_trades` 消费方（H1/H2/A1/A2 脚本、paper tracking、tests）同步改引用或兼容。
- 验收：两段式 190 笔订单 → total_trades=150（completed），不再为 0；旧 closed 值保留在新字段。

**F4 实验注册 status**（~0.5 天）
- 改 `core/experiments.py`：`record_run(..., status="completed")`（completed/failed/smoke）；`count_trials(status="completed")` 默认过滤；旧无 status 行视为 completed。
- 改 `run_overfit_audit.py` 等 DSR 消费方用过滤后 N。
- 验收：单测覆盖三种 status 过滤；`count_trials()` 与手数一致。

### 第 2 周：证据补齐

**F5 核心层单测**（~1.5 天）：runner（路由/折切分/锁参）、panel（pivot/静态广播/前向收益）、factors（注册/IC 排序）、adapter（line 映射）各 5-10 例。验收：256→286+。

**F6 成本敏感性扩展**（~0.5 天）
- 扩 `run_cost_sensitivity.py`：滑点档 0/0.05%/0.1%/0.2% × 佣金档（万0.845+最低5 / 万1+最低5 / **最低1**）。
- 依据：最低佣金是 1 万本金的主导变量（单笔 1300 元 ≪ 平衡点 5.9 万）；ETF 档最低佣金若适用转债则可行性大增。
- 验收：一张 4×3 衰减矩阵；回答"最低佣金降到 X 元时策略净收益转正"。

**F7 下修事件 PIT 化**（~1.5 天）
- 新 `scripts/build_adj_logs.py`：全 universe 逐券 `bond_cb_adj_logs_jsl(symbol=6位代码)` → `data/pit/adj_logs.csv`（6 列官档口径）。
- Panel 增派生列 `days_since_adj`（最近生效日距今）；`pit_snapshot_picks` 增可选 `min_days_since_adj` 过滤（默认关）。
- 预期规模（调研）：全市场约 1/3 券有下修史、中位幅度 20.5%。
- 验收：adj_logs 落盘 + 覆盖率报告；派生列在 Panel 单测中验证。

**F8 纯债溢价率因子（替代 YTM 自算）**（~1.5 天）
- 依据更新：`bond_zh_cov_value_analysis` 有"纯债价值"列，raw 缓存已拉但未入 PIT schema——**扩展 PIT schema 加 `bond_value` 列**（assemble-only 路径重跑），factors.py 注册 `bond_premium = price/bond_value - 1`。
- 门槛（预注册）：RankIC |t|>3（HLZ）；与双低相关性 >0.7 视为冗余不叠加。
- 验收：单因子诊断报告；数据零新增采集（改造现有管线）。

**F9 季频止损带**（~1 天）：预注册两档（-8% 固定 / 20 日真实波幅 2 倍），门槛：回撤改善 ≥3pp 且收益让渡 ≤2pp，否则拒绝。

### 第 3 周：执行通道与收尾

**F10 promotion gate 代码化**（~1 天）：`core/promotion_gate.py` 四条硬门槛（时长 ≥6 月、|预测-实现| ≤5pp、执行率 ≥90%、回撤比 ≤1.5x），输出 JSON 判定。

## 二、执行顺序与依赖

```
第 1 周：F1 + F2接线 → F3 → F4（互相独立，一批提交）
第 2 周：F5 → F6 → F7 → F8（F8 依赖 F7 的 schema 扩展方法）
第 3 周：F9 → F10 → C2 运行节奏启动
```

## 三、不做清单（继承 + 本轮新增）

1-6 继承 V2.1。新增：
7. **不自算 YTM**（纯债价值列已在数据管线中，直接用纯债溢价率因子替代）。
8. **不引入 cb-strategy-mcp 依赖**（仅作交叉验证参照）。

## 四、文档关系

本文件 = 执行真源。ROADMAP-V2.1 = 中间版（被取代）；ROADMAP.md（V1）= 已完成任务记录；HANDOFF.md = 状态快照（F 系列完成时同步）。

# quant_research 路线图 V2.1（2026-09-02 深化版）

> 依据：V2 盘点（6 个声称未做缺口 + 5 个忽略维度）+ 本轮 5 组追加调研（滑点实操/下修历史接口/YTM 因子/paper 验收/限价单技巧，`.scratch/zhihu_research_20260902.json`）+ 一个**重大数据源发现**。
> 深化方式：每个 F 项给出具体实现路径、验收标准、依赖关系与估时，可直接排入执行。
> 纪律不变：预注册、诚实负结果、holdout 代码强制、未过门槛不实盘。

## 〇、本轮调研的三个关键新输入

1. **`bond_cb_adj_logs_jsl(symbol=代码)` 返回逐券下修历史**（实测 113534/128039/110044 各返回下修记录：股东大会日/下修前后转股价/生效日）。这是 B2 长期悬而未决的"下修事件状态 PIT 化"的**现成数据源**——下修是转债特有的事件驱动 alpha（知乎 16 赞指增帖的联动差值因子的核心输入），且该接口是历史事件表（无今日回填问题，天然 PIT 安全）。
2. **YTM 税前收益**：`bond_cb_jsl` 有"到期税前收益"列但仅 30 行（jisilu 实时页限额）；**`bond_zh_cov` 无 YTM 列**。YTM 因子的可行路径 = 用 `到期时间` + 票面利率 + 现价自算（PIT 表已有 maturity_date，缺票面利率——需从 `bond_zh_cov_info` 或发行公告补静态列）。
3. **滑点实操共识**（多篇实证帖）：A 股小盘实盘冲击 20-200bp 常态、回测 0.5-1bp 全成本假设是主要低估源；**转债因 T+0 和亿级成交额滑点更小，但月/季频整手换仓应按 5-15bp 校准并做敏感性扫描**；限价单+回落条件单是散户最优执行工具（有帖实测条件单在转债日内高波动下卖出价接近全天最高）。

## 一、任务表（F1-F10，可直接执行）

### P0：诚实性收口（声称未做 → 做实）

| ID | 任务 | 实现路径 | 验收 | 估时 |
| --- | --- | --- | --- | --- |
| **F1** | C2 三表产出实现 | `run_paper_tracking.py` 增 `--record` 模式：把当期 equity/commission/目标权重快照写 `output/paper_tracking/<季度>.json`；季末跑增量对比（上季目标 → 本季实际）生成三表 CSV | 每季末 3 CSV + 期末汇总 JSON；`--record` 连续运行数据可累积 | 1 天 |
| **F2** | holdout 代码隔离 | 新 `core/holdout.py`：`HOLDOUT_START=2026-08-09`，`assert_holdout_clean(end_date)` 被 run_grid/run_walk_forward/run_cb_backtest/run_overfit_audit 的 main() 首行调用 | 触碰 holdout 的研究命令直接退出非零；paper tracking 豁免（它是记录不是研究） | 0.5 天 |
| **F3** | total_trades 口径修复 | engine.py 摘要同时报 `closed_trades`（原 total_trades）与 `completed_orders`；`total_trades` 改为 completed 语义 | 两段式 trades=订单数（150），不再为 0；既有引用同步 | 0.5 天 |
| **F4** | 实验注册 status 过滤 | experiments.py 增 `status` 字段（completed/failed/smoke，record_run 参数）；`count_trials(status="completed")`；旧 jsonl 无 status 的视为 completed | `count_trials()` 输出与手数一致；DSR 脚本改用过滤后 N | 0.5 天 |
| **F5** | 核心层单测 | runner（参数路由/折切分/锁参准则各 3-5 例）、panel（pivot/静态广播/前向收益边界）、factors（注册/IC 排序）、adapter（line 协议映射） | 256→286+ 项全过 | 1.5 天 |

### P1：被忽略证据补齐

| ID | 任务 | 实现路径 | 验收 | 估时 |
| --- | --- | --- | --- | --- |
| **F6** | 滑点敏感性扫描 | 复用 run_cost_sensitivity 框架：slippage 0/0.05%/0.1%/0.2% 四档（engine 的 `set_slippage_perc` 已支持）× 季频 | 一张滑点衰减表；确认 0.1% 档下季频仍接近盈亏平衡 | 0.5 天 |
| **F7** | **下修事件 PIT 化（B2 复活，数据源已验证）** | 新 `scripts/build_adj_logs.py`：逐券拉 `bond_cb_adj_logs_jsl` → 落 `data/pit/adj_logs.csv`（symbol/股东大会日/新旧转股价/生效日）；Panel 增"自最近下修生效日以来天数"派生列；选债链加可选 `min_days_since_adj` 过滤 | 全 universe 下修表落盘；派生列覆盖率报告 | 1.5 天 |
| **F8** | YTM 因子注册与诊断 | 从 `bond_zh_cov_info` 补票面利率静态列 → 自算 YTM（现价/票息/到期日现金流折现）→ factors.py 注册 `ytm` → IC/RankIC/覆盖率报告 | 单因子报告（含与双低的相关性）；不通过则如实关闭 | 1.5 天 |
| **F9** | 季频止损带实验 | WeightTargetStrategy 已有 `stop_loss_pct`（未启用）：预注册两档（-8% 固定 / 2×ATR 近似用 20 日真实波幅），2020-2025 训练窗 | 预注册门槛：回撤改善 ≥3pp 且收益让渡 ≤2pp；否则如实拒绝 | 1 天 |

### P2：执行通道预备（C2 期间并行，不阻塞）

| ID | 任务 | 实现路径 | 验收 | 估时 |
| --- | --- | --- | --- | --- |
| **F10** | paper→live gate checklist 代码化 | `core/promotion_gate.py`：最小模拟时长 ≥6 个月、跟踪误差 |预测-实现| ≤5pp、成交执行率 ≥90%、回撤在预测 ±1.5x 内——四条全过才输出 promote 建议 | `gate_check()` 输出结构化判定 JSON | 1 天 |

## 二、执行顺序（三周）

```
第 1 周：F1 → F2 → F3 → F4（诚实性四件套，全部收口）
第 2 周：F5（单测）→ F6（滑点）→ F7（下修 PIT，新数据源红利）
第 3 周：F8（YTM）→ F9（止损带）→ F10（promotion gate）
之后：  C2 运行（每季末 F1 三表 / 每月 update_pit_tail + adj_logs 增量）
        → 2027Q2 F10 gate 判定 → C1 决策
```

依赖关系：F1/F2/F3/F4 互相独立可并行；F7 依赖无（新数据源）；F8 依赖 F7 的静态列补齐方法（同源 bond_zh_cov_info）；F9 独立；F10 依赖 F1 落地的数据格式。

## 三、预注册草案（先写门槛后跑数）

- **F9 止损带**：接受 = 回撤改善 ≥3pp 且收益让渡 ≤2pp；拒绝即关闭止损方向，不追加档位。
- **F8 YTM**：单因子 RankIC |t| > 3（HLZ 门槛）才进入组合实验；与双低相关性 >0.7 则视为冗余不叠加。
- **F7 下修过滤**：不预设方向（下修后正/负两种文献证据都有），只做事实数据与派生列，是否进选债链留待有数据后独立预注册。

## 四、不做清单（继承并新增）

1-4 继承 V2（不迁框架/不做融合层调参/不 AI 挖因子/holdout 代码强制）。
5. **不做高频执行优化**（回落条件单/拆单/算法单）——季频一年 4 次调仓，执行边际收益趋零；仅当 C2 出现"成交执行率 <90%"证据时再启动 F10 的执行子项。
6. **不把 bond_cb_jsl 的 30 行实时页当数据源**（限额，仅作交叉验证）。

## 五、与既有文档关系

- 本文件（V2.1）是**执行真源**：F1-F10 排期、验收、预注册门槛。
- ROADMAP.md（V1）：已完成任务的历史记录（A1-A6/B1/B2/C2 启动）。
- ROADMAP-V2.md：V2 盘点过程，被本文件取代执行部分。
- HANDOFF.md：状态快照，F 系列完成时同步更新。

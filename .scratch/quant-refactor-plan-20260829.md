# quant_research 彻底重构方案（2026-08-29）

> 依据：三路架构调研——知乎 10 组架构专题搜索（`.scratch/zhihu_research_20260829_arch.json`）、六框架架构深读（qlib/rqalpha/vnpy/vectorbt/LEAN/Nautilus，代理报告全文含 URL 引用）、国内平台 API 设计 + xtquant 实盘通道调研。本地痛点逐一核实到代码行。
> 关系：本方案与 `quant-improvement-plan-20260828.md`（P0-P3）**不冲突**——R1 步骤天然吸收 P0-1（MWU loss 修正），R2 吸收 P0-3（成本规则化）；P0-2（PBO/DSR）与 P1 策略实验在新旧代码上结论等价，可并行或延后。

## 0. 先说结论

**推荐"逐层渐进重构"而非"推倒重来"：六层架构蓝图，每层从成熟框架借一块，五步迁移，每步独立可验证、可回滚。**

- 推倒重来的代价：丢掉 178 项测试、PIT 管线（849 只/62 万行，重建成本最高的资产）、WF 纪律与全部历史实证结论——这些恰是调研确认的"超越所有开源仓库的部分"。
- 整体迁移到任一框架的代价（调研逐条否决）：qlib 要转 .bin 格式且 T+1/涨跌停/印花税 PIT 不原生（limit_threshold 是全局标量）；vectorbt 无实盘路径；rqalpha/vnpy 依赖其数据生态；LEAN 绑云端；Nautilus 微秒级确定性对月频是纯负资产。
- 逐层借鉴的复杂度预算：总计约 2000-3000 行新代码，六个独立小决策，任一层失败单独回滚。
- **反过度工程护栏**（知乎《量化系统00》警告：散户量化第一杀手是"架构巨人症"——三个月写框架，最小闭环没跑通就烂尾）：每层重构必须对应下文一个已核实的痛点，不做"为了优雅"的抽象。

## 1. 六个已核实的痛点（重构的合法性来源）

| # | 痛点 | 代码证据 | 后果 |
| --- | --- | --- | --- |
| 1 | **MWU 双实现漂移** | `backtest/mwu.py` ETFMWU.next() 内嵌权重更新 vs `trade/pipeline.py` LiveMWU.update() 复刻同一算法（含 Fixed Share 回拉，两份手抄）；PriceSeries 适配器反向模仿 backtrader line 协议（`__getitem__` 的 `idx-1`、`_AmountLine.get`） | 协议真源在 backtrader 侧，任何一边改逻辑另一边静默漂移；P0-1 的 loss 修正也不得不改两处 |
| 2 | **A 股规则散落+硬编码** | `broker.py` AShareCommission 印花税硬编码"2023 减半后"单一费率；TPlus1Broker 是空壳、T+1 靠策略自觉调 `check_t1_before_sell()`；`get_limit_pct` 按代码前缀硬编码；`run_walk_forward.py:28` `_ETF_COMMISSION` 脚本级费率 override | 历史回测跨 2023-08-28 印花税变更点时费用错误；PRIMARY_SOURCE_RESEARCH.md 自己标记的工程债 |
| 3 | **849 只转债装不进 feed** | backtrader 全量建 feed 不可行 → `precompute_cb_union.py` 候选并集绕路 + `--cb-symbols-file` 注入 | 网格超集原则的维护成本（扫参必须用最宽参数重新生成并集）；本质是"数据服务是订阅式而非查询式"的结构缺陷 |
| 4 | **无因子抽象** | 每个策略/专家自己算指标（`momentum_scores`、`rsi()` 定义在 mwu.py 里）；无 (date×symbol) 因子矩阵形态 | 因子不能跨策略复用、不能做 IC/RankIC 评估——P1-3 专家替换实验、P1-4 小市值都缺这块地基 |
| 5 | **实验结果散落** | `output/*.csv` 约 20+ 个文件无注册（grid_*.csv、wf_*.csv、快照 pkl）；"试过多少次"靠 HANDOFF 手记 | P0-2 DSR 需要诚实试验数 N，当前无法机器追溯；结果与配置的对应靠文件名约定 |
| 6 | **脚本承载编排** | `run_walk_forward.py` 的 `_PARAM_TYPES`/`_MOMENTUM_KEYS` 手工网格键路由（339/400 行是编排）；warmup 计算、快照注入、SHA 记录全在脚本层 | `run_grid.py` 与 `run_walk_forward.py` 各维护一份键路由；新参数两处改 |

## 2. 六层架构蓝图（每层借一块，标注来源）

```
┌────────────────────────────────────────────────────────────┐
│ L1 数据层：Parquet + DuckDB 查询式数据服务                    │
│    现有 ParquetCache 保留；新增 get_panel(field, start, end)  │
│    截面视图与 ASOF 信号对齐（DuckDB 可选，纯 pandas 起步）      │  ← qlib 查询式数据服务 + 知乎 DuckDB/Parquet 共识
├────────────────────────────────────────────────────────────┤
│ L2 因子层：命名注册的因子函数 → (date×symbol) DataFrame       │
│    parquet 缓存（复用现有 cache）+ 通用 IC/RankIC 评估         │
│    不抄 qlib 字符串表达式引擎（只抄"因子=矩阵"形态）            │  ← qlib DataHandler/SigAnaRecord 子集
├────────────────────────────────────────────────────────────┤
│ L3 决策层：Expert（=AlphaModel）→ MWUCore（=自定义 PCM）       │
│    MWUCore 纯函数、全项目唯一权重融合实现                      │
│    专家消费 Runtime 查询接口，不再摸 backtrader line           │
│    Loss 映射修正（P0-1）在此一并落地                          │  ← LEAN 五段管线思想 + vnpy 单内核原则
├────────────────────────────────────────────────────────────┤
│ L4 规则层：RuleBook = (品种类型 × 生效日期区间) → 费率/涨跌停/T+1 │
│    印花税历史变更=表里多一行；涨跌停/T+1 下沉为执行路径强制闸     │
│    （撮合/执行层查表强制执行，不再靠策略自觉调用）               │  ← rqalpha "规则即数据" + Plus 印花税 PIT + Nautilus 前置闸
├────────────────────────────────────────────────────────────┤
│ L5 运行时层：小 Runtime 协议（get_history/now/positions/       │
│    submit_target）；BacktestRuntime（backtrader 侧实现协议）   │
│    + LiveRuntime（现 trade/executor 实现协议）                │
│    适配方向反转：backtrader 去实现协议，而非协议模仿 backtrader  │
│    执行通道：Paper / TDXJK / XTBroker（新增，见 §4）           │  ← vnpy 引擎协议 + 聚宽目标式下单语义
├────────────────────────────────────────────────────────────┤
│ L6 实验层：run_id = config hash + git SHA + 数据快照 SHA       │
│    params/metrics/artifacts → output/runs/<run_id>/ + 索引    │
│    试验数 N 可机器追溯（P0-2 DSR 的输入）                      │  ← qlib Recorder 语义（不引 MLflow）
├────────────────────────────────────────────────────────────┤
│ L0 脚本层：run_* 瘦身为"读 config → 调 Runner"；               │
│    warmup/分段/快照注入/组合生成沉入 Runner 层                  │  ← vectorbt "参数即数据" + qlib config 驱动
└────────────────────────────────────────────────────────────┘
```

### 关键设计决策与理由

**D1 Runtime 协议反转（治痛点 1，最核心）**。现状是 PriceSeries 模仿 backtrader line（协议真源在第三方库）；反转后协议是自己的约十行接口，backtrader 侧和 live 侧各实现一遍。vnpy 的 CtaTemplate→CtaEngine/BacktestingEngine 双引擎同构证明了这条路的可行性，且其引擎协议只有约 10 个方法。MWU 权重更新（含 Fixed Share）收敛为单一 `MWUCore`——回测实盘共用一份，P0-1 loss 映射修正只改一处。

**D2 规则即数据（治痛点 2）**。RuleBook 是纯数据表：`[(品种类型, 生效起, 生效止, 佣金率, 最低佣金, 印花税率, 过户费率, 涨跌停%, T+1?)]`。rqalpha 商业版（Plus）已把印花税做成时间序列查表——这是该模式的商业验证。当前 `get_limit_pct` 前缀硬编码、`_ETF_COMMISSION` 脚本 override 全部收编进表。**A 股涨跌停按品种分区在所有调研的开源仓库中都没有现成实现**，这项本来就得自己写，写成表驱动顺带解决印花税历史变更。

**D3 研究计算与执行模拟两段式（治痛点 3）**。vectorbt 范式：849 只转债 × N 天就是一个 (time × 849) 矩阵，双低排序/过滤链/eligible mask 全是横截面向量化运算；执行模拟只接收"目标权重序列"逐事件核算。`precompute_cb_union.py` 的候选并集绕路在矩阵范式下整体作废。qlib 的启示是数据服务查询式（按需取 panel）而非 backtrader 全量 feed 预注册。**不引入 vectorbt 依赖本身**（numba 调试成本 + A 股微结构自写），只抄"矩阵研究计算 → 事件执行模拟"的分离思想，纯 pandas/numpy 实现（月频无性能压力）。

**D4 因子=矩阵，不用 DSL（治痛点 4）**。qlib 表达式引擎的收益 80% 来自"因子 = 命名注册函数 → (date×symbol) DataFrame → parquet 缓存 → 通用 IC 评估"这个形态，字符串 DSL 本身（解析器/调试栈/报错信息）是纯成本。因子注册用装饰器 + 元数据 dict 即可。

**D5 执行通道扩展：XTBroker（调研确认的 2025-2026 现实）**。社区半自动主力已是 miniQMT+xtquant（TDXJK 32 位 DLL 小众）；vnpy 官方 vnpy_xt 只做行情不下单；xtquant 的 connect() 一次性不自动重连、回调全在主线程、session_id 磁盘文件坑都已摸清（详见代理报告 B 节）。月频场景最克制的设计：**进程退出式**——调仓日计划任务拉起 `run_live.py --mode xt`，跑完 logout 退出，天然规避长连接内存增长与断线重连复杂性；启动时查当日同 remark 委托防重复下单（幂等），TradeAction 追加写 jsonl（WAL 思想）。

## 3. 五步迁移路径（每步独立验证、独立回滚）

| 步骤 | 内容 | 吸收既有计划 | 验证方式 | 工作量 |
| --- | --- | --- | --- | --- |
| **R1** | `MWUCore` 抽取（权重更新单一实现，含 loss 映射修正）+ Runtime 查询接口定义 + backtrader/live 两侧适配反转 | 吸收 P0-1；MWU/Expert 相关 50+ 项测试迁移对拍 | 新旧实现跑同一收益序列，权重轨迹逐期 diff=0（loss 修正部分预期有差异，单独声明）；`test_mwu.py`+`test_trade.py` 全绿 | 2-3 晚 |
| **R2** | RuleBook 表 + 执行闸（涨跌停/T+1/最小下单量下沉为强制闸）+ 印花税生效日期用例 + 成本乘子 | 吸收 P0-3 | 新增规则表单测（跨 2023-08-28 边界用例）；重跑基线对比费用项 | 2 晚 |
| **R3** | Runner 层吸收三脚本编排（参数空间描述对象、warmup、分段、快照注入），`_PARAM_TYPES` 双份键路由删除 | — | run_grid/run_walk_forward 输出与重构前 byte-level 可比（或 diff 可解释） | 2-3 晚 |
| **R4** | 实验注册目录 + 索引 + 试验数 N 追溯 | 为 P0-2 供弹药 | 旧 output/*.csv 可回填注册 | 1-2 晚 |
| **R5** | panel 截面接口 + 因子注册 + IC 评估 + 转债回测两段式（废除候选并集） | 支撑 P1-2/P1-3/P1-4 | 转债双低基线（-1.44%）在新管线复现；IC 评估对既有因子出报告 | 4-6 晚（改动面最大，放最后） |

**顺序理由**：R1 风险收益比最高（治最痛的双实现漂移且顺带 P0-1）；R5 改动面最大故放最后；R2-R4 互相独立可乱序/并行。**每步合入前跑全量 `py -m pytest -q`（当前 178 项）作为回归门**。

**执行通道（XTBroker）不排入 R1-R5**：它与重构主线独立，作为 P3 实盘基建的一部分，等第 3 层策略过 WF 验证、真要实盘时再实现（设计要点已在 §4/代理报告固化，无需现在写码）。

## 4. 明确不做的事（防"架构巨人症"）

1. **不做字符串因子 DSL**（qlib 表达式引擎的成本>收益，D4）。
2. **不引入 vectorbt/numba 依赖**（月频无吞吐需求，抄思想不抄库）。
3. **不做常驻 HTTP 执行引擎**（lite-qmt-executor 是日内高频设计；月频用进程退出式，D5）。
4. **不做消息总线/事件引擎线程模型**（rqalpha EventBus、vnpy EventEngine 对月频是负资产；调用式编排足够）。
5. **不迁数据库**（Parquet+文件系统是当前规模正解；DuckDB 只在 ASOF 对齐真成为瓶颈时引入，且只读 Parquet 不迁移数据）。
6. **不在重构期间改任何策略行为/参数**——重构是行为保持的，策略实验（P1）与重构（R 线）分两条线走，避免"改架构顺带改逻辑"的不可归因。

## 5. 风险与回滚

| 风险 | 缓解 | 回滚 |
| --- | --- | --- |
| R1 适配反转引入细微行为差异（line 索引语义 `d.close[0]` vs 适配器 `__getitem__`） | 逐期权重/持仓轨迹对拍测试先行；差异白名单化 | R1 是新增文件+两处替换，git revert 即回 |
| R2 规则表把费率抄错 | 边界用例（印花税变更日前后各一天）+ 与 rqalpha 语义对表 | 规则表是数据文件，改行即回 |
| R5 两段式回测与 backtrader 基线不等价 | 以 -1.44% 转债基线、19.06x 换手为锚复现通过才切换 | 旧 precompute 路径保留一个版本周期 |
| 重构期间未提交的工作树（21 改+17 新增） | **R1 动工前先把当前工作树提交一次**（需用户确认——AGENTS.md 规则：未经确认不 commit） | 全部新代码在 quant_research/ 内，删目录即回 |
| 过度工程复发 | 每步合入前自问"这步治的是 §1 哪个痛点"；治不了的不写 | — |

## 6. 与 2026-08-28 改进计划的合并视图

```
研究主线（P 线，产出结论）          工程主线（R 线，产出可信度）
P0-1 MWU loss 修正     ──被吸收──►  R1 MWUCore
P0-2 PBO/DSR           ◄──依赖────  R4 实验注册（试验数 N）
P0-3 成本敏感性         ──被吸收──►  R2 RuleBook
P0-4 regime 分段标注    ◄──受益────  R3 Runner（分段逻辑沉入）
P1-1 温度计门控         独立进行（PIT 数据现成，不依赖重构）
P1-2 三因子             ◄──受益────  R5 因子层
P1-3 专家替换           ◄──受益────  R5 IC 评估
P1-4 小市值             ◄──依赖────  R5 + L1（退市股 universe）
P3 QMT 调研             独立（XTBroker 设计已固化于本文件 §4/代理报告）
```

**建议节奏**：P1-1 温度计门控不依赖重构、可立即做（当前转债估值高位，正是该实验最有信息量的窗口）；R1 紧随其后；P0-2 PBO 可用现有 csv 先行粗算（N 靠手记），R4 完成后精确重算。

## 7. 调研产物索引

- `.scratch/zhihu_research_20260829_arch.json` + `zhihu_extract_20260829_arch.md` — 架构专题 10 组搜索（含 DuckDB 全栈实践、向量化 vs 事件驱动共识、backtrader 局限讨论、个人量化系统全景的反过度工程警告）
- 六框架深读代理报告（qlib/rqalpha/vnpy/vectorbt/LEAN/Nautilus，含具体类名与 URL）与平台 API/xtquant 代理报告（聚宽/米筐 API 精华、xtquant 连接模型/坑清单、XTBroker 设计）已浓缩进本文件 §2/§4，完整原文在会话记录中；关键外部结论均已在正文标注来源框架
- 本机 vnpy_xt 现状自查：官方 vnpy_xt 仅行情不下单（[vnpy 论坛 34701](https://www.vnpy.com/forum/topic/34701)），miniQMT 交易需社区改造版（[gitee good-man-z/vnpy_xt](https://gitee.com/good-man-z/vnpy_xt)）或直接用 xtquant

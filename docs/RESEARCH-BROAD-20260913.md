# 广泛调研与解决计划（2026-09-13）

> 范围：开源个人投资/交易系统先例（Ghostfolio、Portfolio Performance）、主流量化框架风控（vnpy_riskmanager、Qlib）、架构方法论（Fowler 事件溯源/Snapshot）、行业标准（FIX OrdStatus，前轮已取）、国内社区（知乎/集思录/BigQuant——访问受限，记录边界）。
> 方法：一手源码/文档直接取证；取证失败的来源不引用、不虚构社区共识。

## 一、外部一手取证结果

### 1. Fowler：事件溯源与快照的正确分工

来源：martinfowler.com/eaaDev/EventSourcing.html、/eaaDev/Snapshot.html（均成功取证）。

- **事件日志是唯一事实**："all changes to application state are stored as a sequence of events"；当前状态可从事件完全推导。
- **快照是优化读模型不是事实源**："Since an application state is purely derivable from the event log, you can cache it anywhere"；官方记录可以是事件日志，状态可随时重建。
- **快照应不可变**：Snapshot 模式页明确 "they should be immutable"。
- **重建三能力**：完全重建 / 时间点查询 / 事件重放纠错。

对本项目的裁定：当前"各模块写各自文件 + 快照事后汇总"是反模式。正确形态是——**真实账本（SQLite 交易/现金事件）已天然是事件日志**，快照只需成为不可变的时点读模型并被统一引用；不需要引入事件溯源框架，只需把"写入顺序"规范为 事件先行、读模型后生成。

### 2. Ghostfolio：个人投资记录的事实建模

来源：github.com/ghostfolio/ghostfolio README + prisma/schema.prisma（成功取证）。

Order 模型字段（源码级证据）：`account / comment / createdAt / currency / date / fee(Float) / id(uuid) / quantity / symbolProfileId / type / unitPrice / updatedAt`。

- **fee 是 Order 的一等字段**，不是事后统计——每笔记录自带费用；
- 活动类型枚举含 `BUY | SELL | FEE | INTEREST | DIVIDEND | LIABILITY`——**费用/利息是独立活动类型**；
- 纯记录工具（无下单集成），有 API 导入端点与 MCP 服务器（AI 客户端可读组合并导入活动）。

对本项目的裁定：费用已逐笔入模拟账本（calculate_costs），但**真实账本的 record_trade 已支持 fee/tax 参数而回填命令从未传**——用户回填时可以不填费用，账本就少记。补齐点在回填入口加可选费用参数，非新建模型。

### 3. vnpy_riskmanager：事前风控的规则引擎形态

来源：github.com/vnpy/vnpy_riskmanager README + engine.py（成功取证）。

内置五规则（源码枚举级证据）：

| 规则 | 语义 |
|---|---|
| ActiveOrderRule | 活动委托数量上限（任何时刻未成交委托总数） |
| DailyLimitRule | 全天委托/撤单笔数上限 |
| DuplicateOrderRule | 极短时间窗内同合约同方向同价格的重复报单拦截 |
| OrderSizeRule | 单笔委托最大手数（防乌龙指） |
| OrderValidityRule | 委托指令合法性预检 |

架构：规则引擎 + 规则模板可插拔 + 启动时自动加载 + 触发写日志（source=RiskEngine）。

对本项目的裁定：本项目已有闸门（新鲜度/生命周期/现金/暂停）相当于 OrderValidityRule；**缺的是 OrderSizeRule（单笔量上限）与 DuplicateOrderRule（重复指令）**——后者我们已在回填侧做了（幂等键），前者在生成侧没有。半自动系统下单是人执行，但**生成的操作单本身应有单笔/单日上限**。

### 4. 访问受限的来源（取证边界，不引用）

- 知乎 API：403；
- 集思录转债列表页：仅返回站名，无帖文内容——**不虚构"社区共识"**；
- BigQuant 指定文档：404；
- GitHub 网页搜索/code search：限流或反爬；
- vnpy 官网文档页、Qlib account 文档页：404。

国内社区关于"双低失效/散户仓位"的讨论观点，本项目此前三路调研（CB-RESEARCH-PLAN 记录）已有带 URL 的沉淀，本轮不重复也不新增未经取证的社区结论。


### 5. 知乎社区调研（2026-09-13 补充，经 zhihu-search API 取证，原文摘录留档 docs/zhihu_broad_20260913.json）

五路搜索 40 条结果 + 三主题深读，四个高价值发现（均含原文佐证）：

**发现 A：独立社区作者与我们的双低失效归因独立收敛**（《老爷，转债的时代变了》）
- 该作者 2026 年实测："经典双低策略跑不赢基准"，持仓归因发现反复踩雷**三房/帝欧/文科/宏图/首华**——共同特征是正股基本面差，"双低值低不是因为性价比高，而是因为正股跌、溢价被动扩大"，命名**"双低陷阱"**。
- 与本项目 D3 判定（crowding_silent + 双低陷阱混入）独立收敛——两个不同数据源得出同一病理，交叉验证成立。
- 该作者的修复方向：加正股质量/信用风险/流动性风控因子。注意：这属于**新研究线**，按预注册纪律不并入当前候选。

**发现 B：转债市场三阶段演变史**（《可转债市场的演变过程》）
- 2018-2021 双低年化 25%+；2022 新规（涨跌幅限制+10万门槛）后日均成交 2080亿→500亿；低价 AA- 债首次批量跌破债底。
- 印证：双低的黄金期是特定制度环境产物，2022 制度断点后生态已变——支持"结构变化而非周期"的判断。

**发现 C：散户复盘方法论与我们的影子期设计精确对齐**（《复盘的本质不是写小作文进行"态度反省",而是量化"执行偏差"》）
- 核心观点："为每次偏离计划的动作打上标签，让系统自动统计各类违规次数"——"提前平仓"本月出现 15 次的客观数据"比一万字反省更管用"。
- **本项目影子期周报（执行率/滑点/放弃率/量价门拦截）正是这套方法论的实现**；补充可借鉴点：偏差应打标签分类计数（提前执行/超量/改价/放弃），而非只看汇总比率——列入 E1 批次顺手项。

**发现 D：新手量化七大误区清单与我们已修复项高度重合**（3 赞回答）
- 误区四"忽略交易成本"（建议真实费率 1.5 倍+滑点 0.1-0.2%+只假设 80-90% 成交）→ 我们的 A1/F6 成本敏感性已覆盖，**"只假设 80-90% 成交量"是未做的**，列入影子期后评估；
- 误区五"只看收益不看风险暴露"（选股收益占比<50% 说明赚的是风格钱）→ 归因体系待影子期数据；
- 误区六"单策略仓位不超 30%、运行 3 个以上低相关策略"→ 对 1 万元账户不现实（资金量只够一个策略），**有意识不采纳**，记录为已知分歧；
- 误区七"不记录滑点不检查执行率"→ 影子期周报已覆盖。

**社区估值快照**（2026-06/08 帖）：转债中位数 132.46、历史百分位 93.03%——与我们 E2 动态重算 88.8% 同方向，第三方佐证当前高估区间判断。

### 6. GitHub 认证调研补齐（2026-09-13，Fine-grained PAT 实测）

- 用户配置了 Fine-grained PAT（仅本仓库 Contents/Issues/PR 读写），REST code search 实测可用；MCP（github remote server）已写入用户级配置 `~/.zcode/cli/config.json → mcp.servers.github`，下次会话加载。
- **盈亏平衡展示先例**：搜到多个个人交易账本项目（Trading-tiding 的 portfolio/book.py、PlainlyDigital 的 PORTFOLIO-COST-ANALYSIS 文档等）——"把往返成本换算成需涨 X% 回本"是社区常见做法，非本站独创。
- **订单状态机先例**：lumibot（成熟框架）源码确认状态集 `unprocessed / partial_fill / fill / canceled` + 别名归一表——与本项目批次 A 实现的 `pending/partially_filled/filled/cancelled/expired` 完全同构（多一个 expired 是我们的单日限价单语义）。批次 A 状态机设计获行业先例背书。

### 7. GitHub 认证深挖第二轮（2026-09-13，code search 全量补齐 E1-E3 先例）

**G1 成本门 → ToFu fee_book.py（NiuTrans/ToFu，教科书级同构）**
- 纯函数费率计算 `compute_fee(schedule, amount, action)`——零 I/O、零时钟、零全局，"Determinism is the contract"；
- 费率表即不可变数据：`commission_rate / min_commission / transfer_fee_rate` + source 标注（"A-share published structure: commission 0.025% (min ¥5)"）；
- `commission = max(amount * rate, min_commission)` 地板语义与我们逐字相同；源码原句 "**The ¥5 commission floor makes the effective rate a function of trade size**"——正是成本门要展示的核心事实；
- 债券费率注释明确 "commission only, no stamp tax. Critically NOT the fund path"——与我们 RuleBook cb 免印花税一致（另一处独立佐证 E1 旧版对转债收印花税确为 bug）。
→ **E1 成本门实现方向：复用 calculate_costs 纯函数 + 费率表数据化，不引入新依赖。**

**G2 事件账本 → liuxie066/options-monitor repository_core.py（E2 完整蓝图）**
- SQLite `trade_events(event_id PK, event_json)` 事件表 + `position_lots` 投影表——事件先行、投影后生成，与 Fowler 理论的落地形态；
- **append-only 用 SQLite 触发器硬保证**：`BEFORE UPDATE/DELETE ... RAISE(ABORT, 'trade_events is append-only')`——不靠纪律靠数据库约束；
- 事件带 `payload_hash` + `event_schema_version`（'wheel_event.v1/v2' CHECK 约束）——schema 演进有版本；
- 迁移模式：建新表→逐行迁移→回读比对→原子换名（readback mismatch 即 abort）。
→ **E2 快照终局的最省力路径确认：真实账本已具备事件形态，补 payload_hash 与 append-only 触发器即可，不必新建架构。**

**G3/G4 行为纪律 → 两个直接可用先例**
- **HKUDS/Vibe-Trading（22k star）shadow_account/reporter.py**：影子账本报告含 **delta 归因瀑布**——Noise Trades / Early Exit / Late Exit / Overtrading / Missed Signals 五类行为偏差各自计 PnL，与我们影子期周报同概念且更细；
- **trade-performance-coach（claude-trading-skills）**：30KB 复盘引擎——BehaviorTag 标签化（fomo_entry/revenge_trade/stop_moved/size_creep/rule_drift）+ 每标签配反思问题 + **由标签自动生成下一场操作规则**（如 size_creep → "未来两笔风险上限 0.5R"）+ verdict 分级含 COOL_DOWN（强制冷静期）。
→ **E1 的偏差标签化照此收敛**：放弃/超量/改价/延迟四类标签 + 各自损耗 PnL，量价门已有数据源。

**G5 仓位上限**：多个个人回测项目有 max_position_pct 实现（常规工程），无新洞察。

**调研结论**：E1-E3 全部批次现在都有源码级先例背书，且三个先例（ToFu/options-monitor/Vibe-Trading）的实现哲学与我们既有代码同路（纯函数、事件先行、偏差量化）——不是推翻重来，是按蓝图补齐。

## 二、本地实证（本轮新增）

- **成本门数据全就绪**：mandate.yaml 费率与回测同源；实测 118031 买 10 张 @119.145 → 买费 5.02 + 卖费 5.62 = 10.64 元，往返成本 0.893%，**需涨 0.89% 回本（含双边滑点 1.09%）**——最低佣金 5 元在 1,191 元小单上占绝对主导。这正是用户语言的成本门。
- 用户真实组合集中度 91.7%（002475）已由 /健康 风险行每日暴露。

## 三、解决计划（按依赖排序）

### 批次 E1：成本门 + 生成侧风控上限（小活，1-2 天）

1. 操作单每笔订单附成本行：`往返成本 ¥10.64（0.89%）→ 需涨 0.89% 回本（含滑点 1.09%）`；预期收益无法证明覆盖成本时明确输出"不值得交易"提示（不阻断——判断留给用户，但数字必须给全）。
1b. 影子期偏差标签化（知乎方法论落地）：周报的放弃/超量/改价从汇总比率细分为分类计数——放弃(主动)/超量执行/改价执行/延迟回填 各自计次与损耗，让"违规统计"可见。
2. 生成侧 OrderSize 语义：单笔订单金额上限（防异常数据生成的巨量单，如价格错 100 倍时 10 张变 1 万张的场景）。
3. 真实账本回填支持可选费用参数（对齐 Ghostfolio 的 fee 一等字段语义），不填则按费率模型估算并标注 estimated。

验收：118031 单显示 0.89% 回本；伪造价格异常数据被单笔上限拦截；带费用回填入账。

### 批次 E2：快照终局架构（中活，3-5 天，方案 v2 收官）

1. 写入顺序规范化：**事件先行**——真实账本（SQLite）是唯一事实源；操作单 JSON、快照、通知总线全部是"事件之后的读模型"，任何读模型失败不阻塞事件。
2. 快照不可变化：decision_snapshot 加 content_hash；下游（/健康、runtime/status、通知 logical_id）引用 snapshot_id 而非各自读文件。
3. 时间点查询能力：`/操作单 历史 <日期>` 读归档快照（已有 90 天归档，补读取入口）。
4. 崩溃恢复语义：半写入的读模型（操作单写了、快照没写）在下次刷新自动收敛——已由"每次操作单生成后刷新快照"保证，补故障注入测试锁定。

验收：人工删掉 latest.json 后系统正常（可重建）；同一 snapshot_id 在 /健康、runtime/status、操作单一致；伪造半写状态后自动收敛。

### 批次 E3：研究/交易信号状态体系（小活，1-2 天）

plan targets 每标的加状态标注（对齐 FIX OrdStatus 的枚举纪律 + 本项目操作单已有状态机）：

`research_candidate / paper_candidate / executable / blocked(原因) / expired / manual_review`

操作单只渲染 `executable`；其余状态显式列出原因。消除"current_plan 里出现的就是能买的"歧义。

### 批次 E4：影子期运行（等待数据，8 周不写新功能）

触发条件：E1-E3 完成 + 用户第一笔回填。期间唯一代码动作是修真实 bug。

## 四、规划总表（取代此前所有版本路线图的执行层）

| 周 | 内容 | 交付判据 |
|---|---|---|
| W1（本周） | E1 成本门+风控上限；E3 信号状态 | 操作单含回本涨幅；状态无歧义 |
| W2 | E2 快照终局 | 半写恢复测试通过 |
| W3-W10 | 影子期 + ytm_true 预注册实验（一次） | 周报指标连续积累；实验结论 pass/fail 落档 |
| W11-W12 | promotion gate 评估 | 四硬门槛判定，三选一路线裁决 |

**三选一裁决**（沿用最终方案，不因工程完善度而动摇）：
1. 全过 → 小额人工执行继续；
2. 账本/监控有价值但策略不过 → 降级为持仓监控工作台（Ghostfolio 路线）；
3. 都不值得 → 保留数据资产，停止交易自动化。

## 五、对本轮调研的自我约束

用户核心担忧"把工程完整误认为交易正确"成立且必须制度化：**E1-E3 是有外部先例背书的最小必要工程（成本显示是 Ghostfolio 级标配、风控上限是 vnpy 级标配、快照语义是 Fowler 级正确性），此后进入 8 周功能冻结**——影子期内出现的任何新工程需求必须由真实执行数据触发，而不是由"还能更完善"触发。

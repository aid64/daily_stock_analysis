# 最终修改计划（2026-09-11）

> 面向目标：1 万元个人账户、低频交易、人工在券商 App 下单、飞书接收一张重点明确的决策包、成交后回填并比较模拟/真实账本。
>
> 本计划只定义修改范围、依赖和验收，不把“能运行”当作“能交易”，不启用真实自动下单。

## 一、先定产品边界

项目最终定位为“个人账户的证据驱动型低频决策工作台”，不是自动赚钱策略，也不是高频交易系统。

用户每天只应看到三类信息：

1. **今日结论**：交易 / 不交易 / 仅观察；
2. **执行项**：最多几条，具体数量、限价、放弃条件、撤单条件和预计成本；
3. **风险项**：数据过期、强赎/退市、现金异常、未覆盖持仓、通知或回填异常。

多策略只在研究层比较，用户层只接收一份组合级决策包。

## 二、证据结论分级

### 已证实

- 月频转债基线 1×成本约 +15.7%，但换手约 52×；2×成本总收益约 -22.0%。
- 季频版本换手约 18.2×，最大回撤约 14.8%；2×成本仍约 -2.1%。
- 双低因子已不应继续担任主策略；E1 复合、E3 下修、F8 止损实验均未通过预注册门槛。
- E2 估值择时通过预注册门槛，但只是仓位层；当前估值分位约 95.1%，应偏防守。
- Tushare 账户实测可调用 `cb_daily`、`cb_call`、`cb_share`、`cb_basic`；`cb_call` 明确记录 123089.SZ 已公告实施强赎并退市。
- PIT 增量曾造成截面从 300 只降到 17 只；全量刷新后恢复到约 283 只。
- DSA 已具备飞书 Stream、操作单、成交回填、模拟/真实账本、摘要总线、告警聚合、Decision Snapshot 基础实现。

### 仍不可信或待验证

- 策略在真实最低佣金、滑点、部分成交和人工执行下的净收益。
- current_plan 是否在每次数据刷新后都使用最新完整截面。
- 强赎、到期、停牌、退市和长期无行情是否覆盖全部导出入口。
- 股票与转债混合后的整体风险预算、现金占用和相关性。
- 预测收益与真实成交收益的长期偏差。
- 用户实际执行、放弃、临时改价和漏回填造成的行为偏差。
- 单一 Decision Snapshot 是否已成为所有用户可见状态的真正唯一事实源。

## 三、P0：数据和交易安全（第 1 批）

### P0.1 PIT 完整性与刷新编排

修改范围：

- `quant_research/scripts/update_pit_tail.py`
- `quant_research/scripts/build_pit_snapshot.py`
- `daily_stock_analysis/src/services/daily_pipeline_service.py`

实施：

1. 工作日增量继续优先更新当前目标债，但不得把部分日期写成完整 PIT 尾部。
2. 周日全量刷新；平日增量成功后仍执行 assemble/export。
3. assemble 计算每日截面完整度；尾部日期少于前一完整交易日 60% 时裁掉。
4. 增加“连续两日完整截面”或明确全量刷新完成标志，避免仅凭最新日期判断数据健康。
5. pipeline_status 记录：raw 更新数量、assemble 末端、完整截面数量、被裁掉日期、覆盖率告警。
6. PIT <200 只时不生成可执行买单；可继续生成数据告警和“今日无法执行”状态。

验收：

- 模拟只更新 18 只目标债时，PIT 末端不前移到该部分日期；
- 完整日恢复到 283 只后，plan 才允许使用该日期；
- 断点重启不会破坏已有完整 parquet；
- raw 已更新、assemble 失败、export 失败三种状态在 `/健康` 中可区分。

### P0.2 生命周期硬阻断覆盖所有入口

修改范围：

- `quant_research/scripts/run_paper_tracking.py`
- `quant_research/quant_research/backtest/convertible/picks.py`
- `quant_research/scripts/build_pit_snapshot.py`
- 相关 `tests/`

实施：

1. `cb_call` 中“公告实施强赎”“公告到期赎回”以及已过 `call_date` 的标的进入 blocked。
2. `cb_basic` 的 `delist_date`、`maturity_date` 进入 blocked。
3. 最新有效行情超过允许陈旧天数的标的进入 blocked。
4. blocked 标的不得进入 `targets`；存续权重重新归一化。
5. plan 写入 `blocked`、`blocked_reason`、生命周期数据版本。
6. 历史回测和实盘导出明确区分：回测按 PIT 当时可知信息，当前操作单按最新生命周期硬阻断，不能把事后信息倒灌进历史结果。

验收：

- 123089.SZ 在当前计划中不存在于 targets，只出现在 blocked；
- 4 只存续目标权重合计为 1；
- “公告不强赎”不被误阻断；
- 缺少生命周期表时系统进入 manual_review/blocked，而不是静默放行。

### P0.3 计划新鲜度和价格一致性

修改范围：

- `daily_stock_analysis/src/services/decision_snapshot_service.py`
- `bot/commands/order_sheet.py`
- `src/services/order_sheet_service.py`

实施：

1. 统一 freshness evaluator：PIT 末端、plan generated_at、assemble 时间、价格日期必须通过同一服务判断。
2. 每个 target 保存 `price_date`、`price_source`、`is_realtime`、`data_version`。
3. stale、partial、missing、lifecycle_blocked 状态均不可生成买入指令。
4. 旧 pending 操作单若对应 plan 版本过期，必须标记为 expired/manual_review，不能继续展示为可执行。
5. 计划和操作单绑定 `snapshot_id`，成交回填必须校验 snapshot/order version。

验收：

- 把 pit_end 改旧、generated_at 改旧、assemble 时间改旧，各自都能拒绝生成指令；
- 操作单文本明确显示数据日期和版本；
- 旧单不会覆盖新计划；
- 价格来源缺失时只输出人工核验。

## 四、P0：现金、持仓和订单状态

### P0.4 现金安全闸门

修改范围：

- `daily_stock_analysis/bot/commands/health.py`
- `daily_stock_analysis/bot/commands/order_sheet.py`
- `daily_stock_analysis/bot/commands/portfolio.py`
- `src/services/portfolio_service.py`

实施：

1. 把现金状态拆成：未登记、正常、为负、来源过期、与券商不一致。
2. 当前 `-12,390` 不是“未登记 ¥0”，而是交易记录已存在但资金入账缺失；健康页必须明确这一点。
3. 现金状态不是正常时，买入目标统一转为 blocked/manual_review，不生成买入数量。
4. 支持明确的入金登记事件：金额、日期、账户、来源、备注、幂等键。
5. 现金恢复正常后重新生成新的 decision snapshot 和操作单，不修改历史快照。

验收：

- 负现金时操作单无买入订单；
- 登记入金后买入数量按可用现金重新计算；
- 重复入金事件不会重复增加现金；
- 现金异常在飞书 `/健康`、Web 状态和快照中一致。

### P0.5 订单状态机和回填幂等

修改范围：

- `src/services/order_sheet_service.py`
- `bot/commands/fill.py`
- `src/services/decision_snapshot_service.py`
- API fill endpoint

状态：

`planned → submitted → partial → filled`  
`planned/submitted/partial → cancelled/expired`  
`任何状态 → manual_review`（发现版本、现金、数量或价格异常时）

实施：

- 一笔订单使用 `order_id` 和 `client_order_id`；
- 回填使用 `fill_id`/dedup hash，重复消息不重复记账；
- 支持部分成交、剩余数量、撤单和过期；
- 实际成交数量不能超过计划剩余数量，除非用户明确使用“计划外成交”入口；
- 真实账本、模拟账本、快照都记录版本关联。

验收：

- 重复 `/成交回填` 不会重复增加持仓或现金变化；
- 部分成交后再次查看操作单显示剩余数量；
- 已取消订单不能再次被当作 pending 回填；
- server 重启后状态机可恢复。

## 五、P1：Decision Snapshot 真正成为唯一事实源

### P1.1 快照内容

快照必须包含：

- `snapshot_id`、`trade_date`、`generated_at`；
- plan schema、plan version、strategy version、PIT hash、PIT end、截面数量；
- targets、blocked、stale、no_action_reason；
- 所有真实持仓、现金和账户；
- order sheet、订单状态、成交回填；
- notification logical IDs、发送状态和摘要索引；
- warnings、quality、correlation_id。

### P1.2 接入范围

- daily pipeline 导出后刷新；
- `/操作单` 只读或生成当前快照；
- `/健康` 只读当前快照；
- Web runtime status 只读当前快照；
- Desktop 托盘只显示快照状态；
- `/对比` 使用同一 snapshot/order/fill 关联。

### P1.3 快照测试

故障注入：

- 写 tmp 后进程退出；
- parquet 读取失败；
- plan 读取成功但持仓读取失败；
- 操作单保存成功、快照写失败；
- 快照写成功、通知发送失败；
- 同时两个进程刷新；
- archive 文件损坏或旧日期文件存在。

要求：所有失败都能回退到明确的 manual_review/unknown，不能标成 fresh=true。

## 六、P1：用户每天只收一份组合决策包

### P1.1 组合结论格式

固定结构：

```text
【今日决策包 日期 / snapshot_id】
结论：交易 / 不交易 / 仅观察
数据：PIT 末端、价格日期、数据源、质量
资产：股票、转债、现金、风险占比
执行：最多 N 条，数量、限价、放弃条件、撤单时间
阻断：强赎/退市/stale/现金/成本/风险预算
告警：按标的+方向+类型聚合
回填：上一单未完成项
```

### P1.2 通知时序

- 操作单：盘前立即发送，同时写摘要索引；
- 盘中告警：即时发送，同一逻辑事件 24 小时冷却；
- 晚间摘要：只发送一条，按逻辑事件渲染；
- 测试消息：`test_mode` 或隔离 namespace，绝不写生产总线；
- 一个 logical_id 只允许一条当前版本，刷新时替换。

### P1.3 焦虑控制指标

每周统计：

- 用户收到消息条数；
- 重复消息数；
- 丢失消息数；
- 操作单打开/回填/放弃情况；
- 从消息到人工确认的时间；
- 用户是否因信息过多而忽略操作单。

目标：每日生产消息不超过一张操作单 + 必要的标的级告警 + 一条摘要。

## 七、P1：统一组合覆盖和全市场扫描

### P1.1 覆盖状态

对 `STOCK_LIST`、真实持仓、转债目标统一生成覆盖矩阵：

| 标的 | 每日分析 | 事件告警 | 组合风险 | 有限价能力 | 状态 |
|---|---|---|---|---|---|
| 000899 | 是 | 已配置 | 是 | 股票参考价 | 观察/告警 |
| 002475 | 是 | 未配置阈值 | 是 | 股票参考价 | 观察 |
| 转债目标 | 是 | 生命周期 | 是 | T-1 限价 | 可执行/阻断 |

### P1.2 全市场扫描漏斗

不做“全市场所有股票都交给 LLM”。采用：

1. 交易日/市场过滤；
2. 流动性和价格质量过滤；
3. 技术/事件候选预筛；
4. 风险和生命周期过滤；
5. 统一排序与组合风险预算；
6. 最多少量进入 LLM 解释层；
7. 最终只有组合决策层可以产生操作单。

验收：固定时间内输出候选数量、过滤原因、最终入选数量和数据完整度；扫描超时只降级，不阻塞持仓风险和操作单。

## 八、P1：风险预算与“不要交易”能力

新增组合级确定性风险裁决器：

- 单一标的上限；
- 股票总仓位上限；
- 转债总仓位上限；
- 高相关资产合计上限；
- 事件风险仓位上限；
- 最低现金比例；
- 单日最大损失；
- 组合回撤暂停线；
- 预计收益低于盈亏平衡成本时不交易。

每一笔订单必须计算：

- 全部交易成本；
- 盈亏平衡涨幅；
- 预计持有期；
- 资金占用；
- 最坏情景损失。

若没有足够证据，最终结论应为：

```text
今日结论：不交易
原因：数据/成本/风险预算未通过，保留现金。
```

“不交易”是正常决策，不是系统异常。

## 九、P1：LLM 边界和降级

LLM 只能：

- 总结已取得的数据；
- 解释已确定的信号；
- 整理新闻和风险；
- 生成固定格式自然语言。

LLM 不能：

- 修改目标权重；
- 绕过生命周期阻断；
- 猜价格或现金；
- 决定买入数量；
- 自由合并多套策略；
- 在数据缺失时生成“看起来完整”的建议。

主力当前为方舟 `deepseek-v4-pro-ga-260813`，备用为 tokenrhythm `glm-5.3-flash`。备用模型必须消费和输出同一个结构化结果，切换时交易事实不能改变。主力与备用均失败时，系统输出数据/模型不可用，不生成买卖指令。

## 十、P1：研究验证与 promotion gate

### 10.1 只保留一条候选交易路径

当前候选：

- 季频转债；
- 固定候选规则；
- E2 估值择时作为仓位层；
- 生命周期硬阻断；
- 现金和成本闸门；
- 人工限价执行。

双低、E1、E3、F8 保留为历史证据，不继续调参；ytm_true 只能在预注册后作为唯一新增研究线。

### 10.2 研究产物

每个季度必须有：

- equity_curve；
- fills_vs_plan；
- cost_vs_model；
- turnover/exposure；
- benchmark comparison；
- data hash；
- parameter manifest；
- holdout 状态；
- 结论 `pass/fail/manual_review`。

### 10.3 准入门槛

进入真实人工影子期：

- 保守成本档不出现结构性负收益；
- 12 个月 paper tracking 净收益为正且非单月贡献；
- 最大回撤建议 ≤15%；
- 年化换手 ≤25×；
- 数据完整度和 stale 阻断稳定；
- 不存在未解释的现金/持仓差异。

进入资金扩大阶段：

- 执行率 ≥90%；
- 预测与实际年化差 ≤5pp；
- 实际回撤 ≤纸面 1.5×；
- 2×成本 + 10bp 滑点下收益不翻负；
- holdout 未参与选参；
- 可由锁定依赖、参数和数据 hash 重建。

任一失败：保持 paper-only，不扩大资金，不自动下单。

## 十一、P1：全局暂停和故障注入

增加业务级：

- `/暂停交易`；
- `/恢复交易`；
- Web/Desktop 暂停开关；
- 暂停后继续更新数据和健康状态，但不生成新买卖指令；
- 暂停原因、操作者、时间和恢复条件写入快照。

故障注入测试覆盖：

- Tushare 402/超时/空字段；
- AkShare 旧数据；
- PIT 部分写入；
- assemble 失败；
- plan 版本错配；
- 主力 LLM 超时、备用 LLM 格式不同；
- 飞书发送成功但状态写失败；
- 飞书重试；
- 重复回填；
- 部分成交；
- 现金入账顺序颠倒；
- server 崩溃后恢复。

## 十二、12 个月修改顺序

### 第 1 个月：安全地基

- PIT 完整性和刷新编排；
- 强赎/退市/到期阻断；
- 计划新鲜度；
- 现金硬闸门；
- Decision Snapshot 完整接线；
- `/暂停交易`。

### 第 2—3 个月：稳定影子执行

- 每天一张决策包；
- 手动下单和回填；
- 部分成交/撤单/过期状态机；
- 通知重复、延迟、丢失统计；
- 每周执行偏差报告；
- 期间禁止策略调参。

### 第 4—6 个月：研究真实性

- 补齐 2026 paper tracking 三表；
- 成本/滑点/暴露/基准报告；
- 完整 holdout 评估；
- 唯一 ytm_true 预注册实验；
- promotion gate 自动判定。

### 第 7—9 个月：组合覆盖

- 000899/002475 组合风险覆盖；
- 股票低频策略独立预注册；
- 全市场扫描漏斗；
- 不扩大自动执行权限。

### 第 10—12 个月：路线裁决

- 全部 gate 通过：继续小额人工执行，评估人工审批式半自动接口；
- 部分通过：保留研究工作台和风险监控，停止资金扩大；
- 失败：降级为个人投资记录、数据和持仓监控系统，停止策略交易推进。

## 十三、保留、冻结、删除

### 保留

- PIT、RuleBook、Tushare/ AkShare 适配；
- 预注册和失败记录；
- E2 仓位层；
- paper tracking 三表；
- Decision Snapshot；
- 操作单、回填、账本；
- 飞书单通道、告警聚合和运行健康。

### 冻结

- 双低继续调参；
- E1/E3/F8 继续开发；
- 盘中高频/事件套利交易；
- 多策略并行推送；
- 真实自动下单；
- backtrader 迁移；
- 更多 LLM 和更多通知渠道。

### 删除/清理

- 已废弃的临时脚本、构建副本和重复研究产物；
- 无实际消费者的配置项；
- 将“路线图声称完成”但没有产物的文档表述改成真实状态。

## 十四、当前最先开工的 10 个任务

1. 把 `cb_calls.parquet` 生命周期接入 `pit_snapshot_picks` 或统一导出前过滤层；
2. 为 `current_plan.json` 增加 `plan_version`、`blocked`、`data_version`、`price_date`；
3. 把全量刷新、assemble、export 合并成一个可重试编排入口；
4. 为 assemble 增加部分日裁剪和完整度报告；
5. 把现金负数从提醒升级为买入硬阻断；
6. 让 Decision Snapshot 成为操作单、健康、Web runtime 的共同读取源；
7. 给订单增加 order_id/client_order_id 和 fill 幂等键；
8. 增加 `/暂停交易`/`/恢复交易`；
9. 将操作单改成固定“今日结论 + 执行 + 阻断 + 回填”格式；
10. 先跑 8—12 周不调参影子期，再决定是否做股票策略和执行接口。

## 十五、最终成功标准

系统成功不是每天输出更多内容，而是：

- 每天只有一份清晰的组合决策包；
- 没有陈旧数据生成的订单；
- 强赎/退市/现金/成本异常会阻断交易；
- 用户知道今天为什么交易或不交易；
- 成交回填可靠且幂等；
- 模拟和真实账本长期可解释；
- 研究结论可重建；
- 任意异常可暂停和回滚；
- 没有通过准入门槛时，系统主动保持 paper-only。

## 十六、一手资料与本地证据

- Tushare 官方数据接口文档：<https://tushare.pro/document/2>；`cb_daily` 页面实测说明 2000 积分调用门槛、单次最多 2000 条，并列出 OHLCV 等字段。
- Tushare 实际接口调用：`cb_daily`、`cb_call`、`cb_share`、`cb_basic`；当前账户实测成功。`cb_call` 返回 123089.SZ：2025-09-16 公告实施强赎、2025-10-15 赎回；`cb_basic` 返回退市日 2025-10-23。
- AkShare 官方仓库元数据：<https://api.github.com/repos/akfamily/akshare>；本地实测 `bond_zh_hs_cov_daily` 可返回转债 OHLCV。
- Qlib 官方文档：<https://qlib.readthedocs.io/en/latest/>；定位为量化研究、数据、模型、组合和回测平台，不等同于券商实盘执行保证。
- vn.py 官方仓库元数据：<https://api.github.com/repos/vnpy/vnpy>；活跃的交易平台框架，只有在研究和人工影子期通过后才评估，不作为当前直接引入依赖。
- Backtrader 官方仓库元数据：<https://api.github.com/repos/mementum/backtrader>；定位为 Python 回测库，当前计划暂不迁移。
- 本地 `quant_research/output/`：A1/A2/E1/E2/E3/F8/滑点/费用/暴露报告。
- 本地 `daily_stock_analysis/data/cache/notification_bus/`：重复消息和人工验证污染的直接证据。
- 本地 PIT 截面实测：增量断裂后 300→56→17，全量刷新后恢复约 283；这是 P0 数据管道问题的直接证据。

交易所部分官方页面在本轮网络环境中多次 404/超时，因此制度数字以仓库 RuleBook、Tushare/证监会公开数据字段和本地测试交叉验证；正式上线前应将实际使用券商的交易规则、佣金、最低收费和可转债交易限制作为账户级配置与验收输入。

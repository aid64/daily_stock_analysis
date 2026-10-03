# 多策略对比盘（Compare Deck）运维说明

> 2026-09-30 建立。执行引擎：`scripts/compare_deck_runner.py`（确定性 Python，单测 `tests/test_compare_deck_runner.py`）。
> 本文档是对比盘的运维真源；策略证据与调研结论见 `.scratch/dsa-strategy-validation/*.md`（本地任务目录，不入库）。

## 1. 槽位与资金

每槽起始本金 ¥12,411（Y 槽逆回购名义 ¥12,000+现金 ¥411）（前向纸面跟踪：真实价格/整手/佣金，非券商下单）。etf_mwu_cb（MWU+转债 PIT 专家，月频）因需全池转债日线暂未入槽，待数据成本评估后跟进：

| 槽位 | 策略 | 调仓 | 备注 |
|---|---|---|---|
| A_动量 | 17池ETF 60日动量+MA250 top3 | 周一收盘轮 | 货币ETF腿以现金持有 |
| B_轮动 | 3资产25日动量+绝对过滤 | 周一收盘轮 | 全负→空仓 |
| C_沪深300持有 / D_红利低波持有 | 被动基准 | 无 | |
| E_可转债双低 | quant 引擎导出目标 | 月度调仓窗（本月首个交易日，trade_cal 判定——1-5 日旧窗在 10 月等长假月会错过调仓，已废弃） | 信用列每轮更新（<100元减半仓） |
| G_MWU融合 | `quant_research/scripts/extract_mwu_targets.py`（backtrader 探针提取，引擎口径） | 周一收盘轮比对目标变化 | MWU 融合动量/均值回归/防御三专家；转债现价走腾讯直连 |
| I_PEAD业绩预告 | tushare forecast 最近40天预增(同比≥50%)等权5只 | 月度调仓窗 | 学术最强异象；预告季(1/4/7/10)密集 |
| J_短期反转 | 21日跌幅最深5只（流动性过滤） | 月度调仓窗 | 与A动量负相关；接飞刀风险已知 |
| L_北向择时 | 北向净买入250日分位（moneyflow_hsgt） | 每日收盘轮 | 与C同标的受控实验：择时vs买入持有 |
| M_筹码集中度 | 股东户数降幅≥10%（stk_holdernumber 季度） | 月度调仓窗 | A股行为因子；降幅榜头部庄股风险靠过滤链 |
| N_技术形态对照 | Sequoia-X 七形态扫描（命中≥2优先） | 月度调仓窗 | 条件式：数据回填≥300万行才生效 |
| H_MWU_CB融合 | 同上（etf_mwu_cb 四专家含转债 PIT 双低，探针不执行交易故无需转债 feed） | 月度调仓窗 | 转债专家可空选→现金；与 E 槽同池不同权重机制 |
| F_小市值20 / F2_神帖对照 | tushare 流通市值升序 | 月度窗 | F 剔次新、单票≤¥2500；F2 主板不剔次新（对照）；均熔断跟随 |
| Y_确定性收益 | 国债逆回购(节前/月末 GC001) | 节前最后交易日 14:30 后 | 打新走 dsa 每日待办提醒；逆回购利息=名义×年化×计息天/365 |
| X_事件套利 | 要约收购/现金选择权/吸收合并 | 事件驱动 | 价差规则：现价<对价×0.98(ST 0.95)，单笔≤¥1,241 |

## 2. 调度与时间

- Windows 计划任务 `DSA_Deck_Intraday`：每 20 分钟 → `compare_deck_task.ps1 -Mode intraday`
- 收盘轮不挂绝对时刻：盘中轮在**交易所时间 ≥15:00** 且当日未跑过（`_control.close_done` 幂等键）时自动触发——本机时钟不可信也无影响
- 时段/日期判断：**交易所行情时间戳**（腾讯字段[30]）+ tushare `trade_cal` 交易日历（进程内缓存）；本机时钟仅兜底
- 手动执行：`py scripts/compare_deck_runner.py --mode intraday|close [--dry-run] [--force]`

## 3. 数据源

- 实时行情：`data_provider.DataFetcherManager.get_realtime_quote`（六源降级+熔断；tushare 为 P-1 最高优先级，15000 积分账户）；prefetch 按 5 只分批（tickflow 上限）
- 转债现价：腾讯直连 `tencent_quotes()`（六源链对带后缀转债代码覆盖不到）
- A/B 历史信号：只读 `quant_research/output/feeds_snapshot_v*.pkl`（跨项目文件契约）；超过 7 天自动调 `build_feeds_snapshot.py` 重建 v5，失败沿用旧快照并标注
- 小市值选股：tushare `daily_basic` + `stock_basic`（月度窗，trade_date=上月末）

## 4. 消息（飞书，route_type=strategy_compare）

- 收盘轮每日一条：排名 + 今日操作 + **每槽持仓明细（数量/成本→现价/盈亏%）** + 风险状态
- 调仓操作单、熔断、信用告警、事件建仓/否决/观察跟踪：发生即推
- 无事不发；dedup_key 防重（`deck_close_summary_YYYY-MM-DD` 等）
- 每日 18:00 聚合摘要已关闭（`NOTIFICATION_DAILY_DIGEST_ENABLED=false`）

## 5. 状态与审计

- 台账：`.scratch/dsa-strategy-validation/compare_strategies.json`（原子写 + 写前 `.json.bak` 滚动备份）
- 控制键 `_control`：`close_done`（收盘幂等）/ `event_scan_last`（事件扫描每半天一次）/ `rebalance_done`（月度幂等）/ `_actions`（当日动作，盘中+收盘合并）
- 执行日志：`logs/compare_deck_task.log`（start/exit + runner 输出）
- 病历制：槽位停跑/清仓 → `.scratch/dsa-strategy-validation/{槽位}_postmortem.md`

## 6. 回滚

- 停执行：`schtasks /Delete /TN "DSA_Deck_Intraday" /F`
- 删执行器：`scripts/compare_deck_runner.py`、`scripts/compare_deck_task.ps1`、`tests/test_compare_deck_runner.py`
- 恢复摘要：`.env` `NOTIFICATION_DAILY_DIGEST_ENABLED=true`
- 台账不受影响（历史净值保留）

## 7. 边界

绝不真实下单；主动策略晋升真钱按三关（滚动60日跑赢沪深300 + 三窗口切片为正 + 扣成本为正）；确定性通道（事件套利/打新/逆回购）真钱优先但单笔≤总资金10%。

## 7a. 调度细节（2026-10-02 诊断后）

- **弹窗修复**：所有 `DSA_*` 计划任务动作经 `scripts/run_hidden.vbs`（SW_HIDE 在 CreateProcess 阶段生效，绕过 Windows Terminal 默认终端接管）——审计确认执行链完好
- **电池策略已解除**：`DisallowStartIfOnBatteries/StopIfGoingOnBatteries = False`——交易时段盘中风控（分级熔断/E2/信用）不因用电池停摆；代价是电池续航，回滚=改回 True
- **补跑突刺**：插电瞬间错过的高频任务会立即补跑（run_hidden 已消除弹窗； runner 有单实例锁+时段闸，补跑无害）

## 7a. 回归保护网

- `tests/test_compare_deck_runner.py` 28 项：纯函数（费用/换算/时段/价差/分位/缓冲带/回撤）+ **10-08 月度大考端到端**（mock 时间/行情/引擎，tmp 台账全链）
- 探针提取脚本 `quant_research/scripts/extract_mwu_targets.py`（ETFMWUProbe/MomentumProbe）曾因多轮 patch 丢 `_target_portfolio`——修改探针后必须跑端到端回归

## 7b. 审计与真钱

- 审计脚本：`scripts/compare_deck_audit.py`（只读，8 项核查，✓/⚠/✗ 三态）——检查员 AI 每轮只跑此脚本并解读
- 真钱手册：`docs/real-money-playbook.md`（Y 逆回购券商操作 / X 事件套利人工复核清单 / 晋升流程）

## 7c. 三项数据增强（2026-10-03 落地）

- **E 单券止盈**：持仓券现价 ≥130 元（强赎博弈区）全部卖出——社区共识口径，每日收盘轮检查
- **I 排雷**：PEAD 候选要求预告下限净利润 >0（防基数型假预增）
- **J 崩刀过滤**：21 日跌幅最深候选当日主力净流出 ≥3000 万剔除（真崩≠超跌，moneyflow 接口）

## 8. 预注册与总闸（三闸）

- E2 转债估值择时：全市场转债中位数（cb_daily 单次调用），≥127 减半 / <118 恢复（锚点插值先验），逐日序列积累中
- ERP 股票估值：沪深300 PE 10 年分位，>80% 主动槽减半 / <60% 解除（滞回），实测 60.3% 中性
- 三闸并列独立判定：E2 管 E 槽；ERP 管 A/F/G/H；个股熔断管 F/F2；信用事件流管 E

- `ledger.prereg`：11 槽假设卡（预测/置信/晋级/死亡标准），周五小结展示；改预注册须 tuning 留痕
- ERP 总闸：沪深300 PE 10 年分位，>80% 主动槽减半 / <60% 解除（滞回），实测当前 60.3% 中性
- F/F2 缓冲带：持仓排名 ≤2×slots 保留，空位按市值排名补新

## 9. 已知局限（诚实清单，改进前必读）

- **分红/除息未调整**：D 红利低波（512890）等分红除息日价格跳降会直接体现为净值下跌——净值曲线对高股息标的系统性偏低估。假期后如遇除息，按事件在台账 tuning 记录并手工校正当期 marks。
- **T+1 未建模**：同日"先卖后买"在真实账户里卖出资金当日可用、当日买入次日才能卖——低频（周/月）调仓下影响可忽略；熔断日清仓的均为旧持仓，无冲突。
- **佣金假设**：万 0.845 + 最低 5 元为 quant_research 研究假设，未与真实券商账户费率核对——晋升真钱前必须核对。
- **Y 槽打新未自动跟踪**：中签回填依赖人工；转债打新收益未计提。
- **盘后行情污染**：收盘后六源链个别源返回异常价（实测 tickflow 盘后 512890=1.196 vs 收盘 1.180）——调仓时段闸已隔离，但 risk_round 的异动告警在盘后手动运行时可能基于污染价误报（自动化链路只在交易时段运行，无此问题）。
- **熔断指标**：国证2000（sz399303）为代理指标，非真正的微盘股指数（万得微盘股指数无免费实时源）；-3% 阈值为先验值，未回测校准。


## 10. 真钱资金分配草案（12-31 硬复核的决策框架）

纸面 17 槽等资金是**实验设计**；真钱按证据强度分配，草案：

| 桶 | 比例 | 内容 | 条件 |
|---|---|---|---|
| 核心 40% | C/D 类基准 + Y 现金管理 | 指数/逆回购/打新 | 无需复核（低风险） |
| 卫星 30% | 过三关的主动槽 | 按超额强度加权分配 | 滚动60日跑赢C+三窗口+扣成本为正 |
| 实验 20% | 单槽首笔真金 | ≤¥2,000/槽 | 晋升书面确认 |
| 机会 10% | X 事件套利专用 | 事件触发逐笔 | 全面要约+正价差 |

未过三关的槽真金为 0。分配比例 12-31 复核时按实际表现微调。

# 多策略对比盘（Compare Deck）运维说明

> 2026-09-30 建立。**2026-10-13 与代码对账更新**：E2 水平门→回撤响应、F 缓冲带已移除、T+1 已由执行内核强制、补 K 槽（18 槽全覆盖）、"ERP"改名收敛为"PE 分位总闸"。
> 执行引擎：`scripts/compare_deck_runner.py`（确定性 Python，单测 `tests/test_compare_deck_runner.py`）。
> 本文档是对比盘的运维真源。关联文档：[数据源矩阵](data-source-matrix.md)、[真钱执行手册](real-money-playbook.md)、[决策层变更留痕](decision-layer-changelog.md)。
> 策略证据与调研结论另存 `.scratch/dsa-strategy-validation/*.md`（本地任务目录，不入库）。
> **2026-10-07 状态**：L 槽转观察态（上证发〔2024〕106号，持仓冻结不交易）；I/J/M/O/E/K/H 数据契约已按审查反例修正（`tests/test_deck_slot_contracts.py` 固化）。
> **2026-10-10 状态**：任务08收敛批次已落地——审计三态结论+非零退出码、月度调仓注册表（子任务幂等/失败重试/非撮合窗顺延）、PE 分位总闸决策/执行分离且作用域对齐 deck_registry、撮合窗 15:00 截止、独占锁属主探活、E2 恢复整手买回、收盘通知顺序修正、测试全隔离（CI 干净检出可复现）。计划任务 `DSA_Deck_Intraday` 仍为 Disabled，待用户确认后再启用。
> **2026-10-19 状态**：新增 **§2.1 成交口径边界**（口径真源）——澄清"20 分钟轮不驱动策略调仓"、各槽按声明节奏调仓、成交价/成交时点/信号 as-of 的完整关系，并如实记录三处口径边界与一处待收敛项（周频槽未成交时不重试）。

## 1. 槽位与资金

每槽起始本金 ¥12,411（Y 槽逆回购名义 ¥12,000+现金 ¥411）（前向纸面跟踪：真实价格/整手/佣金，非券商下单）。共 **18 槽**（A/B/C/D/E/F/F2/G/H/I/J/K/L/M/N/O/X/Y；H_MWU_CB 融合槽已激活，CB price feed 由 PIT 生成）：

| 槽位 | 策略 | 调仓 | 备注 |
|---|---|---|---|
| A_动量 | 17池ETF 60日动量+MA250 top3 | 周一收盘轮 | 货币ETF腿以现金持有 |
| B_轮动 | 3资产25日动量+绝对过滤 | 周一收盘轮 | 全负→空仓 |
| C_沪深300持有 / D_红利低波持有 | 被动基准 | 无 | |
| E_可转债双低 | quant 引擎导出目标 | 月度调仓窗（本月首个交易日，trade_cal 判定）；**选债本体季频（rebalance=Q），月度窗只是重导出检查点** | 目标资金按当前 equity（非初始本金）；子进程导出失败显式报错不沿用旧 plan；信用列每轮更新（<100元减半仓） |
| G_MWU融合 | `quant_research/scripts/extract_mwu_targets.py`（backtrader 探针提取，引擎口径） | 周一收盘轮比对目标变化 | MWU 融合动量/均值回归/防御三专家；转债现价走腾讯直连 |
| I_PEAD业绩预告 | tushare forecast 最近40天预增（`p_change_min≥50%` 且净利下限>0）等权5只 | 月度调仓窗 | 口径澄清：同比预增≠相对预期超预期（PEAD/SUE）；预告季(1/4/7/10)密集 |
| J_短期反转 | 21个交易日跌幅最深5只（成交额≥3000万，amount 千元换算） | 月度调仓窗 | 与A动量负相关；接飞刀风险已知；TushareClient 统一取数 |
| K_项目选股 | 项目观察池（`build_buy_watchlist(snapshot=…)`） | 月度调仓窗 | 前向测试（观点非信号）；快照经**参数注入**（不再 monkeypatch 服务模块）；market_scan 未运行时据实报"候选池快照不可用" |
| L_北向择时 | 北向净买入250日分位（moneyflow_hsgt） | **观察态（不交易）** | 上证发〔2024〕106号后单只证券持股改季度披露，供应商字段语义未证——信号照记、持仓冻结；证据充分前不恢复交易 |
| M_筹码集中度 | 股东户数降幅≥10%（stk_holdernumber 季度，按 ann_date 取最新版本） | 月度调仓窗 | 口径降级：户数下降≠主力建仓，低获利比≠安全区（cyq 仅作高位剔除） |
| N_技术形态对照 | Sequoia-X 七形态扫描（命中≥2优先） | 月度调仓窗 | **本机已激活**（回填 231 万行，闸门 200 万+新鲜度）；驱动与数据不入库，干净克隆下条件式跳过（见 §13 已知局限） |
| O_行业轮动 | 申万L1（SW2021）行业20交易日动量→前3行业→市值龙头2只/行业 | 月度调仓窗 | 升序算动量+全量成员后取龙头；国金研报同源 |
| H_MWU_CB融合 | etf_mwu_cb 四专家（动量/均值回归/防御/转债 PIT 双低） | 月度调仓窗 | CB price feed 由 PIT 生成（`pit_prices_to_feed`），四专家真实参与；池 < min_candidates=15 时转债专家让位现金；`unpriced_terms` 输出缺价审计 |
| F_小市值20 / F2_神帖对照 | tushare 流通市值升序 | 月度窗 | F 剔次新、单票≤¥2500；F2 主板不剔次新（对照）；均熔断跟随。**¥12,411 下 20×2500 / 5×4000 结构性不可行**：2026-10-22 首建仓前改为 F **最多 8 只**、F2 **最多 3 只**，每票预算 `min(单票上限, 槽内 equity/槽数)` 后按 100 股整手向下取整；高价股可能因一手超预算被跳过，实际只数按可买尽买（槽名保留用于 cohort 对照） |
| Y_确定性收益 | 国债逆回购(节前/月末 GC001) | 节前最后交易日 14:30 后 | 打新走 dsa 每日待办提醒；逆回购利息=名义×年化×计息天/365 |
| X_事件套利 | 要约收购/现金选择权/吸收合并 | 事件驱动 | 价差规则：现价<对价×0.98(ST 0.95)，单笔≤¥1,241 |

## 2. 调度与时间

- Windows 计划任务 `DSA_Deck_Intraday`：每 20 分钟 → `compare_deck_task.ps1 -Mode intraday`
- Windows 计划任务 `DSA_Deck_Close`：**工作日 15:00:30**，`WakeToRun=True` + `StartWhenAvailable=True` → 同一 `-Mode intraday`；用于机器休眠时唤醒并在收盘集合竞价后触发收盘轮。与 `DSA_Deck_Intraday` 通过 runner 独占锁 + `close_done` 幂等：谁先拿到锁谁执行，另一轮打印"另一实例运行中"或"收盘轮当日已执行"后退出。
- 收盘轮本身不挂绝对时刻：盘中轮在**交易所时间 ≥15:00** 且当日未跑过（`_control.close_done` 幂等键）时自动触发——本机时钟不可信也无影响；`DSA_Deck_Close` 只是唤醒/提前触发，不改变 15:00 撮合闸。
- **运行窗 vs 撮合窗**：运行窗（何时进入 intraday/close 轮）= 9:30-11:30 / 13:00-15:10，15:10 后为 catch-up 补结算窗；撮合窗（何时允许新成交，`_fills_open`）= 9:30-11:30 / 13:00-**15:00**（15:00 整点含收盘集合竞价价，合法）。15:00-15:10 只结算不成交，风控动作抑制为只记录
- 交易日历：周末前置判定（确定性、零 API 调用）；工作日接口失败保守视为交易日（marks 幂等兜底）
- 时段/日期判断：**交易所行情时间戳**（腾讯字段[30]）+ tushare `trade_cal` 交易日历（进程内缓存）；本机时钟仅兜底
- 手动执行：`py scripts/compare_deck_runner.py --mode intraday|close [--dry-run] [--force]`

### 2.1 成交口径边界（口径真源）

**盘中 20 分钟轮不驱动策略调仓。** `run_intraday` 只做：事件套利扫描（X）、观察池刷新（K，每半天节流）、三道全局风控闸（个股熔断 / PE 分位总闸 / E2），以及在交易所时间 ≥15:00 时触发收盘轮 `run_close`。**各槽按自身声明节奏调仓**（`deck_registry.SLOTS.rebalance`）：周频 A/B/G 在**周首交易日**收盘轮、月频 F/F2/E/H/I/J/K/M/N/O 在**月首交易日**收盘轮、C/D 不调仓、X 事件驱动、Y 日历；L 为观察态不交易。→ 不存在"18 槽共享一个交易时钟"。

**成交价与成交时点的关系（唯一真源）**：

- 成交价 = `realtime_quotes()` 实时价（腾讯），无价/零价直接 `NO_QUOTE` 拒绝，不按意图价成交。
- 成交时点 = `_fills_open`（运行时段内且 ≤15:00）。**15:00 整点含收盘集合竞价价**，15:01-15:10 只结算不成交。
- **定仓基数**：A/B/G/H/E 使用调仓时的**当前 equity**（与引擎回测 `broker.getvalue()` 同口径）；F/F2 用 `min(单票上限, 槽内 equity/槽数)` 作为每票预算并按整手向下取整；C/D 用槽内现金 98% 买入。
- 因此**收盘轮的成交价 ≈ 当日收盘价**；台账 `track_rule` 自述为"每交易日收盘实时价重估各策略 equity；周频策略调仓日再平衡"。

**各槽信号的数据截止（as-of）**：

| 槽族 | 信号来源 | 数据截止 |
|---|---|---|
| A | 引擎探针（`extract_mwu_targets.py` → tushare loader） | 探针 `as_of` |
| B | `_load_snapshot()` 最新 `feeds_snapshot_v*.pkl` | 快照末端数据日（文件 mtime 超 7 天**或末端早于上一交易日**时自动重建；重建后仍滞后则日志/消息标注实际末端） |
| G / H | `extract_mwu_targets.py`（tushare loader / 转债 PIT） | 探针 `as_of` |
| E | quant 引擎导出目标（季频选债，月度窗重导出） | 引擎 `as_of`；目标资金按当前 equity |
| F / F2 | tushare `daily_basic`，`trade_date=上月末` | **上月末**（早于调仓日，无当日回看） |
| I / J / M / N / O | tushare 各域接口 | 各槽自述窗口（详见 §1 槽位表与数据源矩阵） |
| K | 项目观察池快照（参数注入） | 快照 `as_of` |
| L | `moneyflow_hsgt` | 观察态，不交易 |
| X / Y | 合约锁价 / 日历收益 | 不适用 |

**已声明的口径边界（如实记录，非缺陷）**：

1. **A/B 数据源不同**：A/G/H 走 tushare loader，B 走 `feeds_snapshot_v*.pkl`；B 的信号日可能比 A 旧（例如 2026-10-16 快照用于 10-21 调仓）。该差异不影响各自策略内部逻辑，但解读 A/B 排名时需知悉。

2. **引擎派生槽（A/B/G）与各自引擎回测存在成交时点差**：引擎回测口径为"调仓日收盘算信号 → **次日开盘**成交"，本盘为"调仓日收盘算信号 → **当日收盘**成交"。**已实测其量级**（`.scratch/dsa-deck-convention-study/`，样本 2024-10-31~2026-08-03 共 92 周）：按**对称腿**（只计实际换手）测得买入腿 −0.1136%（n=68）、卖出腿 +0.0615%（n=66），**净效应 −0.1751%（SE 0.1453%，t=−1.20）——方向为本盘略吃亏（偏保守），但统计上与零不可区分**（95% CI [−0.46%, +0.11%]）。经济解释：A股**隔夜反转**使动量换仓在隔夜端不利，引擎的"次日开盘成交"恰好躲开这段反转。故该差**不影响槽间排名、也不构成"虚高"风险**（实测偏保守，对晋升门槛是安全侧），维持现口径不改内核。**功效局限**：n=68 时最小可检出效应约 0.41%/次换仓，"真值=0"与"真值=−0.18%"均在样本可解释范围内；要定案需按权重加权 + 实际换手名义的更高功效设计。
   > 更正记录：本条初版（同日早些时候）曾写"净效应 +0.0038%（t=0.03）、买卖两侧近乎完全对冲"——那是**测量缺陷**造成的假象：买入腿误将"上期已持有、本期并未交易"的保留标的计入（272 条 vs 实际换手 68 条），用未交易标的的噪声把真实量级稀释到近零。已按对称口径更正。
3. **转债尾盘流动性**：尾盘 14:55 后转债挂单易失败，本盘以收盘价建模；实盘对应操作应提前至 14:50 前（台账 `web_research_tweaks.E_尾盘流动性`，逐 cohort 保留）。
4. **月频组共享"月首交易日"**：同日调仓会使当日同涨同跌摊掉组内相对差异——解读月度排名时需知悉该日不是独立样本。

**非撮合时段的处置（同一语义，槽间已对齐）**：调仓在撮合窗外**不成交、只顺延**，并登记待办、在同窗后续轮次续做至齐备：
- **周度组 A/B/G**：非撮合窗顺延 → `_control.week_rebalance_done` 占位 → **同周后续交易日**续做（周频窗口仅"周首交易日"，错过不再整周丢失）。
- **月度组 F/F2/E/H/I/J/K/M/N/O**：非撮合窗顺延 → `_control.rebalance_done` 占位 → 月内每日 close 续做。
- **PE 分位总闸**：被拒标注"待次日补做"，次日 close 自然重试。
- **消息据实**：凡未实际成交（非撮合窗未下单、或下单被内核拒绝）**不得声称"调仓→"**，改报 `目标→… (非撮合时段, 未成交, 待续做)` 或 `调仓→… (部分未成交: N 笔被拒)`。
- 三道风控（个股熔断 / E2 / E 信用）在非交易时段仍按既有语义**抑制为只记录**（不迁移状态、不发动作消息）。

### 2.2 首建仓 P0 修复（2026-10-22）

2026-10-21 首次收盘轮只成交 C/D（基准建仓），A/B/G/F/F2/E/H 全现金、周/月注册表均为 `[]`。根因不是行情缺失，而是**成交闸在慢预取后二次取时**：收盘轮启动时本可成交，C/D 预取耗时数十秒后 `exchange_now()` 失败/回退墙钟 15:02，周/月注册表与 A/B/G/H 误判"非撮合时段"而静默顺延。修复：

1. `_main_inner` 在进程入口捕获交易所会话时间并暴露为 `_session_now`；`run_close` 启动即计算 `fills_ok`，周/月/建仓注册表与 A/B/G/H 全部复用该值，**不再二次取时**。
2. `marks` 从收盘轮开头移到注册表/成交/ERP 缩放之后：首日净值即反映成交、费用与持仓市值，而不是先记全现金。
3. F/F2 改为最多 8/3 只与每票预算，避免 20×2500/5×4000 在 ¥12,411 下永远买不满（见 §1 槽位表；一手超预算的标的会跳过）。
4. B 快照重建除 mtime 外校验**末端数据日**（`< 上一交易日` 即重建）；10-19 重建出的快照末端仅 10-16，10-22 收盘会再次重建。
5. 审计新增**注册表完成度**（当日 `rebalance_done`/`week_rebalance_done` 非 `all` 即 WARN）、**主动槽覆盖**（主动槽全现金即 WARN）、**任务 LastTaskResult**（非 0 WARN），并纳入三态核心证据——禁止再以"只成交 C/D"宣称正常。
6. `DSA_Deck_Intraday` 设 `StartWhenAvailable=True`（2026-10-22）：休眠/错过触发后在机器恢复时补跑，仍受独占锁、运行窗、15:00 撮合闸与 `close_done` 幂等约束，不产生盘后成交；`WakeToRun` 仍为 False（避免整夜每 20 分钟唤醒）。

## 3. 数据源

- 实时行情：`data_provider.DataFetcherManager.get_realtime_quote`（六源降级+熔断；tushare 为 P-1 最高优先级，15000 积分账户）；prefetch 按 5 只分批（tickflow 上限）
- 转债现价：腾讯直连 `tencent_quotes()`（六源链对带后缀转债代码覆盖不到）
- B 历史信号：只读 `quant_research/output/feeds_snapshot_v*.pkl`（跨项目文件契约）；文件超 7 天**或末端数据日早于上一交易日**时自动调 `build_feeds_snapshot.py` 重建 v5，失败沿用旧快照并标注；重建成功但数据源仍滞后时消息标注实际末端（如 `至 2026-10-16`）。A/G/H 由引擎探针经 tushare loader 取数，不走该快照。
- 小市值选股：tushare `daily_basic` + `stock_basic`（月度窗，trade_date=上月末）

## 4. 消息（飞书，route_type=strategy_compare）

- 收盘轮每日一条：排名 + 今日操作 + **每槽持仓明细（数量/成本→现价/盈亏%）** + 风险状态
- 调仓操作单、熔断、信用告警、事件建仓/否决/观察跟踪：发生即推
- 无事不发；dedup_key 防重（`deck_close_summary_YYYY-MM-DD` 等）
- 每日 18:00 聚合摘要已关闭（`NOTIFICATION_DAILY_DIGEST_ENABLED=false`）

## 5. 状态与审计

- 台账：`.scratch/dsa-strategy-validation/compare_strategies.json`（原子写 + 写前 `.json.bak` 滚动备份）；每日 08:30 `DSA_Ledger_Backup` 已纳入台账与 `archive/cohort-manifest.jsonl`，`--verify` 校验三块容器；离机同步见 `docs/disaster-recovery.md` §2.1
- 控制键 `_control`：`close_done`（收盘幂等）/ `event_scan_last`（事件扫描每半天一次；**仅扫描未返回【异常】时写键**，失败下一轮重试；键按交易所时间而非墙钟）/ `rebalance_done`（月度登记：全部成功 `"all"`，部分成功为已成功标签列表，每日撮合窗续做至齐备；非撮合时段顺延不执行）/ `week_rebalance_done`（周度同构，同周后续交易日续做）/ `bootstrap_done`（C/D 一次性建仓，`"all"` 或已成功标签列表）/ `erp_scaled`（PE 分位总闸已缩放槽登记；槽位周度/月度重建成功后清除，同轮 `erp_scale_pass` 重新应用）/ `e2_state`、`erp`、`crash_state` 等风控状态 / `_actions`（当日动作，盘中+收盘合并）
- 执行日志：`logs/compare_deck_task.log`（start/exit + runner 输出）
- 病历制：槽位停跑/清仓 → `.scratch/dsa-strategy-validation/{槽位}_postmortem.md`

## 6. 回滚

- 停执行：`schtasks /Delete /TN "DSA_Deck_Intraday" /F`
- 删执行器：`scripts/compare_deck_runner.py`、`scripts/compare_deck_task.ps1`、`tests/test_compare_deck_runner.py`
- 恢复摘要：`.env` `NOTIFICATION_DAILY_DIGEST_ENABLED=true`
- 台账不受影响（历史净值保留）

### 6.1 cohort 切换（旧账封存 → 新 cohort 开账，spec Phase1-3）

工具：`scripts/compare_deck_cohort.py`（**默认 dry-run，零写入**；`--apply` 才落盘）。

```bash
py scripts/compare_deck_cohort.py --start YYYY-MM-DD            # 预览计划
py scripts/compare_deck_cohort.py --start YYYY-MM-DD --apply    # 归档 + 开账
```

- **封存不删**：归档 = 复制到 `.scratch/dsa-strategy-validation/archive/compare_strategies.cohort1.<day>.json`
  + 追加 `cohort-manifest.jsonl`（含 sha256，与 `ledger-seal-manifest.json` 互为印证）；原文件字节保留。
- **新 cohort**：18 槽（`deck_registry.SLOTS` 唯一真源）每槽 ¥12,411、冻结配置、明确交易日起步；
  清空 holdings/cash/equity/marks/`_control`/`_actions`，**保留** prereg 与各 study 证据键；
  不倒填历史、不用新规则重算旧结果。
- **禁止重启旧 writer 写归档账**：`--apply` 后 `compare_strategies.json` 即为新 cohort，误启用也不会污染旧账。
- **开账即登记当月为待做**（`_control.rebalance_done = {开账月份: []}`）：月频注册表的门是"本月首个交易日"，**月中开账**时该日已过、本月内门永不再成立 → 十个月频槽整月不建仓；列表值即"已部分完成"，开续做门后月频槽在**开账后首个收盘轮**初始化（开账日恰为月首时与自然路径等价）。
- **基准槽一次性建仓**：买入持有槽 `C_沪深300持有`/`D_红利低波持有` **无周/月/事件/日历注册表覆盖**，清空后必须由 runner 的 `_bootstrap_tasks` 补建（`C→510300.SH`、`D→512890.SH`，按槽内现金买满整手；`_control.bootstrap_done` 门 + 已有持仓即跳过，非撮合窗顺延后续做）。否则 C 恒为现金 → "跑输 C 即降级"的评价纪律失效。
- **回滚**：归档副本可直接覆盖回 `compare_strategies.json`，并删除本轮 manifest 条目；无其它状态需还原。
- **容器类型契约（勿改）**：`_actions` 是**按日期键的 dict**（runner `build_close_summary` 用 `led["_actions"].get(today)`）；重置只清键、不得换成 list；`marks` 是 list、`_control`/`week0`/`strategies` 是 dict。
- 前置：旧账已被任务 00 封存（`ledger-seal-manifest.json`，哈希 `09e16aa8…`）；开账交易日起点**须用户批准**。

## 7. 边界

绝不真实下单；主动策略晋升真钱按三关（滚动60日跑赢沪深300 + 三窗口切片为正 + 扣成本为正）；确定性通道（事件套利/打新/逆回购）真钱优先但单笔≤总资金10%。

## 8. 调度细节（2026-10-02/03 诊断后）

- **事件发现双通道（职责不同非重复）**：server 常驻进程的 EventArbitrageMonitor（每 60 分钟，推送告警）+ runner 的 event_round（每半天，价差核验→X 建仓）。同一公告可能两通道各推一次（dedup 键不同），属预期行为
- **N 槽已激活**：Sequoia-X 回填完成（231 万行 = 其配置全量 2024-01 起），闸门修正为 200 万行+最新 bar 10 天内

- **弹窗修复**：所有 `DSA_*` 计划任务动作经 `scripts/run_hidden.vbs`（SW_HIDE 在 CreateProcess 阶段生效，绕过 Windows Terminal 默认终端接管）；收盘任务用 `scripts/run_hidden_sync.vbs`（同样隐藏，但**等待子进程**并回传退出码，避免任务调度器在 launcher 退出后清理子进程）——审计确认执行链完好
- **电池策略已解除**：`DisallowStartIfOnBatteries/StopIfGoingOnBatteries = False`——交易时段盘中风控（分级熔断/E2/信用）不因用电池停摆；代价是电池续航，回滚=改回 True
- **补跑突刺**：插电瞬间错过的高频任务会立即补跑（run_hidden 已消除弹窗； runner 有单实例锁+时段闸，补跑无害）

### 8.1 唤醒收盘任务 `DSA_Deck_Close`（2026-10-21）

**目的**：`DSA_Deck_Intraday` 每 20 分钟但 `WakeToRun=False`；机器休眠时会整体错过 15:00 撮合窗（10-20 实测 14:42-21:02 空档，首次建仓顺延）。新增独立任务，只在工作日 15:00:30 唤醒一次，避免给 20 分钟任务开 `WakeToRun` 导致整夜每 20 分钟唤醒。

**形态**：动作使用 `scripts/run_hidden_sync.vbs`（`wscript.exe //B //Nologo run_hidden_sync.vbs "powershell ... compare_deck_task.ps1 -Mode intraday"`）：同样隐藏窗口，但 **`sh.Run(cmd,0,True)` 等待子进程并回传退出码**——任务调度器全程跟踪，`LastTaskResult` 有意义，避免 `run_hidden.vbs` 非等待导致 launcher 退出后子进程被清理（实测曾丢 exit 行）。`-Mode intraday` 在交易所时间 ≥15:00 时触发 `run_close` 并设置 `close_done`。设置：`WakeToRun=True`、`StartWhenAvailable=True`、`DisallowStartIfOnBatteries=False`、`StopIfGoingOnBatteries=False`、`MultipleInstances=IgnoreNew`、`ExecutionTimeLimit=PT2H`，principal 与既有任务一致（Interactive/Limited）。

**注册**（PowerShell 原生 cmdlets；实际注册 2026-10-21）：

```powershell
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '//B //Nologo "D:\daily_stock_analysis\daily_stock_analysis\scripts\run_hidden_sync.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\daily_stock_analysis\daily_stock_analysis\scripts\compare_deck_task.ps1 -Mode intraday"'
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]'15:00:30')
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'DSA_Deck_Close' -Action $action -Trigger $trigger -Settings $settings -Principal $principal
```

**电源唤醒定时器**（必要；否则 `WakeToRun` 可能被电源策略拦截）：本机原值 AC=2（仅重要唤醒定时器）/DC=0（禁用），2026-10-21 改为 AC=1/DC=1：

```powershell
powercfg -setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg -setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg -setactive SCHEME_CURRENT
```

回滚：AC=2、DC=0。

**运行期保持唤醒**：任务被 sync launcher 跟踪并不等于系统不会休眠；`compare_deck_task.ps1` 于 runner 运行期间持有 `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)`，`finally` 清除；不保持显示器常亮。该包装同时被 20 分钟任务复用（常规任务仍用非等待的 `run_hidden.vbs`）。

**验证（2026-10-21）**：01:12 注册后首次冒烟；01:33 切换 sync launcher 后复测——隐藏链正常、日志 `intraday start` /「非交易时段(交易所时间 10-20 16:14)，跳过 intraday 轮」/ `exit=0`（中文为可读 UTF-8，exit 行使用结束时间戳）、会话闸拒绝、台账 sha256 前后一致；任务 `LastTaskResult=0`、`NextRunTime=2026-10-21 15:00`（触发器 StartBoundary 为 15:00:30）。`powercfg -waketimers` 需管理员权限，未在非提权会话验证；任务设置项与电源值已核对。

**边界**：只能从**睡眠**唤醒；整机**关机**仍无法唤醒；15:00:30 触发时若交易所戳尚未到 15:00，`run_intraday` 不触发 close，15:02 常规轮会在机器已醒的情况下补触发；若两者都没拿到撮合窗，注册表按既有语义顺延到下一交易日收盘，不补当日成交。

**移除/回滚**：`Unregister-ScheduledTask -TaskName 'DSA_Deck_Close' -Confirm:$false`；电源值恢复 AC=2/DC=0；包装脚本 revert 对应提交。

## 9. 回归保护网

- `tests/test_compare_deck_runner.py` 94 项：纯函数（费用/换算/时段/价差/分位/缓冲带遗留函数/回撤）+ **10-08 月度大考端到端**（mock 时间/行情/引擎，tmp 台账全链）+ 月度/周度注册表（子任务失败/顺延次日重试）+ 空槽首建仓行情预取与按当前 equity 定仓 + 启动会话捕获（入口 → run_close，慢行情不二次取时）+ marks 后置 + F/F2 每票预算 + 快照末端重建 + 子进程 UTF-8 解码 + 盘中扫描失败重试/交易所 halfday + PE 分位总闸缩放 + 独占锁四分支 + 撮合窗边界；全部离线可复现（tmp 台账，不依赖生产台账与外部库）
- `tests/test_paper_execution.py` 21 项（任务 08 执行内核：11 拒绝码/整手/涨跌停/T+1/现金守恒）+ `tests/test_compare_deck_cohort.py` 8 项（开账/种子/容器契约）+ `tests/test_compare_deck_audit.py` 17 项（含时间健康跨日戳/收盘后不报时钟偏差/多源同日比较/执行日志 start-exit 配对/唤醒任务/注册表与主动槽覆盖/任务结果 PASS 与非零 WARN）+ `tests/test_deck_registry.py` 6 项 + `tests/test_deck_slot_contracts.py` 17 项 + `tests/test_compare_deck_shadow.py` 10 项：审计三态结论/退出码传播 + 注册表 18 槽/PE 分位总闸作用域单一真源一致性 + 槽位数据契约 + 影子只读校验
- 探针提取脚本 `quant_research/scripts/extract_mwu_targets.py`（ETFMWUProbe/MomentumProbe）曾因多轮 patch 丢 `_target_portfolio`——修改探针后必须跑端到端回归

## 10. 审计与真钱

- 审计脚本：`scripts/compare_deck_audit.py`（只读，30+ 项核查，✓/⚠/✗ 三态；含注册表完成度/主动槽覆盖/任务结果）——检查员 AI 每轮只跑此脚本并解读
- 检查员解读规则（三态结论 + 退出码）：
  - 任一 ✗（FAIL）→ 结论 `【异常】N 项 FAIL`，退出码 1，须人工介入
  - 无 ✗ 但核心证据（marks 新鲜度 / 执行日志 / 抽样净值 / 注册表 / 主动槽覆盖 / 任务结果 / 唤醒任务）任一非 ✓ → 结论 `样本不足, 无法判定`，退出码 0
  - 核心证据全 ✓ → 结论 `正常`，退出码 0
  - 台账不可读 → 退出码 2
- 关键 FAIL 判据：marks 断供 >15 天 FAIL / >10 天 WARN；最近 intraday 执行日志 exit≠0 → FAIL；抽样净值偏差超阈 → FAIL
- 时间健康 / 多源一致性（2026-10-21 修）：时间戳非当日只标注「市场已收盘」，不再把跨日旧戳误报为 7.8h 时钟偏差；多源一致性按**同一行情日**比较 baostock 与腾讯（盘中跳过），不再拿硬编码 2026-09-30 与实时价比较
- 执行日志 start/exit 配对（2026-10-21 修）：最后一轮只有 start（任务被终止或仍在运行）显式 WARN，不再取最后一条事件或拿更早的 exit 误判；start-only 日志不再触发 IndexError
- 唤醒任务检查（2026-10-21 增）：`DSA_Deck_Close` 的 enabled / `WakeToRun` / sync launcher / 15:00:30 触发器；任一不满足 WARN
- 真钱手册：`docs/real-money-playbook.md`（Y 逆回购券商操作 / X 事件套利人工复核清单 / 晋升流程）

## 11. 三项数据增强（2026-10-03 落地）

- **E 单券止盈**：持仓券现价 ≥130 元（强赎博弈区）全部卖出——社区共识口径，每日收盘轮检查
- **I 排雷**：PEAD 候选要求预告下限净利润 >0（防基数型假预增）
- **J 崩刀过滤**：21 日跌幅最深候选当日主力净流出 ≥3000 万剔除（真崩≠超跌，moneyflow 接口）

## 12. 预注册与总闸（三闸）

- E2 转债估值择时（**2026-10-06 由水平门改为回撤响应**）：全市场转债中位数（cb_daily 单次调用）逐日序列 → 自峰值回撤 ≥5% 进防御（E 槽减半）、回撤收窄至 ≥-2% 恢复满仓（按 `_control.e2_pre_defensive_qty` 登记数量买回，缺登记按现仓×2）；缺数据保持仓位并报 `no_data`（不把 median=0 当作 -100% 回撤）；非交易时段只记录不迁移
- PE 分位总闸：沪深300 PE 10 年分位，>80% 主动槽减半 / <60% 解除（滞回），实测 60.3% 中性。**决策/执行分离**：`erp_gate` 纯决策（只写 `_control.erp_state`），`erp_scale_pass` 执行据实（逐槽减半按 apply_orders 返回判定，被拒标注"待次日补做"，成功写 `erp_scaled[slot]`）；周度/月度重建成功后清该槽标记 → 同轮重缩放
- PE 分位总闸作用域（单一真源 `deck_registry.risk_scope`）：覆盖 `etf_cycle`+`stock_cycle` 共 11 槽（A/B/F/F2/G/I/J/K/M/N/O）；**E/H（cb）不受该闸**（与 E2 只作用 E 槽对称）；L 观察态排除；C/D 基准、X 事件、Y 现金不适用
- 三闸并列独立判定：E2 管 E 槽；PE 分位总闸管 11 槽（registry etf_cycle+stock_cycle 口径）；个股熔断管 F/F2；信用事件流管 E

- `ledger.prereg`：11 槽假设卡（预测/置信/晋级/死亡标准），周五小结展示；改预注册须 tuning 留痕
- F/F2 缓冲带：**已移除**（2026-10-06，23 月回测证实月均拖累 0.72pp；F 改为纯小市值排序）——`select_with_buffer` 随之成为**未接线**的纯函数（仍在单测中，勿误以为生效）

## 13. 已知局限（诚实清单，改进前必读）

- **分红/除息未调整**：D 红利低波（512890）等分红除息日价格跳降会直接体现为净值下跌——净值曲线对高股息标的系统性偏低估。假期后如遇除息，按事件在台账 tuning 记录并手工校正当期 marks。
- **T+1 已由执行内核强制**：`src/core/paper_execution.py` 按资产类别施加策略（股票/ETF `t_plus_1=True`、转债/逆回购豁免），超额卖出返回 `T_PLUS_1` 拒绝码（任务 08 内核）；残余差异仅"卖出资金当日可用"这一账户现金流语义，低频（周/月）调仓下影响可忽略。
- **佣金假设**：万 0.845 + 最低 5 元为 quant_research 研究假设，未与真实券商账户费率核对——晋升真钱前必须核对。
- **Y 槽打新未自动跟踪**：中签回填依赖人工；转债打新收益未计提。
- **盘后行情污染**：收盘后六源链个别源返回异常价（实测 tickflow 盘后 512890=1.196 vs 收盘 1.180）——调仓时段闸已隔离，但 risk_round 的异动告警在盘后手动运行时可能基于污染价误报（自动化链路只在交易时段运行，无此问题）。
- **熔断指标**：国证2000（sz399303）为代理指标，非真正的微盘股指数（万得微盘股指数无免费实时源）；-3% 阈值为先验值，未回测校准。
- **N 槽不可在干净克隆复现**：驱动 `quant_research/vendor/Sequoia-X/n_slot_driver.py`（vendored 第三方仓库，~299MB）与其回填行情数据均**不入库**（`.gitignore` 已忽略 `quant_research/vendor/`）。上表 N 行的"已激活"仅对本机工作区成立；干净克隆下 `sequoia_ready` 闸为假、N 槽**条件式跳过**（不报错、不参与调仓）。复现需自行 vendored Sequoia-X 并回填行情数据。
- **运行在线依赖**：收盘轮/撮合窗要求交易日本机在 15:00-16:13 保持运行；休眠时由 `DSA_Deck_Close`（WakeToRun）唤醒，整机关机仍无法唤醒；错过即不补当日成交（注册表顺延至下一交易日收盘），不得用历史价回填。
- **A/B 数据 as-of 差异**：B 走 `feeds_snapshot_v*.pkl`（末端可能滞后，按文件 mtime 刷新、不校验末端日期）；A/G/H 走 tushare loader。解读 A/B 排名时需知悉该差异。


## 14. 真钱资金分配草案（12-31 硬复核的决策框架）

纸面 17 槽等资金是**实验设计**；真钱按证据强度分配，草案：

| 桶 | 比例 | 内容 | 条件 |
|---|---|---|---|
| 核心 40% | C/D 类基准 + Y 现金管理 | 指数/逆回购/打新 | 无需复核（低风险） |
| 卫星 30% | 过三关的主动槽 | 按超额强度加权分配 | 滚动60日跑赢C+三窗口+扣成本为正 |
| 实验 20% | 单槽首笔真金 | ≤¥2,000/槽 | 晋升书面确认 |
| 机会 10% | X 事件套利专用 | 事件触发逐笔 | 全面要约+正价差 |

未过三关的槽真金为 0。分配比例 12-31 复核时按实际表现微调。

## 15. 影子对照（启用前置，spec Phase3-6）

装置：`scripts/compare_deck_shadow.py`（**只读**；不写台账、不发通知、不动计划任务）。

```bash
py scripts/compare_deck_shadow.py            # 跑一轮并归档
py scripts/compare_deck_shadow.py --status   # 只看进度与差异
```

- 以 `compare_deck_runner.py --mode close --dry-run` 运行新内核；dry 模式按设计
  「只计算与打印，不写台账、不发通知、不执行调仓」。
- 每次归档前**校验台账 sha256 必须一致**，不一致即判 `FAIL`（dry 模式不应写盘）；
  runner 退出码 ≠0 同样判 `FAIL`。
- 逐日追加 `shadow/shadow_log.jsonl`：日期、退出码、各槽 `(dry)` 意图行、stdout 尾部、
  台账哈希、与上一交易日的意图增删差异。
- **门槛**：连续 **3 个交易日**（`REQUIRED_DAYS`）归档且无 `FAIL` → 才进入"用户确认启用"。
- **同日重跑**：证据全部保留（JSONL 追加），但计数与 `FAIL` 判定取当天**最后一次**运行——首次尝试若因工具缺陷无效，重跑即取代。
- **启用自动化**（2026-10-14 用户常备指令"不要什么事都让我来做"）：包装在**满 3/3 且 0 FAIL** 时
  启用 `DSA_Deck_Intraday` 并随即禁用自身；**任何 FAIL 出现则不启用**。
  回滚：`Disable-ScheduledTask -TaskName DSA_Deck_Intraday`。
- 前置：需在**真实仓库工作树**（含 `quant_research/output/`、生产台账、`.scratch/`）上运行；
  worktree/干净克隆缺这些未跟踪数据，E 槽导出会显式失败（属环境差异，非逻辑缺陷）。
- **可选自动化**（`scripts/compare_deck_shadow_task.ps1`，计划任务 `DSA_Shadow_Compare`，**只读**）：
  **每个交易日（周一至周五）15:40** 跑一轮并写 `logs/compare_deck_shadow_task.log`；**满 3 个交易日且 0 FAIL 时：
  先启用 `DSA_Deck_Intraday`，再禁用自身**（不会长期驻留）。
  频率说明：10-15（周四）触发时刻机器未运行致当日缺失，且补跑时机不可控——
  改为每个交易日一槽后，任一工作日开机即可推进门槛（错过的启动经
  StartWhenAvailable 在当日补跑，仍记为同一日历日）。
  安装：`schtasks /Create /TN "DSA_Shadow_Compare" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\daily_stock_analysis\scripts\compare_deck_shadow_task.ps1" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:40 /F`
  移除：`schtasks /Delete /TN "DSA_Shadow_Compare" /F`

## 16. 账本契约与存储决策（唯一真源）

**正式账本 = 单个 JSON 文件**：`.scratch/dsa-strategy-validation/compare_strategies.json`（schema v3）。

- 唯一写入方：`scripts/compare_deck_runner.py`（整文件重写 + 临时文件原子替换；另有 `.bak` 回退，任务 00）。
- 只读消费方：`compare_deck_audit.py`（`json.loads` 后跑 8 项检查）、`compare_deck_cohort.py`（归档 + 开账）、
  `compare_deck_shadow.py`（dry 运行 + **前后 sha256 一致**判定只读性）。
- **为什么是 JSON**：封存清单（`ledger-seal-manifest.json`）、归档 sha256、影子前后哈希校验都以
  "同一状态 → 同一字节"为前提——等价于内容寻址的审计链；JSON 可读、可 diff、可哈希。

**为什么不采纳 SQLite 作为正式账本（2026-10-14 决定，含 GitHub 生态调研）**

1. **哈希链与可审计**：SQLite 页级写入每次改字节，WAL 边车文件会破坏"前后哈希一致"与 diff 审计；
   而影子门槛正是用 sha256 前后一致来证明 dry 只读。
2. **规模与并发不成立**：18 槽、单进程、日频/月频写入，无需事务/并发；上游那套 SQLite
   （`src/storage.py` + `src/repositories/portfolio_repo.py`，`SQLITE_WAL_ENABLED`/`BUSY_TIMEOUT`）
   是为**多进程共库**（Bot / Web / 调度认领）而设——对比盘无此需求。
3. **时序风险**：改造 runner 存储契约将使进行中的 3 日影子对照与 67 项回归失效。
4. 外部先例（调研）：小型纸面/组合账本在开源项目里普遍用**文件**（JSON/pickle），
   选 SQLite 的样本都是"多账户 / 并发写 / 需要表查询"场景。

**`paper_repo.py` / `paper_trading_service.py`（任务 02 产物）状态：未采纳、勿接线**

- 二者是**刻意隔离**的独立 SQLite 实现（与真实 Portfolio 库零共享，内含订单幂等与通知 outbox），
  但**无任何生产消费者**（仅自身单测引用）。模块 docstring 已标注状态。
- **不要**在对比盘路径接线；如未来出现多进程共库需求，须先评估复用 `src/storage.py` 的
  `Portfolio*` 表，并显式接受上述哈希链失效与审计方式变更——另开独立任务，不并入本次修复计划。

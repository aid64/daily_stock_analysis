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

> **511010 手数**：债券 ETF 最小 **100 份/手**（不是 10 份）；¥12,411 资金下 511010 约 ¥14,000/手，G/H 的防御腿可能只能留在现金。2026-10-22 已把 runner 的 10 份/手改为与执行内核一致的 100 份/手（见 §2.3），这是现实约束，不是漏单。

## 2. 调度与时间

- Windows 计划任务 `DSA_Deck_Intraday`：每 20 分钟 → `compare_deck_task.ps1 -Mode intraday`
- Windows 计划任务 `DSA_Deck_Close`：**工作日 15:00:30 / 15:30:30 / 16:00:30（三次唤醒重试）**，每次均 `WakeToRun=True` + `StartWhenAvailable=True` → 同一 `-Mode close`（2026-10-27 修正；此前误注册为 `-Mode intraday`，靠盘中轮 15:00 catch-up 才触发收盘，属偶然路径）；用于机器休眠或 15:00 网络未就绪时唤醒并在收盘后补做。`close_done` 幂等，成功后的重试轮直接跳过。与 `DSA_Deck_Intraday` 通过 runner 独占锁 + `close_done` 幂等：谁先拿到锁谁执行，另一轮打印"另一实例运行中"或"收盘轮当日已执行"后退出。`run_close` 成功写 marks 时同步写 `close_done`；直接 `-Mode close` 在当日已 close_done 时跳过（2026-10-28 修）。
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
6. `DSA_Deck_Intraday` 每 20 分钟仅在交易日 **09:20–15:20** 触发，`StartWhenAvailable=False`、`ExecutionTimeLimit=30min`（2026-10-28 修：盘后补跑曾在 21:03 卡在行情 prefetch 并占住对比盘锁）。收盘由 15:00/15:30/16:00 `DSA_Deck_Close` 负责；`WakeToRun` 仍为 False（避免整夜唤醒）。runner 独占锁在属主 pid 存活时增加 **6h 锁龄硬上限**（所有任务 `ExecutionTimeLimit≤2h`，真实活跃锁不可能超过 6h）：超龄视为 pid 复用/探活失真残留并清除，避免残留锁让次日整轮静默跳过。

### 2.3 10-22 缺价事故与收盘闸（2026-10-22 盘后）

**事故**：15:00 轮启动时 DNS（`api.waditu.com` / 腾讯 / 飞书）全面失败。C/D 有持仓但取不到现价，旧 `revalue` 把缺价持仓排除出净值 → C/D 被记成只剩现金（-97.8%），且 `close_done` 仍被写入，后续行情恢复的轮次不会补写。A/G/H/E 因目标缺价报【异常】未成交；F/F2 在数据源返回 None 时异常；K 实际未成交却报“建仓→”；I/J/M/O 在数据源缺失时返回空列表被登记为成功。

**修复（同一语义覆盖所有入口）**：

1. **核心基准收盘闸**：`run_close` 在写 marks 前检查 C/D 持仓现价；先对**全部普通持仓**补拉一次（六源重试 + 腾讯直连；固定价值/逆回购除外），核心 C/D 仍缺则返回 `【异常】收盘缺价…`，**不写 marks/注册表**；`run_intraday` 只有收盘结果不以 `【异常】` 开头才写 `close_done`。`--mode close/intraday` 的 `【异常】` 输出现在返回 rc=1，15:00/15:30/16:00 的 close 缺价会让任务 LastTaskResult 非零。行情恢复后的后续轮次/手工 close 会补写当日 marks。
2. **估值兜底**：`revalue` 对非核心缺价持仓按成本价计入并标记 `valuation_status.unvalued`（与 `slot_equity` 同口径），不再把缺价误记成现金全额亏损。
3. **手数一致性**：511010 等债券 ETF 也是 **100 份/手**（此前 runner 按 10 份、执行内核按 ETF 100 份，买单被 `lot_size` 拒绝）；`_lot_size` 统一为 CB 10 / 其余 100；`mwu_target_orders` 的买入差额按整手向下取整，避免奇数旧仓导致非整手买单。
4. **任务诚实性**：K/I/J/M/N/O/E/F/F2 对 `apply_orders` 拒绝显式返回【顺延】（不再“建仓→”/“调仓→”后登记成功）；候选存在但全部无报价返回【异常】；`pead/reversal/holder/industry/smallcap` 的数据源全失败显式抛错，不再用空列表冒充“无候选”。
5. **10-22 台账修复**：按 510300=4.107、512890=1.202 修复 10-22 C/D 净值（12,179.8 / 12,296.0），并按成交成本补正 10-21 的 C/D marks（12,406）；`_actions` 增加校正说明。月度注册表在事故后按实际状态重置，10-23 继续补做，不把当天的空结果当作当月完成。

**唤醒时序边界**：15:00:30 的 `DSA_Deck_Close` 可能在网络就绪前启动（10-22 15:00:32 DNS 全断）。旧实现仍写 `close_done`，把 15:02/15:22 的补做锁死；新收盘闸在 C/D 缺价时整轮顺延且不写 `close_done`。由于交易所戳在 15:00 后冻结为 15:00:00，后续 20 分钟轮仍可在网络恢复后补成交/补写 marks；若整日不恢复，当日 marks 缺失：17:30/20:00 审计在 15:10 后交易日会判 **marks/注册表 FAIL**（退出 1 + 飞书告警），并可用手工 `-Mode close`（收盘价口径）补写，而不是记成 -97% 假净值或被静默。顺延原因会在后续轮通过飞书推送（`dedup_key=deck_close_error_YYYYMMDD`，网络恢复后可见），不再只留在日志里。2026-10-29 实测三轮回测均缺核心价、旧 runner rc=0，已补 rc 与补价逻辑。

**收盘缺失手工恢复 runbook（2026-10-29 实测）**：

1. 确认 `_control.close_done` 不是当日、当日有持仓的槽需要 marks；网络至少 `qt.gtimg.cn` 可用。
2. 补做收盘（收盘价口径，不写新成交）：
   ```powershell
   cd D:\daily_stock_analysis\daily_stock_analysis
   py scripts/compare_deck_runner.py --mode close
   ```
   成功输出以 `【对比盘收盘` 开头并写 `close_done`/marks；若仍返回 `【异常】收盘缺价`，等行情源恢复后重跑，**不要**用历史价回填。
3. 刷新收盘状态备份与第二副本：
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup_close.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup_offsite_task.ps1
   ```
4. 刷新收盘快照并审计：
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_deck_audit.ps1
   ```
5. 核对：`close_done`/marks 为当日、审计结论“正常”、收盘快照 mtime 在 15:00 后；若账户操作单也需补，走 09:00 管线/手工导出，**live 推送前先确认**。


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
- 每日 17:25 `DSA_Ledger_CloseBackup`：独立全量收盘备份到 `data/backups_close/<日期>/`（DB/.env/策略/决策快照/操作单/台账），与 08:30 的 `data/backups/` 分离、保留 14 份、`--verify` 校验；不覆盖早上备份
- 控制键 `_control`：`close_done`（收盘幂等）/ `event_scan_last`（事件扫描每半天一次；**仅扫描未返回【异常】时写键**，失败下一轮重试；键按交易所时间而非墙钟）/ `rebalance_done`（月度登记：全部成功 `"all"`，部分成功为已成功标签列表，每日撮合窗续做至齐备；非撮合时段顺延不执行）/ `week_rebalance_done`（周度同构，同周后续交易日续做）/ `bootstrap_done`（C/D 一次性建仓，`"all"` 或已成功标签列表）/ `erp_scaled`（PE 分位总闸已缩放槽登记；槽位周度/月度重建成功后清除，同轮 `erp_scale_pass` 重新应用）/ `e2_state`、`erp`、`crash_state` 等风控状态 / `_actions`（当日动作，盘中+收盘合并）
- 执行日志：`logs/compare_deck_task.log`（start/exit + runner 输出）
- 每日 **17:30 与 20:00**（两次）`DSA_Deck_Audit`：先生成收盘台账快照 `.scratch/dsa-strategy-validation/postclose/compare_strategies.YYYYMMDD.json`（临时文件校验后原子替换，保留最近 30 份），再只读运行 `compare_deck_audit.py`，追加 `logs/compare_deck_audit.log`（含 start/exit）；不写 live 台账。审计 FAIL 时 best-effort 推送飞书（`dedup_key=deck_audit_fail_YYYYMMDD`），不改变审计退出码；任务 `LastTaskResult` 为审计退出码。监控边界见 §13
- 每日 17:25 `DSA_Ledger_CloseBackup`：独立全量收盘备份（见 §5），审计在 17:30/20:00 能检查到当日 `收盘备份`
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

**目的**：`DSA_Deck_Intraday` 每 20 分钟但 `WakeToRun=False`；机器休眠时会整体错过 15:00 撮合窗（10-20 实测 14:42-21:02 空档，首次建仓顺延）。新增独立任务，在工作日 15:00:30 唤醒，并在 15:30:30/16:00:30 各重试一次（最多三次），避免给 20 分钟任务开 `WakeToRun` 导致整夜每 20 分钟唤醒。

**形态**：动作使用 `scripts/run_hidden_sync.vbs`（`wscript.exe //B //Nologo run_hidden_sync.vbs "powershell ... compare_deck_task.ps1 -Mode intraday"`）：同样隐藏窗口，但 **`sh.Run(cmd,0,True)` 等待子进程并回传退出码**——任务调度器全程跟踪，`LastTaskResult` 有意义，避免 `run_hidden.vbs` 非等待导致 launcher 退出后子进程被清理（实测曾丢 exit 行）。`-Mode intraday` 在交易所时间 ≥15:00 时触发 `run_close` 并设置 `close_done`。设置：`WakeToRun=True`、`StartWhenAvailable=True`、`DisallowStartIfOnBatteries=False`、`StopIfGoingOnBatteries=False`、`MultipleInstances=IgnoreNew`、`ExecutionTimeLimit=PT2H`，principal 与既有任务一致（Interactive/Limited）。

**注册**（PowerShell 原生 cmdlets；实际注册 2026-10-21）：

```powershell
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '//B //Nologo "D:\daily_stock_analysis\daily_stock_analysis\scripts\run_hidden_sync.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\daily_stock_analysis\daily_stock_analysis\scripts\compare_deck_task.ps1 -Mode intraday"'
$triggers = @(
  New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]'15:00:30'),
  New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]'15:30:30'),
  New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]'16:00:30')
)
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'DSA_Deck_Close' -Action $action -Trigger $triggers -Settings $settings -Principal $principal
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

### 8.2 盘前检查任务 `DSA_Deck_Morning`（2026-10-26）

**目的**：把每日运行起点前移到盘前，在 15:00 收盘前就发现 server/飞书 Stream/DNS/08:30 备份/台账异常，而不是等到收盘轮失败。任务只读，不写台账、不发交易指令；正常日静默，任一关键项失败才发飞书告警（`dedup_key=deck_morning_YYYYMMDD`，每日最多一条）。

**检查项**：本机 `/api/health`（keepalive 重启窗口重试 3 次×5s）；`/api/v1/assistant/runtime/status` 的 `running` 与 `feishu_stream_alive`；`qt.gtimg.cn`、`push2.eastmoney.com`、`open.feishu.cn` DNS；`quant_research/data/pit/convertible_pit.parquet` 末端距今 ≤7 天（超期 FAIL，暴露 E/H 数据断供）；当日 `data/backups/<date>/stock_analysis.db` 是否存在且非空；台账 `close_done`/最近 mark/主动槽数量/当前周月注册表状态（信息项，不阻断）。`close_done` 在 15:00 前已等于当日只记 WARN。

**形态**：工作日 08:45，`WakeToRun=True` + `StartWhenAvailable=True`，`run_hidden_sync.vbs` 隐藏且等待子进程；包装脚本 `scripts/morning_check.ps1` 追加 `logs/morning_check_task.log`（>5MB 轮转）并透传退出码；检查脚本 `scripts/morning_check.py` 支持 `--no-notify` / `--json`。

**注册（2026-10-26 实际执行）**：

```powershell
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '//B //Nologo "D:\daily_stock_analysis\daily_stock_analysis\scripts\run_hidden_sync.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\daily_stock_analysis\daily_stock_analysis\scripts\morning_check.ps1"'
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]'08:45:00')
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'DSA_Deck_Morning' -Action $action -Trigger $trigger -Settings $settings -Principal $principal
```

**验证（2026-10-26 23:44）**：手动 `Start-ScheduledTask`，`LastTaskResult=0`，日志显示 server/Stream/DNS/备份全部 PASS，主动槽 0/13 与 `close_done=2026-10-26` 仅信息/WARN。

**同日相关设置**：`DSA_Ledger_Backup`（08:30）、`DSA_Ledger_CloseBackup`（17:25）、`DSA_Deck_Audit`（17:30/20:00）均已开启任务级 `WakeToRun`，避免机器休眠把收盘证据任务拖到夜间。10-26 实测 16:06 休眠导致 17:25/17:30 延后到 20:26；开启后次日生效。

**移除/回滚**：`Unregister-ScheduledTask -TaskName 'DSA_Deck_Morning' -Confirm:$false`；备份/审计任务的 `WakeToRun` 可通过 `Set-ScheduledTask -TaskName <name> -Settings` 恢复原值。

### 8.3 开盘建仓任务 `DSA_Deck_Open`（2026-10-27）

**口径变更（用户确认）**：首轮主动槽建仓改为 **paper 账本、09:30-10:00 开盘窗、按开盘/首笔实时价成交**；15:00 收盘轮只负责估值、marks、`close_done` 和收盘侧风控，不再承担首次建仓。评估起点从首个主动槽实际成交的交易日（预计 2026-10-27）开始。

**执行**：工作日 09:31 / 09:45 / 09:55 三次触发（09:31 首次，后两次在 09:30-10:00 窗口内重试失败/顺延子任务；`MultipleInstances IgnoreNew` 防止重叠），`WakeToRun=True`，`run_hidden_sync.vbs` 隐藏等待；`compare_deck_task.ps1 -Mode open` → `run_open()`。执行前先检查/刷新 A/B 用的 `feeds_snapshot_v5.pkl`（**akshare 源**，目标为上一交易日；默认 baostock 会滞后数日），刷新后若已过 10:00 则本轮顺延。随后只执行到期的 bootstrap/周度/月度注册表并应用 ERP 缩放；**不写 marks、不写 close_done、不发交易指令**。**周期边界保护（2026-10-28 修）**：周首/月首开盘时当日 bar 尚不存在，A_动量/G_MWU融合（周）与 H_MWU_CB融合（月）的引擎会停在上一周期目标；open 轮主动跳过这三项并留到 15:00 收盘按当日 bar 重算，B/E/F/F2/I/J/K/M/O 等不依赖当日 bar 的槽仍在开盘执行。收盘引擎还会校验目标 as_of 必须在当前周/月；若数据源尚未给出新周期 bar，A/G/H 返回【顺延】而不按旧周期目标成交，待 15:30/16:00 或次日窗口重算。注册表沿用 `all`/列表幂等门：失败子任务保留列表，后续 open 轮或收盘轮在撮合闸内续做（若在收盘续做，该部分成交价为收盘价，需在审计中如实记录）。

**注册**：

```powershell
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '//B //Nologo "D:\daily_stock_analysis\daily_stock_analysis\scripts\run_hidden_sync.vbs" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\daily_stock_analysis\daily_stock_analysis\scripts\compare_deck_task.ps1 -Mode open"'
$triggers = @(
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]'09:31:00')),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]'09:45:00')),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]'09:55:00'))
)
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'DSA_Deck_Open' -Action $action -Trigger $triggers -Settings $settings -Principal $principal
```

**边界**：open 窗只认本机墙钟 09:30-10:00（交易所时间戳开盘初可能仍是上一交易日收盘）；任务因休眠/关机晚于 10:00 时跳过，不补开盘价。收盘轮仍可在撮合闸内补做未完成注册表，但价格口径为收盘，不冒充开盘成交。回滚：`Unregister-ScheduledTask -TaskName 'DSA_Deck_Open' -Confirm:$false`；如需恢复“仅收盘建仓”，移除 open 任务并 revert 对应提交。

### 8.4 PIT 盘前刷新任务 `DSA_PIT_Daily` / `DSA_PIT_WeeklyFull`（2026-10-27）

**背景（2026-10-27 实测根因）**：东财 `RPTA_WEB_KZZ_LS` 接口新增两列后，akshare `bond_zh_cov_value_analysis` 的固定列位重命名直接 `ValueError`，PIT 尾部增量自 09-18 起全部失败；`--limit 50` 又每次都从固定排序头部开始，长期卡在退市券。结果是 PIT 末端停在 10-16、当日截面仅 17 只，E/H 的转债输入严重失真。

**修复**：`quant_research/scripts/update_pit_tail.py` 改为按 `DATE`/`FCLOSE`/`SWAPOR` 字段名直连东财，不再依赖 akshare 列位；增量队列改为“当前目标债 → 活跃券（120 天内，按缓存最旧优先）→ 陈旧券”，并用 `.tail_cursor.json` 滚动游标续跑；新增 `--active-only`。`build_pit_snapshot.py --assemble-only --start 2018` 仍在增量后重组；raw CSV 与 parquet 均走临时文件 + `os.replace` 原子写，进程中断不会留下半文件。PIT 增量只写入已完成交易日（15:30 前排除当天盘中行，并清理 raw 中晚于 cutoff 的行），盘中重试/手工补跑不会再污染 PIT 截面。

**任务**（均 `WakeToRun=True`、隐藏启动、日志 `daily_stock_analysis/logs/pit_refresh.log`）：

- `DSA_PIT_Daily`：周一至周六 07:30，`-Scope active`（约 330 只，预算 2700s）→ assemble，确保开盘前全活跃池有前收盘价。
- `DSA_PIT_WeeklyFull`：周日 04:00，`-Scope full`（914 只，预算 9000s）→ assemble，维持完整历史截面；避开 09:00 管线。

09:00 的 `daily_pipeline` 仍保留 `--limit 50` 增量作为兜底，但不再承担“保证截面完整”的职责。`update_pit_tail` 与 `build_pit_snapshot` 共用 `data/pit/.pit_update.lock.json`：持锁进程存活且锁龄 <6h 时，另一进程跳过增量/assemble；parquet 用临时文件+`os.replace` 原子替换。2026-10-29 10:03/10:18 实测 Windows PIT 任务与 server 补跑管线并发，锁用于避免 raw/parquet 互写；管线若遇到 Windows 任务持锁会跳过 PIT/assemble；assemble 后日期护栏要求 PIT 末端达到最近完整交易日，落后则管线失败并 5 分钟重试，不导出/推送操作单（2026-10-29 10:28 曾用 T-2 PIT 推送，已补此闸）。

**回滚**：`Unregister-ScheduledTask -TaskName 'DSA_PIT_Daily' -Confirm:$false`（周任务同理）；`update_pit_tail.py` 的列位/游标改动可 revert 对应提交，但会恢复 akshare 列位故障与 50 只固定头部停摆。

### 8.5 H 目标盘前预计算 `DSA_Deck_Precompute`（2026-10-27）

**问题**：H_MWU_CB 的 853 只 CB feeds 引擎提取在冷缓存下约 10 分钟；15:00 收盘轮再重算会把补建/调仓推到 15:30 之后。非调仓日引擎可能没有新目标，预计算应成功跳过（exit=0），不能把“无新目标”当成任务失败（2026-10-29 13:45 修）。

**机制**：工作日 13:45 运行 `scripts/precompute_deck_signals.py`，把 `extract_mwu_targets.py --strategy etf_mwu_cb` 的 JSON 写入 `output/deck_signal_cache/etf_mwu_cb_YYYYMMDD.json`；15:00 `mwu_rebalance` 读取同日缓存，跳过引擎。缓存缺失、损坏、`cache_date` 非当日、策略配置不匹配时自动回退 1800s 实时引擎。**调仓日保护**：缓存 `as_of` 等于当天（说明预计算只看到盘中不完整 bar）时也不使用；H 的 `as_of` 月份与当前月不一致（月初仍只有上月目标）时同样强制收盘重算。

**任务**：`DSA_Deck_Precompute`，周一至周五 13:45，`WakeToRun=True`，隐藏启动，日志 `logs/deck_precompute.log`，执行限 60 分钟（2026-10-27 实测 853 只 CB feed 冷启动约 20 分钟，900s 不足；命中缓存后收盘 H 不再重算）。回滚：`Unregister-ScheduledTask -TaskName 'DSA_Deck_Precompute' -Confirm:$false`；移除缓存读取代码即回到纯实时引擎。

## 9. 回归保护网

- `tests/test_compare_deck_runner.py` 120+ 项：纯函数（费用/换算/时段/价差/分位/缓冲带遗留函数/回撤）+ **10-08 月度大考端到端**（mock 时间/行情/引擎，tmp 台账全链）+ 月度/周度注册表（子任务失败/顺延次日重试）+ 空槽首建仓行情预取与按当前 equity 定仓 + 启动会话捕获（入口 → run_close，慢行情不二次取时）+ marks 后置 + F/F2 每票预算 + 快照末端重建 + 核心缺价收盘闸（不写 marks/close_done）+ 缺价成本兜底 + 511010 整手 + 任务拒绝/数据源失败诚实性 + 子进程 UTF-8 解码 + 盘中扫描失败重试/交易所 halfday + PE 分位总闸缩放 + 独占锁四分支 + 撮合窗边界；全部离线可复现（tmp 台账，不依赖生产台账与外部库）
- `tests/test_paper_execution.py` 23 项（任务 08 执行内核：11 拒绝码/整手/涨跌停/T+1/现金守恒）+ `tests/test_compare_deck_cohort.py` 8 项（开账/种子/容器契约）+ `tests/test_compare_deck_audit.py` 18 项（含时间健康跨日戳/收盘后不报时钟偏差/多源同日比较/执行日志 start-exit 配对/唤醒任务/注册表与主动槽覆盖/任务结果 PASS 与非零 WARN/备份与收盘快照缺口）+ `tests/test_deck_registry.py` 6 项 + `tests/test_deck_slot_contracts.py` 17 项 + `tests/test_compare_deck_shadow.py` 10 项：审计三态结论/退出码传播 + 注册表 18 槽/PE 分位总闸作用域单一真源一致性 + 槽位数据契约（含 M 数据缺失显式失败）+ 影子只读校验
- 探针提取脚本 `quant_research/scripts/extract_mwu_targets.py`（ETFMWUProbe/MomentumProbe）曾因多轮 patch 丢 `_target_portfolio`——修改探针后必须跑端到端回归

## 10. 审计与真钱

- 审计脚本：`scripts/compare_deck_audit.py`（只读，30+ 项核查，✓/⚠/✗ 三态；含注册表完成度/主动槽覆盖/任务结果/08:30 备份/收盘备份/收盘快照）——检查员 AI 每轮只跑此脚本并解读
- 检查员解读规则（三态结论 + 退出码）：
  - 任一 ✗（FAIL）→ 结论 `【异常】N 项 FAIL`，退出码 1，须人工介入
  - 无 ✗ 但核心证据（marks 新鲜度 / 执行日志 / 抽样净值 / 注册表 / 主动槽覆盖 / 任务结果 / 唤醒任务 / 备份 / 收盘快照）任一非 ✓ → 结论 `样本不足, 无法判定`，退出码 0
  - 核心证据全 ✓ → 结论 `正常`，退出码 0
  - 台账不可读 → 退出码 2
- 关键 FAIL 判据：marks 断供 >15 天 FAIL / >10 天 WARN；最近 intraday 执行日志 exit≠0 → FAIL；抽样净值偏差超阈 → FAIL
- 时间健康 / 多源一致性（2026-10-21 修）：时间戳非当日只标注「市场已收盘」，不再把跨日旧戳误报为 7.8h 时钟偏差；多源一致性按**同一行情日**比较 baostock 与腾讯（盘中跳过），不再拿硬编码 2026-09-30 与实时价比较
- 执行日志 start/exit 配对（2026-10-21 修）：最后一轮只有 start（任务被终止或仍在运行）显式 WARN，不再取最后一条事件或拿更早的 exit 误判；start-only 日志不再触发 IndexError
- 唤醒任务检查（2026-10-21 增，2026-10-27 补 mode）：`DSA_Deck_Close` 的 enabled / `WakeToRun` / sync launcher / `-Mode close` / 15:00:30 触发器；任一不满足 WARN（10-27 实测曾误注册为 `-Mode intraday`）
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

- **分红/除息的口径**：价格序列**不做除息复权**；每日 `daily_dividend_pass` 对股票走 tushare `dividend`（仅 `div_proc=实施`，现金取税后 `cash_div`），ETF 仍走 `fund_div`（分红查询按日缓存 1d，避免新事件被长缓存遮蔽）；起算日取 `max(台账起点, 已入账日, held_since)`，防止建仓前事件入账；按报告期+除息日+每单位现金/送转去重（Tushare 同一事件常返回多行，直接求和会双计）；送转股按 `stk_div` 调整份额与成本。除息日价格与现金到账仍有时点差，数据源缺事件时需人工按除息日核对并校正当期 marks；不能把“未复权价格”直接当成未计入分红。
- **T+1 已由执行内核强制**：`src/core/paper_execution.py` 按资产类别施加策略（股票/ETF `t_plus_1=True`、转债/逆回购豁免），超额卖出返回 `T_PLUS_1` 拒绝码（任务 08 内核）；残余差异仅"卖出资金当日可用"这一账户现金流语义，低频（周/月）调仓下影响可忽略。
- **涨跌停/过期报价已接入撮合**：六源链只提供昨收时，`apply_orders` 会按板块规则计算涨跌停（主板 10%、创业板/科创板 20%、北交所 30%、主板 ST 5%、ETF 10%、转债 20%），腾讯直连优先使用其显式涨跌停；涨停买入、跌停卖出、`is_stale` 报价均返回拒绝码。六源链仍缺价的股票也会走腾讯直连兜底（原仅 ETF/转债）。
- **佣金假设**：万 0.845 + 最低 5 元为 quant_research 研究假设，未与真实券商账户费率核对——晋升真钱前必须核对。
- **Y 槽打新未自动跟踪**：中签回填依赖人工；转债打新收益未计提。
- **盘后行情污染**：收盘后六源链个别源返回异常价（实测 tickflow 盘后 512890=1.196 vs 收盘 1.180）。成交仍受 `_fills_open`/交易所戳约束；`StartWhenAvailable` 可能触发盘后补跑，`risk_round` 在非交易时段只记录、不迁移状态，但仍可能基于污染价产生告警文本，需人工判断。
- **熔断指标**：国证2000（sz399303）为代理指标，非真正的微盘股指数（万得微盘股指数无免费实时源）；-3% 阈值为先验值，未回测校准。
- **N 槽不可在干净克隆复现**：驱动 `quant_research/vendor/Sequoia-X/n_slot_driver.py`（vendored 第三方仓库，~299MB）与其回填行情数据均**不入库**（`.gitignore` 已忽略 `quant_research/vendor/`）。上表 N 行的"已激活"仅对本机工作区成立；干净克隆下 `sequoia_ready` 闸为假、N 槽**条件式跳过**（不报错、不参与调仓）。复现需自行 vendored Sequoia-X 并回填行情数据。
- **运行在线依赖**：收盘轮/撮合窗要求交易日本机在 15:00-16:13 保持运行；休眠时由 `DSA_Deck_Close`（WakeToRun）唤醒，整机关机仍无法唤醒。若 15:00:30 唤醒后网络未就绪：核心 C/D 缺价会整轮顺延且不写 `close_done`，15:02/15:22/后续轮（交易所戳仍为 15:00:00）可补成交/补写 marks；若整日不可用，当日无成交且可能有 marks 缺口，注册表续做至下一窗口。不得用历史价回填。
- **A/B 数据 as-of 差异**：B 走 `feeds_snapshot_v*.pkl`，文件 mtime 超 7 天**或末端数据日早于上一交易日**时重建（构建脚本以临时文件 + `os.replace` 原子替换，避免半文件）；A/G/H 走 tushare loader。B 重建后数据源仍滞后时消息标注实际末端（如 `至 2026-10-16`）。解读 A/B 排名时需知悉该差异。
- **收盘后备份**：每日 08:30 的 `DSA_Ledger_Backup` 仍早于收盘；17:25 `DSA_Ledger_CloseBackup` 已提供独立的收盘全量备份（`data/backups_close/<日期>`，DB/.env/台账，保留 14 份），17:30/20:00 审计会检查 `收盘备份` 目录。**仍与 live 同盘、未离机**；离机目标尚未配置，抗盘坏仍需第二存储。
- **收盘快照**：17:30/20:00 审计前生成 `postclose/compare_strategies.YYYYMMDD.json`；审计要求快照与 live 台账**字节 sha 一致**且时间在 15:00 后，否则 WARN（避免盘前快照误判）。快照不是完整备份。
- **评估窗口起点**：2026-10-27 首次形成主动槽持仓（11/13 有持仓；B 无轮动信号、N 无形态候选，保持现金）。因 09:31 本机唤醒时 DNS 全断，首建仓由 15:00 收盘轮按收盘价补建，`_control.eval_basis=close_fallback`；60 个交易日窗口自 10-27 起算。`prereg._meta` 已留痕：2026-12-31 改为中期检查，60 交易日硬复核顺延至 2027-01-31（≥60 交易日，节假日顺延），DSR 顺延至 2027-04-30（≥120 交易日）；不改变判定标准，只保持样本长度。
- **G/H 防御腿 511010 不可执行**：511010 按 ETF 100 份/手、价格约 141 元，单槽 ¥12,411 买不起一手；G/H 实际持有 512010 + 现金，目标中的 511010 权重无法落地（不是缺价），调仓消息与 note 会显式标注“不足一手、按现金”。解读 G/H 收益时需按实际持仓/现金，不得把目标权重当成已执行。
- **退市整理股过滤**：小市值候选来自 `stock_basic(list_status=L)`，退市整理股（如 301139 元道退）仍可能出现；2026-10-27 已加 `ST|退` 过滤并对当日误买按成本价逆回留痕。
- **审计监控边界**：`DSA_Deck_Audit` 每日 17:30/20:00 检查 marks/执行日志/抽样净值/注册表/主动槽/任务结果/唤醒任务，**并检查 08:30 备份、收盘备份、收盘快照（sha+15:00 时点）与 `DSA_Ledger_Backup` 结果**；审计 FAIL 会 best-effort 推送飞书（失败不改变退出码）。仍**不检查**离机目标或审计任务自身。收盘快照只含台账，不含 DB/`.env`。


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
- 只读消费方：`compare_deck_audit.py`（`json.loads` 后跑 30+ 项检查）、`compare_deck_cohort.py`（归档 + 开账）、
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

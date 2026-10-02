# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

> For user-friendly release highlights, see the [GitHub Releases](https://github.com/ZhuLinsen/daily_stock_analysis/releases) page.

## [Unreleased]
- [修复] 对比盘两项深层缺陷：①`apply_orders` 卖出按 symbol 整条移除持仓——部分减仓（等权再平衡/MWU 目标微调）会把整条持仓清零，改为扣量语义（成本价保留），实盘复现（A 槽误清仓已恢复）并补 2 单测；②A 动量槽信号由手搓 60 日动量+MA250 简化版切换为引擎口径（`extract_mwu_targets.py --strategy etf_momentum` 探针 `MomentumProbe._compute_momentum`，官方 17 池配置），ranked 结果与影子信号交叉验证同构；③调仓执行增加交易所时段闸（盘后 tickflow 曾返回污染价 1.196 vs 收盘 1.180，非交易时段一律只记录不下单）；引擎 stdout 解析加固（tushare login 噪声容错）。
- [改进] 风控双维升级：①E 信用监测从单一价格阈值扩为"价格+事件"——每轮扫描 4 只持仓券公告（下调/回售/违约/兑付/评级/质押冻结/逾期），命中即【信用事件】告警并入 credit_flag；②熔断触发新增宏观信号——国证2000（sz399303）跌幅 ≤-3% 与持仓微盘中位数 ≤-4% 并联任一触发（2024-01 式系统性微盘崩盘比持仓中位数更早感知）；③文档新增"已知局限"节（分红除息未调整/T+1 未建模/佣金假设未与真实券商核对/Y 槽打新未自动跟踪/盘后行情污染/熔断阈值未回测校准）。
- [新功能] 检查员核查工具化 `scripts/compare_deck_audit.py`（只读 8 项：台账自洽/执行日志/计划任务/抽样净值/时间健康，✓⚠✗三态+JSON 输出），检查员 AI 降为"跑脚本+解读"；新增 `docs/real-money-playbook.md` 真钱执行手册（Y 逆回购券商操作/ X 全面要约人工复核清单/主动策略晋升流程/风控红线），纸面→真钱通道闭环。
- [测试] 10-08 月度大考端到端回归固化（`test_end_to_end_monthly_rebalance`：mock 交易所时间/行情/引擎子进程，验证 close_done/rebalance_done/marks/Y 槽面值模型，tmp 台账不碰真实数据）；runner 加 O_EXCL 单实例锁（15 分钟残留清理），防长调仓与下一轮计划任务并发写台账；ps1 日志 5MB 轮转；Y 槽月末窗口提示入收盘小结。
- [新功能] 对比盘扩至 15 槽位，新增 L_北向择时：北向净买入 250 日分位择时（≥60% 持有 510300/<40% 空仓/中间维持），与 C 槽同标的构成受控实验（择时 vs 买入持有）——直接检验聪明钱信息优势在 2024-08 披露规则改变后是否衰减；数据 tushare moneyflow_hsgt 实测 2026-10 逐日可得（此前"数据断供"顾虑被数据推翻）；信号纯函数 `north_percentile`/`north_decision` 已测。
- [文档] 量化社区无关键词热榜+原生语言搜索（"实盘"/"翻倍"）：三个关键词框架外的新维度落台账 community_signals （成交量收缩择时/个股股息率排序/小市值拥挤度社区监控），均不新建槽位（避免稀释），12-31 硬复核评估接入；回测平台口径差异实证帖（同花顺 vs 聚宽）收藏为方法论。
- [改进] tushare 15000 积分接口矩阵实测：15/16 可用（stk_factor/cyq_perf/cyq_chips/margin/limit_list_d/top_list/moneyflow/income/sw_daily/repurchase/dividend/block_trade/hk_hold 等全通），并落地两项数据升级：①熔断信号新增全市场跌停家数≥50（limit_list_d，原预案条件真实化，与微盘中位数/国证2000 三信号并联）；②M 槽候选 cyq_perf 二次验证——最新获利比例>60% 剔除（户数降+获利已高=庄股已拉升防追高），实测过滤后候选获利比例 5-22% 安全区。
- [修复] 10-08 大考顺序冲突：E2 减半会被月度调仓的满仓目标买回（择时当天失效）——cb_rebalance 现感知 e2_state（defensive 时目标×0.5）；ERP/E2 防御态下月度重建的主动槽后置缩放×0.5（总闸系数语义=策略目标×总闸）；09-30 收盘补算入台账（F 当日 -1.8%/F2 -1.5% 微盘跌、D +1.35% 红利涨——8.27 拥挤度讨论的风格切换正在兑现）。
- [新功能] 对比盘扩至 17 槽位，新增 N_技术形态对照（Sequoia-X 7.6k★ 七形态策略：均线放量/海龟突破/高窄旗/涨停洗盘/趋势跌停回补/RPS突破/定增，命中≥2策略优先等权5只）——第一轮调研承诺的"技术派 vs 因子派"对照组落地；数据回填（baostock 全市场日线，小时级后台）完成前条件式跳过（`sequoia_ready` ≥300万行闸）；供应商仓库 vendored 至 `quant_research/vendor/Sequoia-X` + N 槽驱动 `n_slot_driver.py`（七策略 JSON stdout）。
- [新功能] 对比盘扩至 16 槽位，新增 M_筹码集中度：股东户数降幅≥10% 排序选股（A 股散户行为因子——户数下降=筹码向主力集中），数据 tushare `stk_holdernumber` 按报告期全市场单次查询（2026Q2 实测 5500 行/1591 只候选，聚宽 2017 断供问题被 tushare 解决）；月度窗接线+fixture。聚宽社区广度搜索补充：涨停(无实证)/股东户数(有实践函数+断供警报)/低波动(理论帖为主)/筹码分布(陈浩体系+算法V2)。
- [新功能] 对比盘扩至 13 槽位，新增两个股票策略：I_PEAD业绩预告（最近40天预增等权5只，tushare forecast 逐日 ann_date 接口，学术最强异象+A股实证）与 J_短期反转（21日跌幅最深5只，剔ST/北交所/科创板+金额≥3000万流动性过滤，LSY2019 反向证据+STREV 实证）；周频反转被成本核算否决（每周换手佣金≈0.4% 吃掉 STREV 10-15% 收益），采用月频；北向跟随进观察名单（2024 后披露频率下降）；新增 `pead_universe`/`reversal_universe`/`pead_rebalance`/`reversal_rebalance` 及月度窗接线。
- [文档] 新增 `docs/data-source-matrix.md` 数据源矩阵（实测版）：实时链优先级/TickFlow 独有接口（main_indices 可用、market_stats 端点异常待查）/tushare 15 接口清单/akshare 各接口稳定性/baostock 交叉验证——并固化纪律：阈值信号双源交叉验证、单源限速缓存、新源先登记实测再写代码。
- [新功能] 对比盘预注册纪律落地：11 槽 `prereg` 卡（假设/方向预测/置信度/晋级与死亡标准/证据链/复核日 2026-12-31 与 2027-03-31 DSR），周五收盘小结追加"假设状态"节——对比盘从排行榜升级为实验注册表；ERP 总闸代码化：tushare `index_dailybasic` 沪深300 PE 10 年分位（当前 60.3% 中性区），滞回阈值 >80% 主动槽减半 / <60% 解除（乐咕接口兜底），周节流+状态机防震荡；F/F2 月度调仓加 2×slots 缓冲带（排名跌出才卖，换手减半以上，`select_with_buffer` 纯函数已测）；修复 watchlist_round 引用未定义变量的 NameError。
- [新功能] 对比盘新增 Y_确定性收益 槽：节前最后交易日逆回购 GC001（¥12,000 @年化1.125%，国庆计息 8 天）；修复 `_is_month_first_trading_day` 未对 trade_cal 降序结果排序导致月首判定取到月末的 bug（10-08 月度调仓窗验证恢复）。
- [改进] 微盘熔断改为分级设计（三年 726 样本校准）：单次触发为反向指标（触发后 20 日平均 +3.77%，13/16 反弹），改为 normal→watch(减1/3)→30日历日内再触→defensive(清仓) 状态机，月度窗 F 重建持仓自动恢复 normal；峰值回撤 ≤-5 纳入触发信号集（2024-01 复盘：日跌条件 1/22 才首触，指数已跌 18.4%）；ERP 阈值历史分层校准：便宜区(<20分位)未来60日 +6.76%/69%胜率（强证据），贵区(>80)减仓超额仅 +0.92%（弱证据，维持阈值并标注）。研究结论入台账 crash_study。
- [新功能] E2 转债估值择时代码化（研究验证回撤改善 31% 的唯一活组件）：tushare `cb_daily` 单次全市场当日行情 → 中位数 → 锚点插值阈值(≥127 减半/<118 恢复, 滞回)驱动 E 槽；实测 09-30 中位数 130.05 已入高位区——10-08 开盘将执行 E 减半；中位数序列逐日积累进 credit_watch 供未来真实分位计算；台账标注 A/G/H 暴露重叠警示与反转假说缺位。
- [新功能] 对比盘绩效观测完善：收盘小结排名行新增各槽回撤（历史峰值口径，`slot_drawdown` 纯函数已测）；`apply_orders` 买卖双路径累计 `fees_paid`（各槽初始化迁移，12-31 硬复核"扣成本为正"判据的数据基础）；`monthly_report` 月度报告（本月收益/回撤/费用/预注册状态）于本月最后交易日经 ERP 同位接入；audit 抽样净值消除变量泄漏（C/A/D/F 四槽实测偏差 0.4%-2.0% 全 PASS）。
- [新功能] 对比盘扩至 10 槽位：新增 H_MWU_CB融合（etf_mwu_cb 四专家含转债 PIT 双低；探针 `_rebalance_to` 置空免转债 feed，全池 853 券零日线成本；09-01 月度目标=4 ETF 76.5%+现金 23.5%，转债专家空选与 E 槽形成分化）；月度调仓窗由"每月 1-5 日"修正为"本月首个交易日"（tushare trade_cal 判定，修复 10 月 1-5 日长假导致 F/F2/E/G/H 月度调仓全部错过的问题）；G/H 槽位调仓接入 runner（周一/月度窗引擎重提取→整手先卖后买，纯函数 `mwu_target_orders` 已测）。
- [新功能] `quant_research/scripts/extract_mwu_targets.py`：backtrader 引擎探针（ETFMWUProbe 子类零侵入）提取 etf_mwu 最新调仓目标与专家权重——数据走项目数据链（tushare P-1 + PandasBarsData 含 amount 线 + engine.run 同口径预处理），对比盘新增 G_MWU融合 槽位（09-28 目标：防御 66.2%+半导体 16.9%+医药 16.9%，专家权重三足鼎立）。
- [新功能] 多策略对比盘确定性执行器 `scripts/compare_deck_runner.py`（2026-09-29/30）：8 槽位（ETF动量/轮动/沪深300/红利低波/转债双低/小市值20/小市值5神帖对照/事件套利）前向纸面跟踪——盘中事件套利价差核验（要约价公告API自动提取）、微盘熔断、转债信用监测、持仓异动告警，收盘净值重估与周/月度调仓（tushare 接口选股）；时段与交易日判断采用交易所行情时间戳+trade_cal 校准（本机时钟不可信场景），由 Windows 计划任务 `DSA_Deck_Intraday` 每20分钟触发，调仓/告警经 NotificationService 推飞书（route_type=strategy_compare）；配套 `scripts/compare_deck_task.ps1` 与 `tests/test_compare_deck_runner.py`（8 项：交易费用/先卖后买/现金不足跳过/时段边界/价差阈值）。
- [改进] 关闭每日通知摘要聚合（`NOTIFICATION_DAILY_DIGEST_ENABLED=false`），对比盘与事件类消息即时推送，不再产生 18:00 单条汇总消息；转债现价改走腾讯直连（六源链对带后缀转债代码覆盖不到，曾致 credit_watch 被写 0）。
- [chore] ZCode 定时任务降级为只读检查员（audit-only：核查执行链/台账自洽/计划任务存活），策略执行不再经由 AI 提示词层。
- [修复] 盲区审计修复批三（09-28）：①`docs/architecture/api_spec.json` 从 FastAPI openapi() 整册重生成（34 路径/37 操作 → 128/142，strategy 枚举缺失与 history 参数名漂移随重生成消除，此前仅覆盖 1/4 实际路由）；②runtime_scheduler 子进程投递契约——包装器 put 后补 close+join_thread（win32 spawn feeder 为 daemon 线程，大 payload 未刷完即被退出掐断）；③watchdog 测试 2/3 处预存失败根因修复：超时 2s→30s（win32 spawn+重型 import 启动本身 >2s，4/4 'timed out after 2s' 实证为误判非队列丢失——投递契约与超时两修缺一不可）、事件监控测试改按名过滤断言（G4 后 daily_pipeline 无条件注册使'恰好 1 个'断言过时）；④lastfailed 全部清零：test_dingtalk_sender 删除、test_bot_status_command 76 passed、runtime_scheduler 文件 27 passed（此前 3 个预存失败全清）。
- [chore] 盲区审计修复批（09-27，部署面+运行时层两代理全量核查后的安全项落地）：①CI `00-daily-analysis.yml` 删除 13 个已删渠道的 secrets 注入与状态 echo 共 48 行（每日生产任务不再向已删端点持有凭据；飞书键全保留，YAML 合法性验证）；②requirements.txt 删 dingtalk-stream/discord.py/PyNaCl 三个零 import 死依赖；③删除 tests/test_dingtalk_sender.py（引用已删模块，P5 漏删）；④dsa-desktop main.js 删 DINGTALK_STREAM_ENABLED 死键注入；⑤.env.example 补 FEISHU_ENCRYPT_KEY/FEISHU_VERIFICATION_TOKEN 可选键说明（代码读取但此前未文档化）。
- [chore] U5 文档治理第三批（09-27，全仓扫尾）：①删除 dsa 9 个自标 ARCHIVED/OUTDATED 历史文档（run-diagnostics-p0~p3、project-analysis、GAP-AUDIT、CLOSURE-FIX-PLAN、DESKTOP-OVERHAUL-PLAN、quant_research_architecture_research——git 历史可回溯），INDEX 演进区改为一行去向说明；②删除 dsa 根 4 个 0 字节测试桩；③删除 quant 遗留数据 4 个（universe_cb 旧口径 ×2、cb_candidate_union 已废除机制产物 ×2）；④ROADMAP-V2.2 与 FUTURE-PLAN-20260910 加 SUPERSEDED 横幅（定义与实际执行不符/引用修正前 E1 数字），HANDOFF 配套引用同步；⑤etf_mwu_cb_monthly.yaml 头注释的 +0.18% 旧数改为证伪警示（-3.45%）。本批 17 文件 -4029 行。
- [文档] E2 分位跨项目"矛盾"解除（09-27 实证，HANDOFF 0.5 修正）：quant 88.8% 与 dsa 68.3% 为**同一指标随行情移动**——两侧同为价格中位数 expanding 分位（构造逐位一致），转债中位数 09-07→09-24 跌 133.77→122.87（-8.2%），分位 92.4%→68.3% 下穿 80% 防守线（顶部密集区 -8% 绝对跌幅=24pp 分位落差，敏感性属已知特性，非缺陷）。此前 HANDOFF"口径不同源需钉死"的判断有误，已改写；E2 门当前为"可配置"态。附带市场观察：9 月转债整体回撤明显。
- [文档] U5 文档治理第二批（09-27，catalog↔注册表严格对账）：①`strategies.yaml` 补 3 条目——rejected ×2（cb_double_low_composite：E1 四臂全负/超额 -1.43pp；cb_revision_event：E3 超额 -0.56%）与 dormant ×1（dividend_lowvol_grid：执行形态暂缓），头部声明改为准确对账表述（16 条目，加载+19 单测验证）；②`strategy-catalog.md` 第五节补 17 池验证版表格行（本项目 WF 实证数字）与 **5.1 系统策略小节**（R1/R2/R3 非 catalog 选品、U0 签署状态、验证纪律指路）——至此注册表 16 条与 catalog 全部对应；③INDEX_EN.md 加 scope 声明（只覆盖用户文档，演进/运维区以中文 INDEX 为单一真源）；④.scratch 本地清理 402M→103M（serve 日志 + pt_p5 等 pytest 残留 280M，不入库项）。
- [文档] U5 文档治理第一批（09-27）：①`notifications.md` 从 344 行多渠道基线重写为 95 行飞书单渠道机制说明（发送链路/message_id 留存/路由/摘要总线/失败隔离/降噪），修正 :271 与 :238 的摘要自相矛盾（DIGEST 行从"预留不发送"改为与已实现摘要总线一致），179 处已删渠道引用清零；②`bot-command.md` 清零 6 处平台残留——架构图改 Stream 唯一接入、Webhook 路由节改收消息机制节、配置块换现行键（dingtalk/wecom/telegram 清零）；③删除 docs/ 6 个 zhihu_*.json 调研原始数据（约 1.1MB，catalog 来源底稿，git 历史可回溯）与孤儿文档 grok-bot-integration.md（INDEX 已无引用）；④INDEX.md 通知行移除过时标注。
- [新功能] ETF 动量影子信号跟踪上线（冻结豁免 v3，用户主动要求更早使用）：①quant `scripts/track_etf_momentum_signals.py`——每收盘计算 17 池动量得分与 top_k 选择，**直接调用 momentum_scores 与回测同源零漂移**，选债/防御逻辑镜像 ETFMomentum.next()，追加 output/etf_momentum_tracking/signals.jsonl（含执行约定"调仓日收盘→次日开盘"、新浪不复权口径 caveat、T+0/T+1 提示）；②dsa `/轮动信号` 命令（别名 影子信号/信号）——只读该 JSONL 文件契约渲染最新信号，横幅强制"影子跟踪·非投资建议·不落单"（registered 态实盘禁令维持），4 项单测（最新渲染/防御态/缺文件/坏行容错）。首个真实信号（as_of 09-24）：仅 2/16 过闸（512890 红利低波 +0.0902、511880 货币 +0.0024）——MA250+绝对动量过滤下属偏防御形态。外部调研依据：GitHub shadow engine 先例、"周五收盘算因子"工程惯例、集思录轮动共识、T+0/T+1 清单。holdout 零触碰；11-08 评估时本日志即影子期数据积累输入。
- [文档] 用户部署偏好登记（09-27，用户原话"更青睐中风险以及更高风险的策略"）：①中风险候选列为首选部署轨道，复现队列确认 多因子→行业动量（etf_momentum 第一候选不变）；②小市值维持 dormant（唤醒待冻结期满+用户明确确认）；③更高风险档维持"当前阶段不推荐"原判（解除需账户增长+独立预注册+影子期三条件）；④卫星 20-30% 仓位纪律不变——类型偏好不得默示推导仓位放大，调整属 U0 级显式签署。登记于 strategy-catalog 第五节部署实况。
- [修复] 备份任务条件层根因补齐（U4 观察日第 1/3 实证抓到）：09-27 自然触发再失败（0x800710E0），但包装日志零记录——错误发生在启动阶段而非脚本：任务设置 DisallowStartIfOnBatteries=true + StartWhenAvailable=false，笔记本 08:30 常在睡眠/电池态，触发即被拒、唤醒补跑仍被电池条件拒。**根因两层并存**（09-26 修的是形态层 py.exe→powershell，本日修条件层）：Set-ScheduledTask 解除电池限制 + 开启错过补跑（Action/触发器原样保留），手动触发验证 LastResult=0、09-27 备份已补（49.1MB+校验通过）。U4 验收窗口自条件修复日起重新起算（连续 3 日自然触发=09-28/29/30）。disaster-recovery.md 同步两层根因与修复命令。
- [新功能] U6 首批收口（策略共存，09-27）：①`/买入` 回复新增风险提示行——成交后单票占比（对照 mandate 单票参考上限 25%，越限标注）+ E2 转债估值分位防守区状态，不阻断（用户是执行引擎）、提示层故障静默跳过（fail-open，3 项单测含故障注入）；②compose 接线前置达成——黄金等价测试从 1 场景扩到 4 场景（全卖/WAIT/减半/REDUCE）**全部等价**（22 passed），证明接线在已测场景内行为保持，"接线或弃用"裁决的事实依据齐备（接线时点仍 = 买侧首条自动买入腿之前）。strategy-catalog 第七节与 v5.0 计划 U6 同步标注进度。
- [修复] 策略准入 promotion_cap 机器强制（策略共存治理，2026-09-27）：strategy_intake 消费裁决报告时新增推进上限门——报告内嵌 promotion_cap（如 shadow）时，超过上限的状态跃迁一律拒绝（"shadow→active 需独立证据"从文档承诺变为 fail-closed 校验；cap 值非法同样拒绝；缺省无 cap 兼容既有报告）。新增 3 项单测（cap=shadow 拦 shadow→active 不落盘 / cap=shadow 放行 registered→shadow / 非法 cap fail-closed），intake 25 passed。
- [新功能] U0 披露补齐：决策包"依据"区新增未确认规则行（"⚠ 规则未确认：R1/…——按系统默认启用中，发 /规则确认 逐条确认或停用"），达成 U0 验收"依据披露引用确认状态而非系统登记"；台账读取失败 fail-open 不阻塞决策包。新增 2 项单测（空台账披露 / 全签署不出现）。注：生产台账 09-26 已全签署（R2 停用），该行当前不出现。
- [文档] 运行时策略共存契约（`docs/strategy-catalog.md` 新增第七节）：三套策略命名空间职责表（注册表 / 运行时决策模块 / 旧 agent 技能 yaml）、唯一裁决顺序（TIER_PRIORITY）、当前生效路径（R1+现金管理层；R2 已停用）、已知语义（WAIT 不产生任何腿/减仓=清仓已修/E2 不阻断人工买入）、已建未接线清单（compose 归并、apply_budget_gate、autostop 调度、intake 报告产出）与买卖单向阀根因；计划 v5.0 状态诚实修正（P2 组合器未接线、P3 autostop 无调度调用点）并新增 **U6 单一裁决点收口**；`decision-layer-changelog` 记 09-27 治理与披露变更（无判决变更）。
- [新功能] 跨项目裁决桥生产端建成 + 全链路冒烟（09-27，冻结豁免范围内）：①`quant_research/scripts/build_intake_verdict.py`——把锁定证据（walk-forward CSV + 过拟合审计 + 快照 sha256）封装为 dsa 消费端的 `strategy_intake_verdict_v1` JSON（含 promotion_cap=shadow 声明、实盘禁令/R2 闸口径等 caveats），补齐此前"只有消费端无生产端"的结构缺口；②ETF 快照 v4（数据至 09-24）构建中**发现 v3 快照 512100.SH 复权因子污染**（close 1.17 vs 自身成交额/成交量隐含 3.12；v1/v4 一致干净；v3 从未被消费，已标记作废）；③WF 重跑被 `assert_holdout_clean` **正确拦截**（holdout 保留给 C2 最终评估，提前消费不可豁免）——豁免记录 v2 当日改写；④干跑冒烟：以既有锁定证据（data_as_of=08-08）试产出 → dsa 新鲜度门正确拒绝（滞后 34 交易日 > 5）——生产端→消费端→门全链路按设计工作，测试产物已清理。**结论：ETF 动量进 shadow 等待 11-08 冻结期满后的 holdout 正式评估，届时脚本 2 秒再生正式报告。**
- [重构] P5 高耦合批完成（补记 09-26 df20f8ee，取代同段此前"延后"判定）：物理删除 12 个通知渠道 sender 文件 + custom_webhook + 钉钉平台适配（bot/platforms/ 仅剩 feishu），`src/notification_sender/` 仅剩 feishu_sender；源码侧零悬空引用（notifications 相关常量/枚举清理），.env.example 渠道键同步清空。文档侧遗留（notifications.md 179 处已删渠道引用、bot-command.md 6 处平台残留）已登记 INDEX/待办，排入 U5 重写。
- [文档] 两项目现状全量固化（用户批评"零零散散"后终止碎片化阅读模式，两 Explore 代理全文读毕双项目）：①quant `HANDOFF.md` 置顶第 0 节现状快照——补记 09-07 后旧正文缺失的三件事（09-12 E1 评估器修正重跑、09-13 ytm_true FAIL 关线、09-13 起 8 周功能冻结+影子期至约 11-08），策略矩阵 10 行真实三态（行业动量/多因子/小市值/红利网格 quant 侧零代码），ETF 动量部署定性对账（HANDOFF:47"禁止实盘"与 dsa "shadow 候选"兼容：前 5 折全负=无稳定实盘参数禁令维持，shadow 观察不冲突；参数口径三头不一致钉死要求；快照 v3 此前漏记），数据层现状（转债 PIT 09-23 新鲜、ETF 快照止 08-08/09-01 无消费者）、跨项目契约现状（intake 桥只有消费端无生产端；E2 分位口径矛盾 quant 95.1/88.8 vs dsa 68.3 待对齐）。②dsa `INDEX.md` 修正：演进区 v4.2→v5.0、进度链更新至 U 系列、蓝图①-⑤、通知场景行以 feishu-handoff 为现行真源（notifications.md 标过时待重写）、新增"策略部署管道真源链"行（写明当前断点=intake 目录不存在+冻结期）。③本条补记 df20f8ee 删除批次（上条 e1）。已知跨项目待办登记：E2 口径对齐、intake 生产端脚本（冻结期满/豁免后）、notifications.md 重写、bot-command.md 清理、catalog↔注册表严格一一对应、.scratch 大日志清理。
- [文档] 两项目策略部署对账（用户指出重构本意=部署已选策略，catalog 与机器断线核查）：①发现注册表登记的 `etf_momentum_rotation4`（知乎四资产版）与 quant 侧实际走完验证的 `etf_momentum`（17 池版：宽基+行业+风格+避险+防御+国债 ETF 防御，60日动量/top3/MA250/周调）**是两个不同策略**——登记物≠验证物；②intake 目录 0 份裁决报告，catalog 状态机从未被真实走过。对账修正：`strategies.yaml` 新增 `etf_momentum` 条目（引用本项目验证产物 walk_forward_etf_2020_2026_locked.csv + overfit_audit DSR 0.645 + 快照 sha256，列为管道第一候选，待打包裁决报告走 registered→shadow）；rotation4 贴 `evidence_status: 未复现——不推进` 标签（intake 永不为社区版产出裁决）；strategy-catalog.md 第五节补"部署实况"注记（社区实证=外部声称值；17池版真实样本外数字：2023 两折 -9.9%/-8.7%、2025H2 +14.1%、2026H1 +37.6%；其余候选排队：多因子>行业动量>红利网格(执行形态不匹配暂缓)>小市值)。部署纪律入册：可推进条目必须引用本项目 quant 管线验证产物。注册表加载测试 19 passed。
- [新功能] U0 规则门控消费端接线（"确认/停用即时生效"从承诺变为事实）：新增 `src/services/rule_confirmation_service.py`（台账读写 + `is_rule_enabled` fail-open 查询 + STRATEGY_RULE_GATE 映射），`/规则确认` 命令改为薄壳（展示清单 + re-export 兼容）；消费点全量接线——`portfolio_compositor`（R1 停用→决策引擎拿不到止损/止盈位、R2 停用→跳过 `decide_position` 集中度分支（新增 `concentration_enabled` 参数）与 `apply_concentration_targets` 第二层修正、R3 停用→现金决策不取分位）、`strategy_engine.evaluate_strategies`（R4/R5 停用→跳过对应评估器）。fail-open 语义：台账缺失/损坏=全部启用（治理增强不得让决策引擎失聪）。用户 2026-09-26 会话委托签署：R1/R3/R4/R5 确认、R2 停用（单向阀问题，备注待买侧授权后启用）——已落台账并随 server 重启（第三次）生效。测试：rule_confirm 套件扩至 23 项（is_rule_enabled 四态 + 引擎门控 2 + 组合器门控 3 含黄金兼容），黄金基线+策略引擎+集中度回归 29 passed。
- [修复] U4 打新策略非交易日成本与过期提醒（09-26 新发现项）：`cb_new_issue` 非交易日直接跳过全量 akshare 拉网（实测周六约 64 秒纯浪费——打新提醒由后台任务独立三段式推送，决策包汇总是盘前场景）；提醒清单过滤 `reference_date < 今日` 的过期事项（实测曾出现 9 天前缴款残留）。测试同步：原 2 用例补交易日 context（新守卫下空 context=周六会被正确拦截），新增"非交易日不构造日历服务""过期过滤"2 用例，19 passed。server 已重启（PID 12408），U0 规则确认命令/U1 message_id 留存/U4 三项修复全部在线上生效。
- [新功能] U1 漏斗第一步：飞书发送侧 message_id 留存（v5.0 计划 U1，现状发送成功即丢 SDK 返回值，"建议→回应→回填"漏斗无法关联）：`feishu_sender` 新增旁路出站台账 `data/feishu_dispatch.jsonl`（追加式：at/message_id/logical_id/msg_type/content_sha16/source，线程锁串行写，**留痕失败只告警绝不影响发送**）；`logical_id` 从 `NotificationService.send_with_results` 的 dedup_key（即 NotificationEvent.logical_id）穿参至 `_app_send_raw` 成功分支提取 message_id（webhook 响应无 message_id 不记录，生产为 App Bot 模式不受影响）；无 logical_id 的消息（bot 回复等）记 content 指纹便于与 bus 对账；分片发送逐片记录。新增 8 项单测（成功/无 message_id/失败不记/穿参/分片逐片/webhook 不记/留痕失败不影响发送/路径归属），conftest 全局 autouse fixture 隔离台账防测试污染生产数据；`test_notification.py` 12 处 mock 断言同步新调用契约（logical_id 关键字），通知回归 110 passed。漏斗第二步（回应命令）与按钮升级按计划在第二波。
- [新功能] U0 意图归化文本命令版（v5.0 计划 U0，关闭 v2.0 审计#1 的入口）：新增 `/规则确认` 命令（bot/commands/rule_confirm.py，别名 规则/ruleconfirm）——`/规则确认` 列出 5 族系统规则（R1 价位锚/R2 集中度 35%/R3 E2 门/R4 打新/R5 逆回购）各自的"做什么/依据/验证状态/当前确认状态"；`/规则确认 <编号> 确认|停用 [备注]` 签署（裸编号自动归一 R 前缀，坏编号/坏动作给出可读错误）。确认台账为旁路追加式 JSONL `data/rule_confirmations.jsonl`（at/rule_id/action/note/source，同规则最后一次记录为准，坏行跳过）——不写回 strategies.yaml（保护行内注释）。按钮形态随 U1 卡片回调设施升级（计划已修订）。未确认期间行为不变（建议照常产出，决策包依据披露引用确认状态为 U0 第二步接线）。新增 14 项单测（清单/状态/动作/追加式/错误路径/坏行容错/别名注册）；命令注册回归 32 passed（24 命令无重名）。
- [修复] P0.5/U4 备份计划任务 0x800710E0 三连败修复（09-24/25/26 LastResult=2147946720）：根因为 py.exe 直调形态在计划任务上下文被拒——与健康任务 DSA_Server_Keepalive 逐字段 XML 对比仅 Action 形态不同（同 LogonType/同电池条件/同 RunLevel，排除其余因素）。新增 `scripts/backup_ledger.ps1` powershell 包装（-NoProfile -File 形态与 Keepalive 同款，输出落 `logs/backup_task.log`，透传退出码 0/2/3/1）；`schtasks /Change /TR` 单字段替换 Action（每日 08:30 触发器/InteractiveToken/电池设置原样保留）。**任务上下文实跑验证 LastResult=0**（19:09 触发：热备份 49.1MB + verify 校验通过 + db/.env/strategies.yaml/snapshot/order_sheets 五件套齐全），当日缺失备份已补上。开发中实测并规避两个 PowerShell 5.1 坑（已写入脚本注释与 disaster-recovery.md）：①无 BOM UTF-8 被按 ANSI 代码页误读，行尾中文字符可吞掉换行符使下一行并入 `#` 注释（两条赋值语句凭空消失）——包装脚本必须纯 ASCII；②EAP=Stop 下 native 命令 stderr 首行即 NativeCommandError 中断管道——native 调用段放宽为 Continue、以 $LASTEXITCODE 为准。验收闭环=连续 3 日自然触发成功（观察点 09-27/28/29 08:30，本次仅完成首日）。disaster-recovery.md 注册命令同步为包装形态。
- [chore] server 重启使非交易日门修复（b9ef600c）生效：终止旧实例（PID 17600，09-25 11:42 启动）→ 以 Keepalive 同形态拉起（新 PID 11128）；验证端口 8000 监听、/docs 200、`feishu_stream_alive=true`（bot 命令通道重连正常）。周一 09-28 开市起策略引擎路径的非交易日门全量生效。
- [文档] v5.0 计划执行前调研修订（GitHub/官方 SDK 调研 + 代码核实后定稿执行形态）：**U0 交付形态定为文本命令**（`/规则确认 <编号> 确认|停用`）——lark-oapi 1.7.1 实测虽已支持长连接卡片按钮回调（Builder 含 register_p2_card_action_trigger，无需公网/无需升级依赖），但需飞书后台手工订阅 card.action.trigger、漏配即完全静默（卡片能发、点击无反应、无日志）且代码无法自证配置到位；按钮形态随 U1 的回调基础设施一并升级，不为 U0 单独建一次。**确认台账改为旁路 JSONL**（data/rule_confirmations.jsonl 追加式，不重写 strategies.yaml——现有 yaml 写回会抹掉全部行内注释）。**U1 漏斗砍除"已读"层**（SDK 支持 reaction/已读事件但需新增 scope+订阅，成本最高信息量最低），漏斗=送达→回应→回填；第一步改为发送侧 message_id 留存（现状发送成功即丢返回值，漏斗无法关联的根因）；策略类建议（逆回购/打新）不进操作单、漏斗止步"回应"层写为诚实边界。**U2 计数源改为实例 ID**（logical_id 是刷新替换语义不能直接计数；噪声冷却 process-local 重启即丢也不能用；静默复用 set_registry_status 幂等状态机）。**U4 日历澄清**：exchange_calendars XSHG 数据无过期问题（实测覆盖至 2026-12-31，9/25-9/27 中秋、10/1-10/7 国庆逐日与交易所公告吻合），此前"09-25 周五 0 条异常"判断有误——那天是中秋假期，0 条正是守卫正确行为；2026 年末前刷新日历数据包记为低优先级维护项。顺序改为两波：第一波（U4 提交+重启+备份修复 → U0 文本命令 → U1 message_id）→ 第二波（U1 漏斗+U2 休眠+按钮形态）。
- [文档] v5.0 计划实测微调（用户要求"实际跑项目看现状再微调"）：09-26 实测复核四项——① server 健康（uptime 28.9h，keepalive 实证有效、stream 存活）；② P4 非交易日门在**管线层实证生效**（周六 09:27 管线跑到 calendar_gate 正确跳过），此前漏的只是 strategy_engine 路径（修复已单测锁定，待提交+重启）；③ 备份任务**三连败定案**（09-24/25/26，0x800710E0=ERROR_REQUEST_REFUSED，py.exe 直调形态被拒），U4 修复路径明确为改 Keepalive 同款 powershell 包装；④ **新发现**：cb_new_issue 周六仍全量 akshare 拉网约 64 秒（非交易日网络成本）且可能残留过期缴款提醒——三项（跳过拉网/上市卖出提醒非交易日不产出/缴款窗口过期检查）并入 U4 验收。计划"执行现状/关键旁证/U4"三节据此更新。
- [文档] 策略中心重构计划 **v5.0——使用中心计划**（基于 2026-09-26 磁盘核实）：核心论点为**建设已超前于使用**——P1/P1.5/P2/P3◐/P4/P4.5/P5◐ 已提交落地、09-24 操作单证明建议质量达标（减半观察+保底一手非清仓），但真实账本最新成交停在 09-07、**零执行持续 19 天**；三轮审视（v4.1 十四条/v4.2 五条/本次核实）收敛同一结论，再给"没人执行的建议"加功能是重复犯错。更深层根因：系统每日执行的规则（结构锚价位/35% 集中度/E2 门）无一条经用户确认（v2.0 审计#1 至今未闭环）——人不执行自己没同意过的纪律。新阶段 U 系列：**U4** 耐久性修复（备份任务已注册但每日失败 0x800710E0、周六逆回购建议漏非交易日门——立即）；**U0 意图归化**（飞书卡片逐条确认规则：确认/修改/停用，strategies.yaml 记 confirmed_at，关闭审计#1）；**U1 执行漏斗仪表**（送达→已读→回应→回填四环可追溯）；**U2 建议休眠**（N 次无回应→静默+一次性确认询问，根治"你又说了又说"）；**U3** 依赖后台线（ETF 等 quant 裁决报告、打新熔断等破发数据源）；**U5** 文件治理（本计划文件按"单一版本+沿革指针"重写，v4.x 四层版本头压缩归 CHANGELOG）。含诚实条款：若用户确认后停用多数规则，"今日无待办"是正确输出而非失败。
- [修复] 非交易日逆回购建议（U4，实测 2026-09-26 周六策略引擎仍建议"今日操作 GC001"）：P4 非交易日门此前只覆盖操作单路径，漏了 `strategy_engine.py` 的 `reverse_repo_window`——补"今日必须可交易"前置门（非交易日直接无信号），新增单测 `test_non_trading_day_no_signal`（周六+高现金→无信号），17 passed。
- [文档] 文件治理（v5.0 U5 首批）：`EXECUTION-BLUEPRINT` 从属标注更新到 v5.0；`account-decision-rules.md` 头部两条叠更正注合并为一条（09-19 与 09-26 更正合并），引用同步 v5.0；计划文件本身从"v4.x 四层版本头+批评处置表叠加"重写为单一版本+沿革指针。
- [重构] P5 模块增删收敛·低风险批（v4.2 计划 P5，第四节删除总表，批评#11）：物理删除两个"未注册使用/与 A 股主线无关"的低耦合模块，为后续高耦合批（13 个 notification sender + dingtalk）验证删减纪律。①**删 bot 平台 discord**——`bot/platforms/discord.py` 从未在 `ALL_PLATFORMS`（仅 dingtalk）注册、唯一引用是自身测试，连同 `tests/test_discord_platform.py` 一并删除；`bot/platforms/__init__.py` 无需改动（本就未导入）。②**删 `src/brokers/futu` 券商持仓导入**——港股券商持仓导入（`FutuPortfolioError`/`load_futu_stock_codes`）与 A 股账户主线无关，物理删除整个 `src/brokers/` 目录；`main.py` 清理：移除 import、`_resolve_portfolio_stock_codes` 改为非空 portfolio 一律 `raise ValueError`（保留入口兼容仍传 portfolio 的 API 调用方，不再静默支持任何券商）、移除 futu 空持仓短路/skip_futu_stock_analysis 分支/futu 相关日志文案、清理 `--portfolio` 覆盖提示与 runtime_scheduler 透传；`runtime_scheduler.py` 从 `SCHEDULE_ARGS_OVERRIDE_KEYS` 与默认 args 移除 portfolio。**边界保留**（不同模块）：`data_provider/futu_fetcher.py` 数据源与 `futu-api` PyPI 依赖（供 fetcher 用）保留，CI 的 `import futu` 探针与 desktop 打包 `--collect-all futu` 均不变——CI `futu_packaging` 过滤器只移除已删的 `src/brokers/futu/**` 路径条目。测试同步：重写 `test_main_portfolio.py`（改为断言非空 portfolio 抛 ValueError、runtime_scheduler 不再携带 portfolio 覆盖）、`test_main_schedule_mode.py`（futu 加载失败/成功用例替换为"不支持 portfolio 返回非零"、serve 模式吞 ValueError 继续运行）、`test_futu_distribution_contract.py`（移除 `src/brokers/futu/**` 断言，其余分发契约保留）。flake8 关键检查零告警，main/schedule/portfolio/CI 契约/分发契约相关回归 74+4 passed。**P5 部分完成**——低风险两模块已零残留删除（全仓 grep 仅剩计划表历史条目）；13 个 notification sender + dingtalk 平台因与 NotificationService 多继承高耦合延后。

- [新功能] P3 策略准入裁决闭环（v4.2 计划 P3 dsa 侧，批评#4/#6/#14）：把"策略从研究到实盘授权"的准入做成可审计的文件契约管道 + 运行态降级打通，跨项目只走文件不 import 代码。①**strategy_intake.py 准入管道**——裁决报告 schema（`strategy_intake_verdict_v1`，必填 schema_version/strategy_id/pit_end/data_as_of/generated_at/oos_window/metrics/verdict）+ fail-closed 校验（结构问题一律拒绝推进）；**数据新鲜度门**（批评#4）：verdict=promote 先过 data_as_of 滞后门（超阈值交易日则拒绝并告警，不静默用过期数据裁决，E2 分位滞后两天正是此门要拦的场景），再按状态机推进一档（registered→shadow→active，不允许跳级）；verdict=reject→rejected、hold→保持；`process_intake_dir` 批量消费目录、单份失败不拖垮其余；apply=True 才落盘 strategies.yaml（原子替换）。②**挂每日管线**——daily_pipeline 3.45 步消费 quant 侧 `quant_research/output/strategy_intake/` 裁决报告并推进注册表状态、写状态文件；失败不阻塞主决策。③**DecisionSignal 加 strategy source_type**（decision_signal_service + api schema Literal 同步）——策略建议可落库为 source_type=strategy。④**autostop 挂线 + 跑输幅度门 + 注册表打通**（批评#14）——跑输判定从"严格小于基准"改为"跑输超过 margin_pct"（新增 STRATEGY_AUTOSTOP_UNDERPERFORM_MARGIN_PCT，默认 1%，避免跑输 0.01% 也累计触发降级）；触发暂停时把注册表对应策略同步为 dormant（此前 autostop 用独立表、与 strategies.yaml 脱节，两套状态打通）。⑤**命中率口径诚实标注**（批评#14）——outcome 聚合的 hit_rate_pct 显式标 `hit_rate_is_absolute=True` + caveat，声明是绝对方向命中率非相对基准 alpha，策略真实能力须结合影子跟踪误差判读（买入执行率在零执行期是虚荣指标）。新增 22 项测试（报告校验 5 + 新鲜度门 3 + 状态机 6 + set_registry_status 3 + 批处理 2 + autostop 幅度门 3），outcome/signal 回归 100 passed，flake8 关键检查零告警；.env.example 同步新配置项。**P3 部分完成**——dsa 侧准入管道/新鲜度门/状态机/autostop 打通全部落地；**未做**：打新破发率熔断（需外部破发数据源）、ETF 四资产全程（需 quant 侧产出裁决报告）——依赖 quant 侧产物，非 dsa 侧可独立完成。
- [新功能] P4.5 决策包归档契约（v4.2 计划 P4.5，批评#10——归档无人认领）：把 `/操作单 历史` 从"只回放订单"升级为"回放系统当天为什么这么判"的完整判断，并让 content_hash 从文案变成真正的校验契约。①**归档当时的原判而非事后重算**（核心修复）——`build_snapshot()` 新增 `decision/rendered_text/portfolio_snapshot/order_sheet/execution_mode` 入参，`refresh(**kwargs)` 透传；`_build_sheet_text` 三个落单分支（新单 generated / 沿用单 reused / 暂停 halted）均把**当时生成**的 AccountDecision 与渲染原文传入归档，根治"盘中价格/规则变化后重算的归档与用户实际收到的指令不一致"；仅现金/恢复交易/管线等无参入口降级重算（标 `recomputed`）。`_capture_decision` 完整快照每票判决（verdict/target/reason/rule_state/ai_note/**破位诊断 breach**）+ 现金/E2/集中度阈值 + 观察池 + 策略信号。②**content_hash 成为校验契约**——从 16 位截断升级为全长 64 位 sha256（截断指纹抗碰撞裕度不足）；新增 `verify_snapshot()` 读回重算比对（hmac.compare_digest 常量时间），`/操作单 历史` 对每份归档校验、篡改/损坏标 ⚠；旧档兼容（缺 hash 返回 True 不误报、16 位旧档按前缀比对）。③**历史命令完整回放 + 加固**——展示判决/破位/现金结论/execution_mode 标签，日期参数经 `normalize_archive_day` ISO 校验（拒绝 `../` 路径穿越），非 dict 归档结构防御，旧档无 account_decision 时降级只显订单不崩。④**落盘加固**——latest.json 与 archive 均改唯一 tmp 名 + os.replace 原子替换，写操作加 `_SNAPSHOT_WRITE_LOCK` 串行化（多刷新入口并发安全），归档同样原子写并清理残留 tmp；快照加 `schema_version=2`。新增 18 项测试（normalize 4 + capture 3 + hash 校验 6 + 历史回放 5 含旧档兼容/篡改可见/路径穿越拒绝 + 同日原子覆盖仍每日一档）；decision_snapshot/审计/CB 全链路回归 52 passed，flake8 关键检查零告警。**P4.5 完成**——任取一天可回放"系统当天为什么这么判"且 hash 可校验。
- [新功能] P4 通知事件化 + 非交易日门 + 两套话术消除 + ai_note 新鲜度（v4.2 计划 P4，批评#8/#9）：①**统一出站事件契约**（`src/services/notification_events.py`）——`NotificationEvent`（logical_id/route_type/priority/symbols/time_sensitive/bypass_digest/payload）+ `to_send_kwargs()` 映射到既有 send_with_results 参数，与既有 logical_id 体系（daily_operation_sheet/event_alert）对齐；发送方产事件、渠道消费的契约就位（渐进迁移，不强制一次性重写所有 sender）。②**非交易日门**（批评#9）——`is_trading_day`（复用 trading_calendar，fail-open）+ `time_sensitive_suppressed_note`；决策包渲染在非交易日顶部加"今日非交易日"说明并**抑制挂单边界**（开盘放弃/14:50 撤单只在交易日有意义），回填命令仍保留（成交后才发、不带时效）。③**两套话术消除**（批评#8）——告警文案从"结合决策包人工确认/不自动下单"改为"不给操作口径，发 /操作单 看今日待办（同一事件的卖出/减仓由决策包统一裁决）"，告警与决策包不再各说一套。④**ai_note 新鲜度**（P4-5）——`_latest_ai_notes` 对超过 days 窗口的分析加 `[陈旧·N天前]` 标记，AI 解释不再冒充当前观点（治"旧报告一句话与当日决策矛盾"）。新增 11 项测试（事件契约 2 + 非交易日门 3 + 决策包门 2 + ai_note 新鲜度 2 + 交易日钉死修复既有渲染断言）；相关回归 91 passed（含 alert_worker/golden/service）。黄金基线通过交易日 monkeypatch 保持边界行断言稳定。
- [重构] P2 后半：position_sizer 预算闸门 + 组合器参数进 P0.9 纪律 + 买侧授权配置（v4.2 计划 P2 收尾，均为 A 类——无当日判决变更）：①**position_sizer 闸门**（`portfolio_compositor.apply_budget_gate`）——把此前零接线的三层仓位管理（`build_position_sizer_from_config`）接为**买入**预算闸门：单票上限（max_position_pct 25%）+ 总敞口上限（max_total_exposure_pct 95%，扣除已持仓市值算余量）双重裁剪、整手取整；卖出/减仓腿不受约束（持有多少都能卖）。当前买侧只读（观察池不落单），真实账户无买入腿 → 闸门空转，不改判决；买侧授权后自动生效。②**组合器参数进 P0.9 回放纪律**（decision-layer-changelog）——补齐此前缺席的第三层：优先级链/容忍带/预算闸门参数登记入三层参数清单，并记"无判决变更"对照（compose 为新增函数未切主链、闸门只约束买入而买侧只读），敏感性说明三者均为结构值无网格调参空间。③**买侧授权配置**（strategies.yaml `buy_side` 段 + `strategy_engine.load_buy_side`）——intent=observe（默认只读）/signal（授权落单）+ universe 标的池；读取失败 fail-closed 到 observe（买侧永不因配置故障意外放开）。这是 v4.2"买侧不无限期悬空、但授权是增强非前提"的落点：不填=正式交付形态（观察池只读+现金管理兜底），填了=激活。新增 9 项测试（闸门四态：卖出不裁剪/单票上限/总敞口/无买入腿空转；买侧授权：默认 observe/缺文件 fail-closed；契约与组合器沿用前半 15 项）；P2 全量回归 45+36 passed，黄金基线零改动。**P2 完成**——StrategyOutput 同构 + 注册表合一 + 组合器归并 + 闸门 + 买侧授权全部落地。
- [重构] P2 前半：策略层同构化清偿 + 组合器 compose()（v4.2 计划 P2 前置子项——P1 "同构化名不副实"欠账的完整清偿）：①**StrategyOutput 统一契约**（`strategies/types.py`）——目标持仓腿 TargetLeg（symbol/target_quantity/weight_pct/constraint）+ StrategyOutput（tier/cadence/legs/cash_action/reason/constraint_notes/meta），TIER_PRIORITY 显式优先级链（exit_rule 10 > risk_control 20 > cash_management 30 > low_risk 35 > satellite 40，社区共识：离场>风控>现金管理>配置型）；P1 三模块异构输出（PositionDecision/原地改 list/Tuple）与 strategy_engine 纯展示 StrategySignal 从此有统一内部契约，StrategySignal 降为渲染投影。②**适配层**（`strategies/adapters.py`，A 类零破坏——不改任何既有函数签名，黄金基线与 monkeypatch 目标不动）：user_rules_output（只抽 SELL 腿，WAIT/HOLD 不产腿）/concentration_output（REDUCE 腿，P1.5 保底一手语义进 constraint）/e2_timing_output（防守 cash_action）/signal_to_output（现金策略操作进 cash_action 不产持仓腿）/decisions_to_outputs（编排产物一键组装全套输出）。③**注册表合一**：strategies.yaml 新增 3 个系统策略条目（user_rules/concentration/e2_timing，system: true 标记 + params_source 指明参数真源——身份登记不夺参数真源）；strategy_engine.evaluate 跳过系统条目（重上下文策略由 compositor 编排内适配，不走轻上下文评估），新增 evaluate_strategy_outputs 统一契约出口。④**组合器 compose()**（`portfolio_compositor.py`，A 类落地——新函数不切现有编排，黄金等价性测试守护）：优先级归并（同 symbol 冲突取高优先级并记 conflicts）/容忍带（目标==当前剔除、差额不足一手记 note 保留披露，与 order_sheet 既有口径一致）/cadence 排他（再平衡优先跳过日频微调，当前全 daily 预留）。新增 15 项测试（契约/优先级链/适配器四态/注册表合一/compose 冲突-容忍带-cadence/黄金场景等价性——adapters→compose 归并目标与现有编排路径订单一致）；P2 相关回归 59 passed。
- [新功能] P0.5-fix 备份计划任务注册 + P1.5 集中度变更用户确认收官：①`DSA_Ledger_Backup` 计划任务已注册（每日 08:30，`backup_ledger.py --verify --retention 14`，py.exe 绝对路径防 PATH 差异），今日备份已产出并校验通过（43.8MB 热备份 + 核心表齐全）；**首个运行态证据观察点=明日 08:30 自然触发**（v4.2 运行态条款：连续 ≥3 日有当日新备份才算 P0.5 达标；今日手动 /run 触发曾遇调度器 Queued 卡住，已 /end 清回 Ready，若明早未跑则改 Keepalive 同款 powershell 包装形态）。②decision-layer-changelog 集中度 B 类变更人工确认为"已采纳"（用户 2026-09-23 过目真实账户只读渲染后裁决）——P1.5 三项验收全部关闭（①集中度不机械清仓✅②paper 闭环替代验收✅③驱动规则标注✅）。③依据行截断 96→110 字符（"稀释"一词曾被截掉一个字）。
- [新功能] P1.5 替代验收通过 + 回填链两处 symbol 形态缺陷修复（v4.2 P1.5 边界条款：真实成交需用户券商下单，接受隔离 paper 账户完整闭环作为替代验收）：①新增 `scripts/acceptance_p15_paper_loop.py` 可复跑验收脚本——隔离环境（DATABASE_PATH/SHEETS_DIR/SimulationLedger 全指 tmp，真实 .env 价位规则只读加载）跑通全闭环：paper 账户登记（入金 ¥14,000 + 买入 000899/002475，成本与真实账户一致）→ 决策包（断言 P1.5 新集中度算法：002475 保底一手非清仓）→ 操作单 → 模拟账本 → `/成交回填` 一笔（002475 卖 100 @54.50，偏离限价 -0.62%）→ 真实账本记账（fee=7.83 estimated）→ 操作单标单（100/100）→ `/对比` 产出非空真实偏差（模拟 54.84 vs 真实 54.50，-0.62%）。②**验收暴露两个真实生产缺陷并修复**——(a) `fill._do_fill` 腿匹配缺陷：回填侧统一 canonical 前缀形态（SZ002475）与操作单腿的存储形态（股票决策链=裸码 002475）精确匹配永不命中——**股票操作单的回填标单自 09-15 上线以来从未生效**（转债链恰好前缀形态兼容所以未暴露），修复为 canonical 归一匹配 + 标单/fills 用腿原始形态；(b) `SimulationLedger.compare_with_real` 配对缺陷：匹配键只 upper() 未真正归一（docstring 声称的"归一形态"名不副实），股票腿在 /对比 里永远显示"未回填"，修复为两侧经 canonical_symbol 归一配对。新增 2 项回归测试（裸码腿回填标单/裸码-前缀配对），fill+simulation 15 passed。
- [新功能] P1.5 建议可执行性与首笔引导（v4.2 计划 P1.5 前半，发现#4/#5/#6）：①**账户规模感知集中度**——阈值内预算不足一手时旧逻辑"减仓到阈值内"数学上等于清仓（实测 002475：35% 预算 ¥4,711 < 一手 ¥5,495 → 连续输出"卖出全部 200 股"，操作单 09-15~09-23 连续 9 天 pending/fills=0，不可执行建议很可能是零执行成因），`strategies/concentration.py` 修正为：多手持仓 → 保底一手分批降低（target=1 手）并披露"卖出后仍超阈值"；仅持一手 → 持有 + "账户过小需加仓/入金稀释"提示；预算充足场景与止损/止盈路径逐字节不变（黄金基线零改动通过）。真实账户只读验证：002475 判决从"卖 200"变"卖 100 留 100"，依据行讲清"为什么不是全卖"。B 类语义变更已按 P0.9 纪律留痕（docs/decision-layer-changelog.md 含变更前后对照表，待人工确认）。②**动作卡驱动规则标签**（发现#6）——决策包动作卡依据行加 `[止损规则]/[止盈规则]/[集中度规则]` 前缀，"止损减半与集中度清仓交替主导导致卖出量 200→100→200 跳变"的机制切换从此可见；依据行截断 52→110 字符（"保底一手"等关键披露不得被截掉）。③**首笔引导**（发现#4）——账户无任何真实成交记录时，决策包尾部提示"如何完成第一笔回填"（复制卡片命令→记账→/对比 开始量化偏差），查询失败静默降级。新增 7 项单测（保底一手/仅一手持有/真实账户回归钉死/驱动标签渲染/首笔引导三态），更新 1 项旧断言（test_concentration_reduce_target_lots 固化的正是本次修正的"清仓"缺陷）；相关子集 36+19 passed。
- [文档] 交接与导航文档规范修订（用户指出交接单"写得不好、有些东西不靠谱"并要求规范全部文档）：①`.scratch/dsa-handoff-20260923.md`——删除第 0 节"通道探针"（静态文件两次 md5 对比探不出任何真实故障、且为 bash 语法在本机 PowerShell 跑不通；替换为行为规则：工具失败停手报告、不重试循环不换会话碰运气），全部核实命令 PowerShell 化，"P1 完成"补 A 类限定（同构欠账 StrategyOutput/注册表合一显式移 P2），补价位规则现值与 P0.9 关键发现（价位锚 window 才是卖出开关）；②`docs/INDEX.md`——演进区从 v4.1 更新至 v4.2（P1.5 首笔执行闭环前置 P2/运行态证据条款/当前进度链 P0✅→P0.5⚠→P0.9◐→P1✅→下一步 P1.5），补从属链说明，新增"运维与可靠性"分区收纳 disaster-recovery 与 decision-layer-changelog（此前仅在场景表、无分区归属），account-decision-rules 与 strategy-catalog 移出"配置"区消除重复归类（归演进区单一归属）；③`docs/EXECUTION-BLUEPRINT.md`——头部效力声明按 v4.2 第八节限定为"已交付形态（①-④）事实说明、⑤及以后规划权归重构计划"，修正头部与正文矛盾（头部曾标 ⑤⏳ 待启动、正文实为 ✅ 09-07 完成），版本 v1.1→v1.2；④`docs/account-decision-rules.md` 头部规划真源引用 v4.1→v4.2。INDEX.md 全量 40 个本地链接扫描零死链；INDEX_EN.md 无演进/运维区对应内容，本次无需同步。
- [文档] 策略中心重构计划 v4.2（基于对 P0–P1 执行现实的磁盘核实，吸收 5 项发现，核心是把验收从"系统说得对不对"扩到"建议有没有被执行"）：①**新增 P1.5 首笔执行闭环**前置于 P2——实测操作单连续 9 天 `pending`/`fills=0`、真实账本最新成交停在 09-07，P0–P5 无一阶段管"建议是否被执行"，零执行下 P2 买侧/P3 命中率/P0.9 回放价值均无法验证；②**"减仓=清仓"不可执行建议**（发现#5 深化）——集中度规则对当前 2 股小账户产出"卖出全部 002475"（35% 预算 ¥4,711 < 一手 ¥5,495 → 目标 0），P1.5 加"账户规模感知集中度（保底一手/分批）"为验收项；③**P1"同构化"名不副实**——`StrategyOutput` 目标持仓契约全仓不存在、且存在两套平行策略概念（`strategy_engine`/`strategies.yaml` 纯展示不产订单 vs `strategies/` 驱动订单但无注册表），"新策略零改主干接入"下移为 P2 显式验收项（前置：定义 StrategyOutput + 注册表合一）；④**P0.5 备份为一次性演练非常态**——脚本与还原演练为真但定时任务从未注册、备份停在 09-21，根因是验收标准弱于正文，v4.2 给所有"定时/无人值守"阶段加"运行态证据"硬条款；⑤**P0.9 只覆盖两层**——破位分档+价位锚已建，组合器参数层随 P2 补齐。同步更新三件套表（插 P1.5、修 P0.5◐/P0.9◐/P1✅ 状态、加运行态证据条款）、顺序约束、模块增删表。
- [重构] 账户决策引擎策略同构 P1 收尾（v4.1 计划 P1——`account_decision_service.py` 编排体抽离，门面仅剩契约 re-export + 渲染 + helper）：①`AccountDecision` 数据类下沉 `strategies/types.py`（与 `PositionDecision` 同处，契约类零外部依赖），门面 re-export 保持兼容；②新建 `portfolio_compositor.py`（L2 组合层）承载 `compose_account_decision` 编排：拆持仓→逐票规则决策→集中度目标修正→现金/E2→买入观察池+策略引擎信号→组装 `AccountDecision`；③`account_decision_service.build_account_decision` 改为薄委派。为保持既有约 11 处测试 monkeypatch 目标（`ads.compute_e2_percentile`/`load_price_rules`/`_latest_ai_notes`/`build_account_decision`）**零改动**，compositor 经门面命名空间惰性调用这三个 helper（避免循环导入 + 保 patch 语义）。**行为不变（A 类·黄金基线逐字节）**：golden 3 + service + concentration 4 + audit 3 = 29 passed，无一处测试文件改动。冲突裁决优先序与买侧目标持仓语义属 P2，届时在 compositor 落地。
- [重构] 账户决策引擎策略同构 P1 step3/4（v4.1 计划 P1——`account_decision_service.py` 逐步拆为 `strategies/*` + 门面）：①集中度减仓目标修正抽到 `strategies/concentration.py`（`apply_concentration_targets` 纯函数：REDUCE 决策按组合总市值近似出阈值内可持有量、整手取整，减仓不足一手退化为持有）；②E2 择时抽到 `strategies/e2_timing.py`（`compute_e2_percentile` + `E2_DEFENSIVE_THRESHOLD` + `decide_cash_verdict` 现金决策映射），门面 re-export 保持 `ads.*` 命名空间兼容（测试 monkeypatch 目标不变）。**行为不变（A 类·黄金基线逐字节一致）**：`test_account_decision_golden` 3 项 + `test_account_decision_service` + 新增 `test_concentration_strategy` 4 项共 26 passed；E2 抽出后实测分位与抽出前逐位一致（0.9080213903743316/2026-09-18，验证 `parents[4]` PIT 路径校正正确——该路径无测试覆盖，靠运行时比对守住）。v4.1 中 E2 降级为卫星预算系数属 B 类语义变更，不在本步。
- [修复] 测试隔离：`test_cb_signal_bridge` 的 `OrderSheetCommand` 全链路测试会 `refresh()` 决策快照，此前未隔离 `SNAPSHOT_DIR`，跑一次即覆写真实 `output/decision_snapshot/latest.json`（Web 横幅/托盘/状态端点数据源）。新增 `isolated_snapshot_dir` fixture 把 `SNAPSHOT_DIR` 指向 tmp。同时修正两处陈旧断言（决策包渲染早前重写为"今日待办"卡片式，断言仍查已移除的"账户决策包" header）。
- [修复] 成交回填方向护栏（同一 symbol 在操作单中同时存在买卖双腿时，`mark_order_filled` 原按 symbol 取第一条腿，可能错腿回填污染单据状态）：`mark_order_filled` 新增 `direction` 参数按方向定位目标腿，同 symbol 多方向且未指定 direction 时抛 `ValueError` 拒绝执行（不再静默取第一条）；`/成交回填` 命令传入 `direction=side`。单腿单不指定 direction 保持原逻辑向后兼容。新增 3 项回归测试（双腿未指定拒绝/双腿指定命中正确腿/单腿兼容）。
- [修复] 交易日历周末门（`infer_market_phase` 在 xcals 日历库缺失/异常时把周末误报为 UNKNOWN）：周六/周日的确定性判定前移至 `_XCALS_AVAILABLE` 依赖之前，日历库不可用时周末仍恒定返回 NON_TRADING。新增 1 项回归测试（无日历库时周末返回非交易日）。
- [修复] 每日管线非交易日门（周末/节假日 09:00 仍跑全管线并推送带 T+1 时效的操作单——无新截面无新价，产出只能是重复单或陈旧单）：`run_daily_pipeline` 在幂等检查后、执行前新增交易日历门，`infer_market_phase("cn")` 判非交易日则跳过并写状态；`force=True` 手动补跑可越过，日历不可用时 fail-open 维持既有行为。
- [修复] 决策快照持仓价格陈旧告警（快照携带持仓 price_date 但无陈旧检测——价格源断流回退旧收盘价、新买入尚无 PIT 价时，操作单会按旧价生成而无提示）：`build_snapshot` 对持仓估值价与信号数据日（pit_end）比对，价格缺失或日期落后于 pit_end 的持仓计入 `quality.warnings`。
- [新功能] 在线决策层回放基线（v4.1 计划 P0.9——把"每天对用户输出指令却免检"的三层纳入与策略同级的验证纪律）：①破位分档阈值从硬编码提为 `BreachThresholds`（deep_pct/shallow_pct/persist_days/volume_spike/volume_shrink/fresh_days/wait_max_consecutive，默认值=原硬编码值，`diagnose_breach(thresholds=None)` 行为逐字节不变，19 项单测通过）——阈值可注入扰动；②`scripts/replay_decision_layer.py`——逐评估日回放历史判决 + 参数扰动敏感性（只读本地日线、不联网、无副作用）；③`docs/decision-layer-changelog.md`——三层参数清单 + 基线回放 + 变更留痕规范（今后改参数必附回放 diff，否则视为静默调参）。**基线关键发现**：破位分档阈值在真实数据上几乎不改判决（60 评估日各种扰动恒为 hold×59+half×1）——结构锚价位下历史破位本就极稀，分档是过度设计；真正决定卖出触发的是价位锚 window 参数（10日破位3次/20日1次/30日0次），它才是该验证的对象。印证外部审视"复合无回放"风险点、且定位到风险在价位锚而非分档。
- [新功能] 账本备份与单机 DR（v4.1 计划 P0.5 耐久性前置——补齐"账户真源零备份"这一致命缺陷）：①`scripts/backup_ledger.py`——SQLite 在线 backup API 热备份 `stock_analysis.db`（运行中也一致、不锁库）+ `.env` + `strategies.yaml` + `output/decision_snapshot/` + `output/order_sheets/` 到 `data/backups/<日期>/`（含 MANIFEST，`data/` 已 gitignore 不入库），每日一份滚动保留 14 份，`--verify` 校验备份 db 可打开且 5 张核心 portfolio 表齐全；②`docs/disaster-recovery.md`——备份对象/Windows 计划任务注册命令/三类崩溃场景（账本损坏、配置丢失、整机迁移）的还原步骤/边界（只做本机备份不外传、跨项目契约与 PIT 缓存可重建不备份）；③实跑验收：备份 41MB 库 + 临时副本还原演练（账户 2/交易 5/现金流水 2 全部可读）通过。
- [文档] 策略中心重构计划 v4.1（吸收对 v4.0 计划本身的 14 条外部审阅，修正"验证纪律覆盖反了"这一系统性盲区）：①新增 **P0.5 耐久性前置**（账本 8 表+.env+strategies.yaml 定时备份、每阶段 pre-<阶段> 还原 tag、单机 DR 步骤）——重组 2 万行前先有还原点；②新增 **P0.9 在线决策层回放基线**——把结构锚价位/破位分档/组合器参数这三层"每天真正对用户说话却免检"的逻辑纳入与策略同级的回放集+参数扰动敏感性表+decision-layer-changelog 留痕（000899 判决翻转即此盲区实证）；③**P1 拆两类基线**解矛盾——A 类行为不变部分逐字节、B 类语义变更部分走前后对照+人工确认；④**买侧不再无限期悬空**——给默认推进路径（观察池只读+现金管理兜底）、授权交互（strategies.yaml 的 buy_universe/buy_intent）、搁置时限（P5 前未授权则以兜底形态正式交付）；⑤**裁决报告契约**——strategy_intake 消费的 quant 回测 JSON 定 schema + 数据新鲜度门（data_as_of 滞后超阈值拒绝 promote 并告警，拦 E2 已滞后两天这类场景）；⑥**免验证策略熔断**——逆回购/打新虽免回测 active 但挂降级触发器；⑦**非交易日门**并入前移的 P4（周日不发带 T+1 时效的挂单指令）；⑧**P4.5 决策包归档契约**——破位诊断/判决/依据脚注一并进 content_hash 防篡改归档；⑨阶段序调整 P0.5→P0.9→P1→P2→P4→P4.5→P3→P5；⑩成功指标改为带对照（命中率相对基准）、代码量口径改为"活跃代码量"（删除+冻结移出主 CI）；⑪第八节理清"三个唯一真源"从属：重构计划=规划真源、蓝图=已交付形态说明、策略目录=策略面真源。同步更新 INDEX 演进表版本标注（v4.0→v4.1）与 account-decision-rules 价位过时更正引导。
- [文档] docs 全量职责体检与治理（用户要求"确定每个文档职责，过时/无用/重复的删除，其余更新现状"）：对 55 个 md 逐个核对职责与代码现状（search agent 交叉验证 src/ 实现）。①**删除**：`bot/dingding-bot-config.md`、`bot/discord-bot-config.md`（钉钉/Discord 属计划砍除的 13 渠道；对应 sender 代码深嵌 20+ 文件，留待 P5 随代码同步下线，本次仅删纯文档）。②**加 ARCHIVED/历史标注**（只标不删，保留知识可追溯）：run-diagnostics-p0/p1/p2/p3（#1391 交付记录，功能已落地）、quant_research_architecture_research（描述外部独立项目，本仓库无对应实现）、ASSISTANT-GAP-AUDIT-20260910（时点审计）、CLOSURE-FIX-PLAN（诉求已处置）、DESKTOP-OVERHAUL-PLAN（桌面端已冻结）。③**加冻结标注**（内容仍准确、对应模块将冻结不删）：multi-strategy-contract、screening-engine、agent-stream-events。④**修死链**：INDEX.md/INDEX_EN.md/openclaw-skill-integration.md 中指向不存在的 `grok-bot-integration.md` 的 4 处本地链接全部移除，INDEX Bot 接入行改为"（飞书）"。⑤全库死链复扫确认零残留。稳定文档（full-guide/DEPLOY/FAQ/LLM_CONFIG/notifications 等约 30 个）无需改动，权威契约文档（analysis-context-pack/decision-signals/intelligence-sources/image-extract-prompt）保留。
- [文档] 策略中心重构计划 v4.0（用户授权"审阅整个项目，可删可增模块"——基于全项目代码实测盘点重写，纠正 v3.0 事实错误）：**v3.0 把地基方向搞反了**——它声称 quant_research 有 event-sourced 的 AccountRepository、dsa 该降级为读侧投影；实测证伪：quant 全仓无 AccountRepository/append-only/payload_hash，其 trade/ 只有内存态 PaperBroker(回测用)+executor+sizer、无 account.py。真正的持久化账户真源在 dsa（8 张 portfolio_* 表 + payload_hash 防篡改 + portfolio_event_audit append-only）。v4.0 方向：dsa=账户真源+决策+交互（保留强化），quant=回测裁决所（策略准入唯一权威，只走文件契约，遵 AGENTS.md 两项目边界）。含实测行数/引用盘点（system_config 5563、screening 3893/275 引用冻结不删、agent 21534 例行冻结、14 sender 砍 13）、模块增删总表（删：13 sender+dingtalk/discord 平台+brokers/futu；冻结：screening/agent 例行/桌面端；收缩：system_config/run_diagnostics；新增：strategies/ 目录+portfolio_compositor+strategy_intake+notification_events）、阶段序 P0✅→P1 策略同构→P2 组合器+买侧→P3 quant 裁决闭环→P4 通知/LLM→P5 增删收敛，每阶段强制验收/指标/回滚三件套。v1.1/v2.0/v3.0 计划作废。
- [改进] 决策包渲染整体重写为"待办动作卡片"式（用户反馈"飞书发的还是老一套文字墙，让你改你当看不见"——S4b 指令台化提前落地）：旧格式 10 行依据披露开场 + 每票一段小作文 + 观察池列 4 只全买不起的票，动作埋在中间。新结构动作优先：①编号动作区（交易单+策略现金操作统一编号，每条含一句话依据/边界规则/**可直接复制的回填命令**如 `/成交回填 002475 卖出 200 54.1`）；②持有观察区一票一行（现价/浮亏/规则状态/AI 一句话——AI 注释只在此区展示，动作卡片保持干净）；③现金与策略区两行；④观察池**仅在现金买得起一手时展开**（现金不足时一行说明"最便宜一手约 ¥X"），回答"观察池对我有没有用"；⑤依据溯源压缩为一行。实测对比：同日决策包从 ~40 行降至 ~22 行，前 4 行内出现全部可执行动作。同步清理：删除今日 bus 中旧价位（regen 前）触发的两条脏告警（旧 server 进程内存残留，与决策包新价位同日矛盾）。新增/更新渲染断言（待办标题/回填命令/破位一行式/WAIT 行无卖出）。
- [文档] 策略中心重构计划 v3.0（用户要求"自由追求最好的方案"——重写而非修订）：本版不再浮于架构图，直面项目真正的根结——quant_research 已有生产级交易引擎（AccountRepository 事件溯源账户/PaperBroker 真实费率/两段式 PIT 回测/RuleBook 品种规则），但 dsa 侧从未用它，反而用轮子重搭了半个（两套账本语义/两套仓位管理/两套成交假设并存）。v3.0 的唯一目标是让 quant 接管"交易事实层"（账户+策略评估+回测），dsa 只保留"决策与交互"（组合器+指令台+解释层）。核心决策：A1 quant 的 AccountRepository 成为唯一账本（/成交回填 落到 quant 而非 dsa 的 portfolio_trades）；A2 策略统一入口 compute_target_portfolio（输出目标持仓而非买卖指令，天然解决"光卖不买"）；A3 用户规则降级为约束（破位诊断是约束紧急程度标记，不是决策）；A4 E2 择时降级为卫星预算系数；A6 通知事件化（v1.1 D6 保留）。阶段序：P0✅ → P1 账户真源对齐 → P2 策略统一入口（quant 侧 strategy/ 目录）→ P3 组合器与执行对齐 → P4 验证闭环 → P5 交互指令台 → P6 买侧落地（组合器输出即调仓单）→ P7 收敛治理。每阶段强制验收/指标/回滚三件套。
- [文档] 策略中心重构计划 v2.0（按用户 12 项审计重排全部优先级）：核心变更为**信任先行**——正确性/诚实性/可用性未建立前不动架构（v1.1 的"架构先行"被审计证伪）。新阶段序：P0 已完成（骨架+审计 6/7/8/10/1 修复）→ P1 最后一公里（股票卖出→/成交回填→账本联动 E2E + 回填时延/规则遵循率/止损滑点三度量基线 + 影子度量语义重定基线——人工否决与偏差分桶）→ P2 规则语义修正（分级执行条款显式化/规则休眠/暂缓记忆落盘/阈值配置化/集中度单一来源/regen 每周管线化）→ P3 决策引擎拆分（黄金基线前提=P1+P2）→ P4 验证与成绩单（快照含决策包全文入 hash 保护——审计 #11）→ P5 买侧结构（买入触发规则位/候选池/position_sizer 闸门/E2 降级为卫星预算系数——审计 #3/#4，唯一外部 block=用户买入范围意图）→ P6 交互通知 AI 归位（审计 #12）→ P7 收敛 → P8 持续线+文档治理（FINAL-PLAN 标注 superseded）。每阶段强制验收标准/成功指标/回滚方式三件套制度化（审计 #5）。
- [新功能] price_cross 价位按市场结构重新生成（用户裁决"重新生成，根据市场行情变动价格"——替换成本±固定百分比机械模板）：①新增 `scripts/regen_price_rules.py`——从本地日线（与破位诊断同源）按"结构锚 + 波幅"生成四档价位：止损一档=近20日收盘最低点（跌破=20日结构破坏）、止损二档=最低点−1.5×近20日平均日波幅、止盈一档=近20日最高点（上穿=动量突破）、止盈二档=最高点+1.5×日均波幅；默认干跑打印新旧对比，`--apply` 写回 .env（只替换目标标的的 price_cross，涨跌幅/量比等其他规则逐字节保留）；标的缺省取当前持仓快照；建议每 1-2 周或显著波动后重跑。②已应用（2026-09-19，数据截至 09-16）：000899 止损 9.35/9.18（原 10.87/10.65）·止盈 10.05/10.22（原 11.77/12.89）；002475 止损 51.63/50.0（原 55.07/53.93）·止盈 57.62/59.25（原 59.61/65.29）——000899 从"每日重复全卖指令"变为结构止损观察（现价高于 20 日低点止损档），002475 集中度减仓决策不受影响。③依据披露文案同步：来源从"机械模板（成本±固定百分比）"改为"结构锚生成（近20日低/高±1.5×日均波幅，脚本按行情重跑更新）"。server 已重启加载新规则并验证决策输出。
- [修复] 审视修复三连（用户 2026-09-19 全面审计的第 6/7/8 条，半仓流程必经路径上的正确性缺陷）：①WAIT 档指令矛盾——破位诊断"建议暂缓一日观察"时订单仍生成全量卖出（`decide_position` 只处理了减半档），现为持有决策（目标=当前量、无卖出腿、文案与指令一致）；②`load_latest_sheet` 同日多单按文件名（uuid 十六进制）排序选"最新"等于抽签，`/成交回填` 与决策快照都压在该函数上——改按修改时间排序；③当日 partially_filled 单重跑 /操作单 会另存新单叠加（幂等分支只认 pending），而"卖100留100"的半仓流程使部分成交成为预期路径——partially_filled 同样沿用既有单。新增 4 项回归测试（WAIT 持有无卖出腿/mtime 压过字典序/部分成交沿用不另存/无单返回 None）。
- [修复] 决策文案来源诚实化（审计第 1 条部分）：止损/止盈执行文案从"你设定的价位已被跌破"改为"止损位已被跌破（价位为系统登记的规则，见依据披露）"——反推证实四档价位为成本±固定百分比机械模板生成、用户从未确认，不得断言为用户意图；完整归化（确认价位/改波动率基准/移除）待用户裁决。
- [新功能] server 运行守护（审计第 10 条：09-18 整个交易日因 server 进程死亡丢决策包，账户中心可用性=server 进程可用性）：①`scripts/keepalive_server.ps1`——端口探测（2s 超时）不通才拉起 hidden 窗口实例，`logs/keepalive.log` 留痕，已验证存活时零动作；②Windows 计划任务 `DSA_Server_Keepalive` 每 10 分钟（已注册验证 Ready）。已知限制：本机守护，关机即全停。server 已于 09-19 重启（running=true、feishu_stream_alive=true、监控恢复）。
- [文档] 策略中心重构计划 `docs/REFACTOR-PLAN-STRATEGY-CENTRIC.md` v1.1（用户定调"根据策略做整个项目的重构"，并要求覆盖通知与其余全部模块）：①全项目模块处置总表——通知系统 14 个 sender 实测盘点（保飞书 1、砍 13，deprecate 先行）、新增统一决策事件消息模型（一切出站消息=logical_id/priority/action_hint 的事件，发送方只产事件渠道只消费）；Bot 20 命令分保留强化/标的收敛两档；API 19 endpoint 分保留/冻结/并入策略管道三档；Web 14 页保留 9、砍除合并 4；桌面端冻结不删；数据源/Agent/LLM 管线各自的收敛规则；②八项核心决策（新增 D6 通知事件模型、D7 桌面端冻结、D8 砍除先 deprecate）；③S0-S6 阶段细化为 S4a 通知事件模型/S4b 指令台化/S4c LLM 归位、S5a 通知砍除/S5c 产品面收敛，含顺序约束（S4a 先于 S5a 防消息断流）。
- [新功能] 策略部署层（重构第一步，用户要求"为部署调研所得策略而重构项目"）：①仓库根新增 `strategies.yaml` 策略注册表（mandate.yaml 先例：改参数不改代码），每条策略带 tier/risk/status/params，状态机 registered→replicating→shadow→active（rejected/dormant 可达），与 `docs/strategy-catalog.md` 一一对应；②新增 `src/services/strategy_engine.py` 统一评估引擎——只评估 active 策略，输出统一 StrategySignal 契约（tier/risk/action/reason/detail），单策略失败隔离不拖垮其余；③首批 active 策略上线（免回测的规则性正期望/无风险两类）：国债逆回购窗口（明日非交易日=周五/节前跨日计息 + 月末窗口触发，金额按可用现金 1000 整数倍，品种 GC001/R-001）与转债打新汇总（复用 bond_new_issue_calendar 扫描做盘前清单，不重复推送）；④`BondNewIssueCalendarService` 新增只读公开方法 `upcoming_reminders(days)`（发行表一次拉取多日复用，不推送不写状态），`_collect_reminders` 增加可选 records 参数避免重复拉网；⑤决策包现金块新增"策略操作建议"区（AccountDecision 增 strategy_signals 字段，快照 to_dict 同步）。实测（2026-09-18 周五）：逆回购信号触发（¥1,549 现金→操作 GC001 ¥1,000，理由"明日非交易日跨日计息"）；打新汇总拉到瑞鹄转02 缴款提醒。新增 14 项单测（注册表加载/周五窗口/月末+国庆双理由/普通日无信号/最低现金/整千取整/打新汇总/失败隔离/信号契约）。
- [文档] 策略目录 `docs/strategy-catalog.md`（2026-09-15~18 多轮社区调研沉淀）：风险谱系总表（零风险→高风险五层）+ 每条策略的规则/社区实证/风险来源/接入点/验证状态；状态机与接入管道（登记→换源复现→对照实验→影子→准入）；明确排除项（LLM 直接交易指令、下修博弈、LOF 高溢价等，均带证据）；已否决项（本项目自测：双低合成因子、下修博弈）。免回测白名单的两类（逆回购=无风险利率、打新=规则性正期望）与目录状态机对应。
- [修复] 决策快照被空持仓覆盖（决策快照 portfolio 为空且与当日真实持仓/操作单不一致的根因）：实测 `DATABASE_PATH` 默认是相对路径（`./data/stock_analysis.db`），**数据库取决于进程工作目录**——工作目录不同会读到不同（甚至全新空）的库。已确定性复现：项目目录下读到现金 1549+两只持仓，仓库根/用户主目录下读到 0/0。当另一进程（不同 cwd / 不同 DATABASE_PATH）刷新同一份快照时，就会把有持仓的快照覆盖成空仓。①`build_snapshot` 新增 `data_versions.database_path`（本次进程实际使用的数据库绝对路径）——快照“哪份数据算出来的”可回溯；②`refresh` 新增降级覆盖护栏：同日 + 既有快照有持仓 + 新快照无持仓 + 数据库路径不同 → 拒绝覆盖并告警（合法清仓、同一数据库不受影响）。新增 5 项单测。
- [新功能] 决策包"依据与验证状态"披露（用户问"决策引擎的策略哪里来的、需不需要验证"）：`/操作单` 账户决策包标题下新增固定的诚实分级块——逐条列明止损/止盈（你在 .env 配的 price_cross 价位，机械模板·未经回测验证）、破位分级（深度/持续性/均线/量能启发式·未验证）、集中度（35% 共识阈值·未在本账户规模验证）、E2 择时（转债估值分位·实验性）、AI 分析（仅解释不决策）、买入候选（观点非信号），让"规则从哪来、验证到什么程度"每天随决策包可见，而不是只藏在文档里。
- [修复] price_cross 破位告警改"状态型"去重（用户反馈"你又说了又说"——同一破位状态每天重复推送）：此前 legacy 价格规则靠内存指纹 TTL 24h 去重，导致 000899/002475 在 09-14/15/16 各收到几乎相同的"跌破价位"告警。现按"被跌破档位集合"签名判定——签名未变（同一破位状态持续）不再重复推送，新增档位跌破或复位后再次破位才推；签名持久化到 `data/cache/alert_breach_state.json`（原子写+异常安全），进程重启不再重发；本周期确认无触发即清零（复位）。触发器记录与审计不受影响，仅抑制重复的用户通知；其余告警类型（成交量/涨跌幅）行为不变。新增 `breach_state_suppressed` 统计与单测（同态持续/新增档位/复位重推/跨实例持久化）。
- [新功能] 买入命令 `/买入`（用户反馈"光卖不买"——买入侧最小闭环，用户确认才落单）：①`/买入 <代码> [数量] [价格]`（别名 加仓、买）把一条买入腿追加进**当日已存在的**操作单（追加而非重建，保留既有 orders/fills 与回填状态），并记入模拟账本（幂等）；②闸门全部复用既有实现且不抛栈——交易暂停拒绝、当日无操作单则提示先看 `/操作单`、现金 ≤0 拒绝并提示 `/入金`、非整手拒绝、超单笔 20,000 元上限拒绝、成本门给出往返成本与回本涨幅；③限价缺省用最近收盘价（全市场快照→本地日线既有取价路径，不新增数据源），数量缺省按可用现金整手计算并受单笔上限截断；④命令回复含方向/数量/限价/金额/成本/回本涨幅与 `/成交回填` 指引，并标注溯源（今日观察池候选 / 你直接指定）；⑤决策包"买入观察池"段为每个候选补"参考执行"行（限价·可买手数·往返成本·回本涨幅）并给出"回复 /买入 生成买单"指引，回答"卖出的钱去哪"。`order_sheet_service` 新增 `recalculate_totals` 与 `generate_order_sheet` 的 reasons/hold_reasons 注入参数（决策引擎按标的注入理由）。新增 9 项买入命令单测（正常落单/暂停/无单据/现金为 0/超上限/非整手/缺省数量/异常不抛栈/参数不足）。
- [新功能] 买入观察池 + market_scan 管线接线（用户反馈"光卖不买"——买入侧此前无候选来源）：①`market_scan_service`（全市场快照 + 规则漏斗，蓝图 ②-B 建成后一直未接线）正式接入每日管线——每交易日盘前自动落库全市场截面快照并跑规则漏斗（5563 → 流动性/市值/估值/ST 过滤 → 候选池），失败不阻塞主决策但写入 pipeline_status 留痕；②新增 `buy_watchlist_service`——从候选池按确定性规则打分（温和上行 0~7% / 量比 0.8~3.0 / 换手 0.5~10% 硬过滤；涨幅 1~5%、量比 1~2.5、PE≤30、换手 1~5% 结构值加分，参数不做网格调参），剔除北交所与已持仓，输出 top5 买入观察列表；③账户决策包新增"买入观察池"附注区（观点非信号——未经回测验证、不产生买卖指令、不进 order_sheet 状态机），回答"卖出的钱去哪"；E2 防守区照常展示但标注择时风险；新浪降级快照缺量比/换手/PE 列时打分自动退化为涨幅+成交额（缺列不扣分）；快照停更 >3 天 source_note 告警、>7 天观察区降级为空并提示 market_scan 未运行；④新增 14 项单测（打分规则/北交排除/持仓排除/缺列容错/快照回溯/停更告警/降级）。
- [新功能] 破位动态诊断（用户反馈"持仓已跌破限价，你该根据目前变化详细判断"，而非机械全卖）：规则触发后新增三维诊断——①破位深度（现价低于最深止损档）②持续性（连续收盘低于止损档天数）③均线结构（MA5/10/20 排列 + 价格站上几条）④量能（近5日/近20日均量比）；按明确优先级树输出三档建议：缩量浅破位→**暂缓一日观察**、深破位/长期破位/放量→**立即全部卖出**（但连续仅 1 日则降档）、其余→**减半观察**。**减半建议会真的减半**（目标数量同步整手减半，不足一手时明确标注"无法拆分"并退回全卖），杜绝"文案说减半、订单却全卖"的指令矛盾。只读本地日线、不联网、失败不阻塞决策。实测当日：000899（连续22日·均线纠缠·放量）→立即全卖 100 股；002475（连续1日·空头排列·量能平稳）→减半卖 100 股保留 100 股，同一规则对两只票给出差异化处理。
- [文档] 交接文档补齐（用户反馈"通知不满意但我看不到配置在哪"）：①新增 `docs/feishu-handoff.md`——飞书三种接入方式（群 Webhook / App Bot / Stream）的区别、当前实际生效配置（含 `FEISHU_CHAT_ID` 私聊时实存 open_id 的命名陷阱、行内注释隐患）、发送与交互链路图、三类通知路由表、每日摘要折叠与例外、按现象查的排障手册、新环境迁移步骤与安全边界；②新增 `docs/account-decision-rules.md`——决策规则溯源：price_cross 升级语义、当前价位实为"成本 ±固定百分比"机械模板（反推比例已列表）并非研究结论、四条依据各自的验证成熟度、为什么现在只有卖没有买（买入侧无合格信号源 + E2 防守区）、破位分级机制与未经验证的诚实边界、规则不休眠等已知缺口；③`docs/bot/feishu-bot-config.md` 补私聊命名陷阱、群解散错误 230002 处置、行内注释禁忌与交接文档入口；④`.env.example` 补应用凭据不等于推送开启、私聊语义、Stream 事件订阅前提。
- [修复] 通知诊断过时文案：`NOTIFICATION_DAILY_DIGEST_ENABLED=true` 时诊断曾报"当前为预留配置；P4 不会发送每日摘要"，与已实现的摘要总线（JSONL 总线 + logical_id 折叠 + 到点聚合）完全相反，会误导交接——改为 info 级说明实际行为与两处即时发送例外。
- [新功能] 账户中心决策引擎（FINAL-PLAN-ACCOUNT-CENTRIC R1）：/操作单 与每日管线推送从"CB 策略目标 diff"重构为"账户决策包"——决策对象是用户真实持仓+现金（000899/002475/¥1,549），决策依据按优先级为①用户价格规则（AGENT_EVENT_ALERT_RULES_JSON 的 price_cross 升级为决策语义：止损位跌破/止盈位上穿 → 卖出全部）②组合集中度（超 35% → 减仓到阈值内，整手取整）③E2 择时（估值分位 >80% → 现金防守不新增买入）④AI 分析（每条决策附最近一次分析的一句话解释，只解释不决策）；CB 策略目标降级为"研究观察"附注段（paper tracking 状态可见但不再指导操作）。卖出/减仓指令继续由 order_sheet 状态机承载（限价/放弃条件/14:50 撤单/成本门/单笔上限/回填幂等全部复用）；新增 account_decision_service + 12 项单测，规则匹配修复两处缺陷（Config 须走 get_instance 单例而非直构；规则键统一为持仓快照的裸码形态）。实测当日决策：000899 与 002475 止损位均已跌破 → 两条卖出建议（¥975 + ¥10,556）。
- [改进] 股票分析报告语言清理（用户反馈"优化语言"）：①筹码结构行从裸管道数字（"32.8 | 10.5964 | 16.81 警惕"）改为带标签中文（"获利盘 32.8% · 平均成本 10.60元 · 90%集中度 16.8%（警惕）"），数值口径自适应（0-1 小数与百分数两种历史格式）；②量比缺失不再打印 "None"，改为"缺失"（与状态"量能正常（量比数据缺失）"不再自相矛盾）；③报告 prompt 的 data_limitations 示例明确要求完整中文句子、禁止照抄 technical: partial 等原始枚举键值——LLM 报告不再泄漏英文枚举。
- [修复] 管线并发互斥与超时根治：①server 5 分钟重试与手动补跑可能并发执行 pit_tail/assemble——run_daily_pipeline 加 O_EXCL 锁文件互斥（陈旧锁=持锁进程已死可抢占）；②akshare 底层请求无超时，单只债网络挂起拖死整步——fetch 加 30s socket 级超时；③update_pit_tail 新增 --time-budget 优雅退出（增量断点安全，部分进度 rc=0），管线预算 420s 为 600s 步骤超时留 DNS 挂起余量（getaddrinfo 不受 socket 超时约束）。
- [新功能] E2 真实账本事件完整性（RESEARCH-BROAD 批次收官，蓝图 options-monitor repository_core）：①payload_hash 列（INSERT 时应用层对财务字段算 sha256，事后改值可检测，历史行已回填）；②财务字段 UPDATE 即 ABORT 的存储层触发器（trade/cash 两表按各自列适配）；③DELETE 强制审计留痕——before-payload 写入 portfolio_event_audit（该表自身 append-only 触发器保护）。设计取舍：API 修正删除流保留（delete 后重新登记），未采用无条件 append-only ABORT 以免破坏已文档化的修正路径。验收四反例：hash 维护/UPDATE ABORT/DELETE 留痕/审计表防改全过，portfolio 73 项回归通过。
- [新功能] E3 信号状态体系（RESEARCH-BROAD 批次）：导出契约目标三态分层——executable（有有效价格，随单下发）/ blocked（生命周期，带原因）/ manual_review（无有效价格，人工核实，不虚构不静默）；targets 只装 executable；操作单新增"信号状态：executable×N · blocked×M"汇总行与人工核实明细段。消除"current_plan 里出现的就是能买的"歧义。quant 导出测试同步 v2 断言。
- [新功能] E1 成本门与生成侧风控（RESEARCH-BROAD-20260913，先例：ToFu fee_book / vnpy OrderSizeRule / Ghostfolio fee 一等字段）：①操作单每笔订单显示往返成本与回本涨幅（"需涨 0.84% 才回本"，≥1% 提示确认值得再做）；费率复用 simulation_ledger 纯函数，转债免印花税；②生成侧单笔金额上限 20,000 元（异常价格数据截断或拦截并要求人工核实）；③/成交回填 支持可选 费用N 参数，未传按费率模型估算并标注 estimated（Ghostfolio fee 一等字段语义——费用不缺席）；④影子周报偏差标签化：超量执行/价格偏离/延迟回填分类计数入周行。
- [新功能] E1 成本门与生成侧风控（RESEARCH-BROAD-20260913，先例：ToFu fee_book / vnpy OrderSizeRule / Ghostfolio fee 一等字段）：①操作单每笔订单显示往返成本与回本涨幅（"需涨 0.84% 才回本"，≥1% 提示确认值得再做）；费率复用 simulation_ledger 纯函数，转债免印花税；②生成侧单笔金额上限 20,000 元（异常价格数据截断或拦截并要求人工核实）；③/成交回填 支持可选 费用N 参数，未传按费率模型估算并标注 estimated（Ghostfolio fee 一等字段语义——费用不缺席）；④影子周报偏差标签化：超量执行/价格偏离/延迟回填分类计数入周行。
- [新功能] 模拟账本无主条目隔离（批次C5）：sheet 文件已不存在的条目自动移入 quarantined 区保留审计，不参与对比统计——修复历史操作单重建/删除导致的失配污染（实测隔离 38 条，含无主 sheet 与已删单据残留）；账本加载时幂等执行，判据只查 sheet 存在性（不按今天退市状态改写历史）。/对比 口径自此以活跃条目为准，影子期执行率基线重建。
- [新功能] 组合集中度风险行（批次D）：/健康 接入 PortfolioRiskService.get_risk_report——单标的持仓占比超阈值（默认 35%）显式预警并显示持仓市值；实测用户账户 002475 占 91.7% 触发预警。复用既有风控服务，不新建第二套计算。
- [新功能] 影子期执行度量（OPTIMIZATION-PLAN-V2 第三批）：新增 shadow_metrics_service——按周聚合执行率（数量口径，合理放弃剔出分母）、金额加权平均滑点（买贵/卖便宜为不利方向）、放弃率、漏回填腿数与平均回填时延；/对比 周报 输出近 4 周明细，每日摘要尾部自动附本周指标行（有单据才显示）。验收：已知场景（一腿足额买贵 0.354%+一腿放弃）指标精确命中。
- [改进] 个人持仓展示成本与浮动盈亏（用户反馈"当时的买价不是现价"）：操作单个人持仓行现显示 现价/市值 + 成本/浮动盈亏（元与百分比），数据来自 /成交回填 记录的真实买价（fifo avg_cost）。
- [修复] 操作单三处显示缺陷：①部分成交 hold 文案按现金状态区分（available_cash<=0 才提示未登记，否则显示可用金额与本手所需）；②E2 择时行因 quant_research 内外层包名遮蔽在 server 进程内 import 必炸且被静默吞掉——改为内联 expanding 分位计算（与 /健康 同算法同 PIT），择时行恢复显示；③修复后 /操作单 首次输出完整可执行决策包（买入限价+张数+放弃条件+择时提示+生命周期阻断+个人持仓）。
- [新功能] /入金 /出金 命令（修复 P0.4 闭环缺口）：此前 /持仓登记 只支持买卖交易、入金无命令可走，现金闸门的恢复路径指向了不存在的操作——新增现金登记命令复用 record_cash_ledger 单一真源，登记后自动刷新决策快照并即时反馈账面现金与闸门状态。
- [新功能] 模拟账本无主条目隔离（批次C5）：sheet 文件已不存在的条目自动移入 quarantined 区保留审计，不参与对比统计——修复历史操作单重建/删除导致的失配污染（实测隔离 38 条，含无主 sheet 与已删单据残留）；账本加载时幂等执行，判据只查 sheet 存在性（不按今天退市状态改写历史）。/对比 口径自此以活跃条目为准，影子期执行率基线重建。
- [文档] ytm_true 接班实验预注册（ytm-true-preregistration.md）：五门槛运行前写死（净收益超双低+2pp、2×压力档不翻负、回撤 ≤1.1×、分年稳健 ≥60%、D2 残差 IC 检查），fail 即关闭；状态"已预注册未运行"。
- [修复] 研究证据链重建（OPTIMIZATION-PLAN-V2 批次C）：①E1 评估器重写——premium 逐日 PIT 对齐（旧实现窗口末值映射历史，实测前视中位偏差 17.5pp/排序相关 0.146）、调仓日收敛为季度首日（旧用 263 个采样日）、现金守恒的整手换仓、修正转债免印花税；修正后重跑：双低 -30.45% / ytm_true +13.11%（最强臂）/ 合成 -36.98%，gate 仍 fail（维持关闭），ytm_true 待独立预注册；②E2 current_state 补 as_of，/健康 弃静态 JSON 改按最新 PIT 动态重算 expanding 分位（与操作单同源，实测静态 95.1% vs 动态 88.8%）；③双低 verdict 方向勘误：原始双低负 IC 是因子有效方向，"先反转后归零"表述修正；④E1/E2 旧结论 superseded 标注保留审计轨迹。
- [新功能] 成交回填完整性（OPTIMIZATION-PLAN-V2 批次A）：①幂等键 trade_uid（腿+日+量+价派生）——同一笔回填两次只入账一次（此前实证双记账）；②先写真实账本、成功后才标操作单（账本失败不留半状态）；③部分成交累计量语义：fills 累计而非覆盖，单据状态对齐 FIX OrdStatus（pending/partially_filled/filled/cancelled/expired），加权均价记录 fill_count；④量价合理性门：回填价偏离限价 ±5% 或量超计划剩余需追加"确认"参数；⑤/撤单 命令（部分成交后撤剩余、已成交量保留）；⑥跨日 pending 单读取时自动过期（昨日限价今日不可执行）。重复回填实验数据已清理。
- [修复] 通知真实回执（批次B）：每日摘要与每日管线检查 send_with_results 的 success——通知失败不再写 digest_sent 标记、不再置 pipeline ok=true；pipeline_status 新增 notify_ok 字段与"决策完成≠通知送达"分离；通知失败推 system_error 并次日重试。反例实测：mock success=False 时无 sent 标记。
- [修复] 安全契约复核 R0（SAFETY-AND-EVIDENCE-REVISION-20260912）：①暂停/负现金闸门从"清空目标"改为**观察模式**——此前 targets={} 会被生成器解释为目标 0，已持有转债会生成清仓卖单（暂停反而触发卖出）；反例实测：暂停与负现金均零指令、个人持仓照常展示。②旧 pending 操作单在闸门命中时标注"不可执行，仅留档"。③决策快照修复 OrderLine 序列化缺失（非空订单曾导致快照丢失操作单块）；新增 signal_available 字段，契约缺失/PIT 错误不再伪装 fresh=true。④暂停状态不可读时按阻断处理（fail-closed）。122 项回归通过。
- [新功能] /暂停交易 /恢复交易 命令（业务级 kill switch，FINAL-MODIFICATION-PLAN P0.7）：暂停后数据/监控/查询照常，但操作单不再生成任何买卖指令；状态原子落盘（trading_halt.json），每次切换记录原因/操作者/时间；恢复时自动刷新决策快照。操作单幂等命中旧单时同样显示暂停/现金闸门状态，杜绝"旧单看起来仍可执行"。
- [新功能] 信号契约升级 v2（cb_current_plan_v2）：每个目标携带 price_date/price_source/data_version，plan 携带 pit_data_version（PIT parquet sha256 前 16 位）——下游可校验"价格是哪天的、来自哪份数据"；bridge 双 schema 白名单兼容。
- [新功能] 现金硬闸门（P0.4）：现金为负（账户缺资金入账）时买入目标全部降级人工核验并显式说明原因，现金未登记（¥0）时买单不给出张数；闸门在新单与幂等旧单两条路径统一生效。
- [修复] 操作单盘前直发：report 路由默认被每日摘要总线拦截，导致 09:00 生成的操作单压到 18:00 才送达——管线推送改为"总线留索引 + bypass 直发"（与告警同模式），恢复蓝图"盘前推送操作单"契约；信号新鲜度拒绝时改走 system_error 即时告警。
- [修复] PIT 部分日截面保护：盘后增量只刷新部分券时，当日截面会残缺（实测 18/283）并扭曲 E2 分位数与 min_candidates 池判定——assemble 时裁掉截面数不足前一日 60% 的尾部日期，PIT 末端始终停在最后一个完整日。
- [改进] LLM 配置收敛为单渠道单模型：主力切换为火山方舟托管的 `openai/deepseek-v4-pro-ga-260813`（实测 3.7s 正常返回）；删除欠费（402）的 DeepSeek 官方渠道与 key 失效（403）的 wawapii 中转渠道；按用户要求不使用方舟其他模型（doubao 系全部移除）。
- [改进] LLM 备用线路补充：新增 tokenrhythm 中转渠道作为唯一 fallback，模型按用户指定为 `glm-5.3-flash`（实测 4.5s 正常返回）——主力失败时降级到中转线路，实现线路级冗余而非跨模型降级。
- [新功能] Decision Snapshot 单一事实源（FINAL-SOLUTION W3）：新增 decision_snapshot_service——信号契约/持仓现金/操作单/PIT 数据版本（sha256+截面券数）收拢为版本化快照（latest.json 原子写 + 按日归档保留 90 天）；每日管线导出后与 /操作单 两条路径自动刷新；/健康 新增快照行，/api/v1/assistant/runtime/status 新增 decision_snapshot 字段供 Web 横幅与桌面托盘共用；计划新鲜度评估下沉为服务层唯一实现，操作单门控与健康视图共用同一口径。
- [新功能] paper tracking 现金季度记录：空调仓季度（min_candidates 让位现金）不再报错缺表，诚实记录为平盘权益+零成交+零费用行；2026Q1/Q2 已补录，三表恢复 2025Q4 起连续（2026Q3 待季末）。
- [改进] /健康 增加可用现金状态行：现金未登记且存在买入目标时显式提醒 /持仓登记 记账，闭合现金登记提醒环。
- [修复] PIT 数据管道断裂修复（FINAL-SOLUTION P0）：`update_pit_tail --limit 50` 每日滚动导致 PIT 截面从 300 只衰减到 17 只、2026 全年调仓空转 11 个月——周日全量刷新（914 只约 2 小时）+ 工作日增量优先队列；assemble 后新增截面覆盖率护栏（<200 只推 system_error 提示全量刷新）。
- [修复] 生命周期硬阻断：quant 导出计划前消费 cb_calls/cb_terms 生命周期事件——强赎已实施/到期赎回/已到期/行情缺失超 7 天的标的一律剔除并记入 plan.blocked（含原因），存续权重重新归一化；DSA 操作单展示阻断明细；123089.SZ（2025-09-16 公告强赎、2025-10-23 退市）不再出现在目标中。
- [修复] 操作单信号新鲜度门槛：pit_end 超 7 天或 generated_at 早于 pit_end 时拒绝生成操作指令（诚实降级为"信号过期"），绝不渲染陈旧计划。
- [修复] 通知总线收敛：引入 artifact_type/logical_id/symbols 业务身份，同一操作单刷新替换旧摘要；逐规则告警按标的/类型/方向聚合为一条用户通知，同时保留每条规则的审计记录；测试推送支持 test_mode，避免污染生产摘要。
- [改进] 操作单与组合可见性：股票持仓只展示股数和实时/最近收盘参考价，不进入转债目标 diff；/健康增加自选股/真实持仓与个性化告警规则覆盖诊断；操作单载荷带 PIT 末端、计划生成时间和 stale 标的。
- [修复] 通知缺口修复（配置审计发现）：①每日摘要总线不再拦截 alert 类推送——盯盘告警/止损止盈/监控告警恢复即时推送（此前 digest 开启后全部延迟到 18:00，盘中止损信号收盘后才达）；②删除 .env 中三个无代码消费者的 tushare backup 死变量及误导性"三级 failover"注释；③ETF 溢价监控标的显式声明（511380 转债 ETF）。
- [修复] G6 清理与解冻：E2 择时硬编码日期截断改为跟随 current_plan 的 pit_end 动态计算；删除 .env 幽灵配置（STRATEGY_SIGNAL/ALPHASIFT）与 config.py 过时的 digest 预留告警；simulation_ledger 路径层级修正（parents[1]→[2]）并合并历史分裂账本（8 条）；bot 文档 webhook URL 描述改为 Stream 模式说明；打开三个市场监控开关。
- [新功能] G2 运行状态端点：GET /api/v1/assistant/runtime/status（uptime/飞书在线/管线最近执行），Web 横幅与桌面托盘 tooltip 共用数据源。
- [新功能] G4 每日自动化管线：runtime_scheduler 注册 daily_pipeline 任务（09:00 轮询触发）——PIT 增量（子进程+网络重试）→ assemble → 信号导出 → 操作单生成（幂等）→ 推送，任一步失败推 system_error 告警；状态落 pipeline_status.json 供 Web 横幅；修复 update_pit_tail 的 --limit 语义（PIT 已最新时快速退出防超时）与网络抖动重试。
- [新功能] G5 成交回填闭环：bot /成交回填 命令 + POST /api/v1/assistant/order-sheets/fills 端点——操作单匹配腿标 filled + PortfolioService 记真实账本 + /对比 立即可见；canonical_symbol 扩展股票裸码（6 沪 / 0、3 深）。
- [新功能] 桌面端 P5 开机自启：托盘菜单 checkbox 开关 + desktop:get/set-launch-at-login IPC（Web 设置页可调用），Electron 31 正确 args 处理（--hidden 启动直接进托盘，读写传相同 args），dev 模式禁用防误注册 electron.exe。
- [新功能] 桌面端 P4 通知桥：后端新增免鉴权 SSE 通知流端点（/api/v1/assistant/stream/notifications，StreamingResponse + 5s 轮询 mtime 增量推送 + keepalive 心跳），主进程 fetch 流式订阅 → Windows 系统通知弹出（点击聚焦主窗口），断线退避重连（上限 5 次防挂起）；手机推送链路（企微/飞书 webhook）不变，双路并存。
- [新功能] 桌面端大修（P1-P3）：AppUserModelID 设置、单实例锁、崩溃自动重启（指数退避+稳态重置）、稳态健康监控（30s 轮询+unref）、托盘常驻三态图标（关窗缩到托盘继续运行）、系统通知基础（Notification+点击聚焦）
- [文档] 桌面端大修计划更新为 v2：基于 Electron 官方文档与参考项目（every-pdf 进程生命周期模式）调研修订——ICO 托盘三态、免鉴权 SSE 通知端点（照抄仓库内 analysis.py 先例）、every-pdf 式崩溃退避重启+稳态健康监控、Electron 31 正确的自启 args 处理；取消单文件拆分（测试绑定），改单文件分区改造（P1-P6 含已验证行号）。
- [文档] 新增 DESKTOP-OVERHAUL-PLAN.md：桌面端彻底大修计划（G0 前置 + D1-D8）——main.js 模块化拆分、托盘常驻与状态灯、SSE 通知桥（桌面系统通知与手机推送双路并存）、后端崩溃自动重启、单实例锁；吸收并替代 CLOSURE-FIX-PLAN v2 的 G1。
- [文档] CLOSURE-FIX-PLAN 重写为 v2（G1-G7）：核心变化是围绕用户痛点重设计——一键启动/停止桌面入口（无终端）、Web 首页运行状态横幅（看得见系统活着）、飞书早安心跳、每日管线自动跑；v1 的 F1/F2 已完成并标记。
- [文档] 新增 CLOSURE-FIX-PLAN.md：基于全仓端到端审计的 12 条断裂链路修复计划（F1-F7），含平台决定（飞书 Stream）、执行顺序、逐项验收标准与 GLM-5.3F 执行注意事项。
- [文档] 新增 quant_research/docs/CB-RESEARCH-PLAN.md：基于三路独立调研（双低失效考古/YTM 因子与死亡判定方法论/接班候选面）与本地 PIT 实证的转债策略诊断与接班研究计划（D1-D3 诊断线 + E1-E3 实验线，全部预注册、N 计入 registry）。
- [修复] 恢复被意外覆盖的 .env.example（内容曾被写成执行蓝图全文）：从最近健康提交还原并重新套用 screening/AGENT_MODE 冻结标注；配套修复 run 补交的 evals 模块与 assistant 端点漏 add 问题，全仓 71 文件变更逐一审计确认污染面仅此一文件。
- [文档] 蓝图阶段④砍除核实结论：screening（SCREENING_ENABLED 默认 false + 入口把关）与 Agent 多智能体例行模式（AGENT_MODE 默认 false 显式 opt-in）本就不在每日默认路径；.env.example 补冻结标注与成本警示，run_diagnostics/配置三件套收缩降级为按使用反馈再议。
- [新功能] Web 新增助手三页（/assistant/health、/assistant/compare、/assistant/stats）：运行健康五要素、模拟vs真实逐笔对比、信号后验统计；后端新增 /api/v1/assistant 视图端点（health-view/comparison/order-sheets）。
- [新功能] /操作单 生成即记模拟账本（幂等）；bot 新增 /对比 命令：模拟 vs 真实成交逐笔对比（价格/费用/回填状态），量化信号可信度。
- [新功能] 模拟账本 simulation_ledger：操作单生成时按限价假设成交记模拟侧（费率模型与 quant_research PaperBroker 数字对齐：佣金地板/卖出印花税/过户费，滑点按 F6 解析口径单列），与真实账本（PortfolioService）逐笔对比并渲染成交价偏差——量化"要不要信它"。
- [新功能] 全市场扫描数据地基（market_scan_service）：每日全市场截面快照落库（东财主源+新浪降级，parquet 按日增量），零 LLM 规则漏斗（流动性/市值/估值带/ST 剔除/截断，参数读仓库根 mandate.yaml 声明文件），全市场 5555 → 候选池 300 真实验证。
- [新功能] bot 新增 /健康 命令（运行健康视图）：CB 信号数据新鲜度、今日推送总线计数与摘要状态、当日操作单状态、活跃决策信号数、异常探测（契约缺失/解析失败/数据超 7 天），30 秒讲清系统运行状况。
- [新功能] 每日摘要总线落地（NOTIFICATION_DAILY_DIGEST_ENABLED 由预留变真实实现）：非 system_error 静态推送写入当日 JSONL 总线（持久化去重，重启不丢），每日 NOTIFICATION_DIGEST_TIME 聚合为一条分级摘要（持仓相关>重要>其他），由 runtime_scheduler 注册后台任务到点发送；system_error 与 bot 会话回复不受影响。
- [新功能] 转债信号桥（cb_signal_bridge）+ /操作单 命令：quant_research 以 current_plan.json 契约导出选债计划（含 T-1 收盘价，跨项目只走文件不 import），dsa 侧读取后经仓位上限/现金约束生成操作单；信号无报价（疑似强赎退市）显式示警，契约缺失诚实降级。
- [新功能] 操作单生成器 order_sheet_service：目标持仓 diff → 人可照抄执行的具体限价操作单（限价=T-1收盘、开盘越界放弃、14:50 未成交撤单），含现金贪心分配、先卖后买、文件账本状态机（pending→filled/cancelled）与 bot 渲染格式；先卖后买、整数手与费用假设复用量化研究侧模型。
- [新功能] 接线 position_sizer 三层仓位管理（此前零引用的孤儿模块）：新增 build_target_position_caps 把持仓快照接到风险预算/单票上限/总敞口三层约束上，含全局敞口二次裁剪与离线成本估值净值语义，为操作单生成器提供仓位上限。
- [文档] 新增执行蓝图 EXECUTION-BLUEPRINT.md：半自动交易助手终局形态与五阶段执行顺序（布线→数据地基→账本→结构性砍除→慢线），含验收总表与执行注意事项；作为后续执行顺序的唯一真源。
- [新功能] bot 新增 /持仓、/持仓登记、/清空持仓 命令：持仓登记落库（PortfolioService），查看与清空以对话完成，报告的持仓建议不再依赖口头描述。
- [新功能] CLI/定时分析主链路注入真实持仓上下文（per-stock portfolio_context_map）：LLM 的"持仓者建议"基于真实数量与成本；无持仓或读取失败时静默降级，不影响分析流程。

- [测试] 新增 Bot 指数入口 transport-independent 在线 E2E smoke（`scripts/smoke_bot_index_entry.py`）：worker 子进程经真实 `CommandDispatcher.dispatch_async` 提交并在同进程轮询 `TaskService` 贯穿到 `StockAnalysisPipeline`，父进程只负责 deadline、进程树清理（Windows `taskkill /T /F`、POSIX 杀进程组）与退出码（0=成功/1=失败/124=超时），输出单行 `E2E_EVENT {json}` 事件（`phase=submitted|completed|failed|timeout`）；smoke 覆盖 `SH.000016`/`上证50`/`930955.CSI` 矩阵，期望 code/name 取脚本内置权威映射（不信任响应/结果自报身份，提交 code mismatch 输出含期望值与实际值的显式错误并携带结构化实际 `stock_code`，dispatcher 路由错误也失败），矩阵外 target 与非正 `--timeout` 在提交与 spawn 之前即被拒绝（退出码 2），completed 须 exact canonical code、exact 注册名称且 `analysis_summary`/`operation_advice`/`trend_prediction` 非空（仅空白视为空），失败或结果不完整即非零退出；超时清理进程树（Windows `taskkill /T /F`、POSIX 杀进程组），清理成功输出 `timeout` 事件并退出 124、清理失败输出含清理错误的 `failed` 事件并退出 1，不回滚 DB/报告/通知副作用；用户 Ctrl-C 中止时父进程同样先清理进程树，清理成功透传中断、清理失败输出含清理错误的 `failed` 事件并退出 1，绝不静默吞掉清理失败；worker 意外异常输出 `failed` 事件并退出 1（stderr 保留异常证据，`KeyboardInterrupt` 不按普通失败处理），父进程将 worker 任意其他退出码归一化为 1（运行时契约只暴露 0/1/124）；父进程以内部 `--worker` flag 显式拉起子进程（不依赖环境变量，防外部预置绕过硬超时）；不 mock 在线依赖、不 dry-run、不修 transport，不加入离线 gate。
- [新功能] Bot `/analyze` 支持已登记指数入口：显式代码（`sh000016`）、CSI alias（`930955.CSI` 收敛为 `csi930955`）与注册中文名（`上证50`）均可提交，指数以结构化 `AnalysisTarget` 经 `TaskService` 贯穿到 Pipeline `process_single_stock`（`sh000016` 不再被改写为 `SH000016`）；注册名称查询独立于 parser identity alias（中文名不进入 `find_by_explicit_key`/`parse_analysis_target`），同名歧义返回明确错误并要求显式代码，未登记 CSI 与未知名称返回明确错误且不提交任务；个股代码（A/HK/US）保持既有 legacy code 路径不变，股票名称输入（如 `贵州茅台`）由本次 Bot 入口新暴露——复用既有名称解析器（`resolve_name_to_code`）解析后提交 legacy code，不携带结构化 target；提交成功响应在 `BotResponse.extra` 暴露内部任务 identity（`task_id`/`stock_code`）供在线验收等内部流程使用，文本与错误路径不变、平台适配器可忽略 `extra`。
- [新功能] 新增最小 Agent 轨迹评估入口 `evals/agent_trajectory/`(Refs #1956):纯函数指标层只消费真实 `tool_calls_log + AgentResult`,冻结最小指标契约(工具命中、冗余/缓存、失败/重试、总步数/max_steps),`run_eval.py` 经 `build_agent_executor` 真实执行并输出文本摘要 + 结构化 JSON 报告;评估为 reporter 非 gate,零 `src/` 改动

- [修复] 将 litellm 依赖窗口上界收敛到 `<1.99.0`：1.99.0 起把 `prompt_cache_key` 透传给 OpenAI provider，破坏 provider 缓存测试对不透传行为的既有断言（CI backend-tests 3/3 与 backend-gate 失败）；保留历史最低版本与 `!=1.82.7`/`!=1.82.8` 事故排除，同时同步更新各 LLM 兼容文档中写死的依赖约束表述，避免文档与 requirements.txt 漂移

- [新功能] 新增 `SEARXNG_TIMEOUT_SECONDS` 配置自建 SearXNG 单次搜索超时（默认 10 秒），已接线全部 SearchService 构造入口（含题材搜索子进程重建）与默认 GitHub Actions 工作流

- [修复] 美股日线路由现按各数据源当前优先级排序，单项 `*_PRIORITY` 配置（如 `YFINANCE_PRIORITY=0`）对美股即时生效；指数固定首选与 Longbridge preferred 语义保持不变

- [新功能] 新增个股研究聚合 API，以统一 canonical code 返回行情、历史、研究产物、资讯、缓存持仓关系和监控规则，并对每个块独立标记 fresh/partial/unavailable。
- [修复] 个股研究聚合拒绝交易所冲突的股票身份，在市场限定后无历史候选时保持空结果，兼容市场限定裸码与混合大小写旧数据，并从独立基本面快照补齐 ResearchArtifact 的财报与分红证据。

- [新功能] 支持通过 `main.py --stocks` 一次性分析已登记板块指数，自动使用指数适用的数据与分析能力，并保持报告、历史和决策信号兼容。
- [修复] `main.py --stocks` 在解析股票列表前先 best-effort 刷新股票索引注册表，保证首次运行能吃到刷新后的指数 alias/身份；刷新失败、超时或禁用不阻断分析。
- [修复] 交易日过滤对市场未知的指数 code（如 `sh000016`/`csi930955`/`930955.CSI`）按 `market=cn` 参与 A 股休市过滤，避免休市日指数被 fail-open 保留；市场仍未知的非指数 code 继续保留。
- [修复] 指数分析将实际命中的日线数据源归因保存到历史记录，并由 Dashboard/Brief aggregate 报告展示；来源无效时保持原有输出。
- [文档] 在中英繁 README 顶部关联 DSA arXiv 论文，并新增 `CITATION.cff` 统一项目引用信息。
- [新功能] 新增数据源能力与数据集质量只读契约，提供 `/api/v1/data/overview` 和 `/api/v1/data/capabilities`，为大盘看板、数据中心、个股详情和自选 2.0 统一暴露 provider capability、dataset quality 和 source priority。
- [修复] 统一美股指数实时行情与数据能力概览为 YFinance-only 路由，避免 YFinance 失败后误用 Longbridge fallback。
- [改进] PR CI 增加文档路径检测：仅修改普通文档、非治理 Markdown 或 LICENSE 时跳过后端测试分片、Docker、Web 与桌面打包，保留轻量治理和门禁汇总；契约文档、静态 API 规格与测试 fixture 仍执行后端回归。
- [新功能] 新增 ResearchArtifact 结构化研究产物契约，在 `AnalysisReport.structured_report` 中承载 Thesis、Evidence、Invalidation Conditions、Next Actions 和 Data Quality，并提供从现有报告生成结构化产物的后端 helper 与 Web 类型。
- [修复] 同步 ResearchArtifact 与 `AnalysisReport.structured_report` 到静态 OpenAPI，避免公开 API 规格与运行时契约漂移。
- [修复] Linux/Docker 分享图补齐 Noto CJK 字体与中韩文字体栈，避免 PNG 只显示数字和英文、中文或韩文内容消失。
- [修复] 股票名称归一：AkShare 部分名称带无意义内嵌空格（“五 粮 液”、“万  科Ａ”）与全角宽度拼写（“京东方Ａ” 的全角 Ａ），统一经 `_normalize_stock_name()` 做 NFKC 宽度归一 + 去空白（原 `_compact_stock_name` 仅去空白）：本地映射初值、`_build_name_map_from_df()` 构建与 `extend_AkShare()` 合并、磁盘缓存加载三个入库口（含归一后为空的条目守卫）与 `resolver_name_to_code_list()` / `is_known_stock_name()` / `resolve_name_to_code()` 三个查询入口同源归一；消除空格/全角拼写与本地无空格半角拼写比较不相等造成的假性改名别名，源形态输入（带空格/全角）与常规拼写同样可解析，覆盖 `/analyze` API、按名称导入与 Bot 文本解析等全部调用路径；全角拼写此前与分词管道 NFKC 归一后的半角输入（“京东方A”）永不相等——全名精确匹配落空且会被更短库内名误切出错误实体（“京东”），归一后半角/全角输入产出一致、展示名统一为归一拼写；磁盘缓存中的历史未归一数据（带空格/全角）经合并与加载入口自动归一，无需迁移。
- [新功能] Web Chat 意图识别层新增分词模块：`web_intent_tokenizer` 六步管道（多股票全名实体扫描 → 标点/空白切分 → 代码形提取 → 市场关键词 → 无歧义关键词 → 残存 gap 多策略 DFS 匹配）把用户消息切分为携带语义标签的 Token 序列；配套 `web_intent_types` 数据字典（Token 结构、Market 枚举、21 个语义 tag、clean/extend 双词池与正则机器）。核心原则"宁可不做，不可做错"：Step 1~5 只做精确匹配，Step 6 要求整段 TAG 全覆盖（交叉验证）才产出，未覆盖片段保持空 tag 交下游 LLM 兜底；代码形 token 辨认为 `stock_code`（附 code/name/market 三元组）/ `wrong_{market}_code` / `unknown_{market}_code` 三态，token 层代码拼写统一 canonical 归一（a=6 位裸数字、hk=HK+5 位、us=大写 ticker）。意图枚举与意图识别结果随后续 `web_intent_resolver` PR 引入。新增 183 个分词单元测试。
<!-- 新条目格式：- [类型] 描述（类型取值：新功能/改进/修复/文档/测试/chore）-->
<!-- 每条独立一行追加到本段末尾，无需分类标题，合并时冲突最小 -->
- [新功能] 完善 Futu OpenD 港股数据源接入：系统设置支持 OpenD 地址、端口和港股实时数据源优先级，保留 Longbridge、AkShare、YFinance fallback。
- [测试] 增加 Futu 配置 schema、港股实时路由和 fallback 契约覆盖。

- [新功能] 建立唯一、可生成、可校验、可降级的指数身份注册表：由 `scripts/stock_index_seeds/index_registry.csv` 的 31 项 manifest 确定性合并进 `apps/dsa-web/public/stocks.index.json`，运行时唯一真源为 JSON 中通过校验的 `active=true`/`assetType=index` 行，移除 `stock_list_parser` 的 5 项硬编码白名单；支持 `--index-only` 生成与字节稳定输出。
- [新功能] 补齐显式 SH/SZ/CSI 指数 alias 收敛与 CSI 身份：`sh000300`/`000300.SH`/`sz399300`/`399300.SZ`/`000300.CSI` 均解析到 `sh000300`，`csi930955`/`930955.CSI` 解析到 `csi930955`；未登记 `.CSI` 输入返回 `unsupported`；裸数字恒为 stock 并仅通过 `matched_index` 暴露歧义。
- [新功能] 数据管理器按 SH/SZ/CSI 支持矩阵映射 provider symbol：CSI 仅 AkShare 支持（`csi{code}`），Tencent/TickFlow/Yahoo 返回空 symbol 并记录 `unsupported` provider-run，不触发指数健康熔断。
- [改进] 存储层 `_derive_canonical_id` 统一为 parser 推导（裸码=stock、显式指数=index），并新增幂等分批修复历史裸码错误 canonical 串桶（`000001`/`000016`/`000688`/`930955` 等），显式指数行与正确 stock 行不受影响，registry 为空时修复 no-op。
- [改进] Web loader 完整解压含 index 的共享 payload，但在返回给 autocomplete/popular/group 消费面前过滤 `assetType=index`，股票与 ETF 行为保持不变。
- [测试] 为生成器、loader、parser、provider 路由、存储修复与 Web 门槛补充 TDD 回归锚点。
- [修复] PR #2267 review 收敛 CSI 显式身份：将 `csi` prefix（canonical）与 `.CSI` suffix（显式 alias）在 parser/build/runtime 规范化器中分离，未登记显式 `csiNNNNNN`/`NNNNNN.CSI` 一律返回 `unsupported`（不再落入美股或猜测 SH/SZ），并防止未登记 `csi000300` 被等价成已登记 `000300.CSI` alias；存储 `_derive_canonical_id` 对 unsupported 输入返回 NULL，避免进入持久化 canonical 桶。
- [修复] 在 seed、build entry 与 runtime candidate 三层严格校验 alias 唯一性与整数 popularity：NFKC/casefold 等价 alias 跨条目冲突被拒绝（无静默覆盖），非负整数之外（小数/布尔/负值/字符串）popularity 一律拒绝，整数 `100` 保持有效。
- [修复] 收敛已登记 CSI 显式身份在 resolver、任务去重键与历史候选中的分裂：`csi930955`/`930955.CSI`/`CSI930955` 统一解析为 parser canonical `csi930955`，未登记 `csi930956`/`930956.CSI` 保持既有降级语义；`is_code_like()`、REST/watchlist 输入边界与完整 Pipeline 透传不变。
- [修复] 阻止任意更新的非 bundled 指数候选（含 legacy `static` 子集）在 remote 缺失/损坏时以 active-index 子集覆盖 bundled baseline：所有非 bundled 候选必须为 bundled active-index canonical 集合的合法超集，否则回退 bundled 并记录 WARNING。
- [新功能] 桌面端全局右上角增加更新入口，与设置页共用更新状态；普通浏览器 WebUI 不展示，且不会在挂载时重复触发后台检查。
- [修复] 桌面端右上角更新入口与设置页共用检查中状态，避免一侧检查时另一侧仍可重复触发 GitHub Releases 检查；主进程手动检查路径同步增加 in-flight 防重。
- [新功能] Web 自动补全与搜索放行已登记指数（注册中文名/`sh`/`sz` 前缀/`csi`/`.CSI` 显式形式均可检索与提交），热门候选仍仅股票；`/analyze` API 对显式指数输入构造结构化 `AnalysisTarget`（INDEX 用 `canonical_id` 去重，与同码个股互不折叠，CSI alias 收敛），未登记 CSI 单请求返回明确 4xx、异步批量仅该目标进入响应 `rejected` 列表；指数 target 经任务队列贯穿到 Pipeline `process_single_stock`，`DecisionSignal` 以 `market_override="cn"` 落库并有真实分支测试。
- [修复] `/analyze` 在解析前按 strip 后非空原始 token 数限制 50 上限，rejected/duplicate token 也计入，防止用拒绝或重复 token 绕过批量上限；以唯一 `is_single = len(stock_codes) == 1 and not rejected_entries` 统一驱动 metadata、409 与单任务 202，duplicate+rejected 混合批次不再误返 legacy 409（单 duplicate 仍 409、部分 rejected 仍 202、全 rejected 仍 400）。
- [新功能] 报告 meta 补充可选 `asset_type`（`stock`/`index`），由后端 canonical code 经 `parse_analysis_target` 生成权威类型；指数报告在 Web 报告页与 Chat 自选入口隐藏 stock-only 自选操作，裸同码股票（如 `000016`）保持股票行为，market review 与旧客户端可缺省。
- [修复] Web 批量分析把 accepted/duplicates/rejected 三类计入确认数，含 rejected 的完整 chunk 继续提交下一 chunk 而非误报 incomplete，最终以 warning 展示拒绝数量与首个拒绝原因（中英文文案）。
- [修复] 历史筛选/删除/计数与 stock-bar 对已登记指数按 parser canonical 身份隔离：`sh000016`/`SH000016`/`000016.SH` 等显式形态互相可达（lowercase canonical + uppercase legacy canonical + 显式 alias），但永远不命中同码裸股票，裸码查询也不会命中指数记录；同一指数多条显式形态旧记录在 `/history/stocks` 合并为一行并计数全部形态，无记录删除仍返回 `deleted=0`。SH/SZ/CSI 共用同一 parser 分支，股票 alias、港股与海外市场行为不变。历史列表、历史详情与 stock-bar 对已登记指数（含旧 uppercase/显式 alias 持久化记录，如 `SZ399300`、`000300.CSI`）的 API `stock_code` 一律输出 parser canonical（`sh000300`/`csi930955`）。
- [改进] 任务列表/SSE 事件、历史列表项与 stock-bar 项追加可选 `asset_type`（`stock`/`index`）：任务侧从已提交的 `analysis_target` 透传（不重新猜测），历史与 stock-bar 侧由持久化 `record.code` 经 `parse_analysis_target` 生成；旧客户端与 market review 可缺省，字段可选追加不破坏既有契约。
- [修复] Web 首页与自选工作区改用资产感知身份键：任务/报告/历史的 `assetType` 优先，且被后端保证为 parser canonical 的代码只做**大小写折叠**（`SH000016`→`sh000016`），禁止再用前缀/后缀正则猜 canonical（否则 `000300.CSI` 会被误猜成 `csi000300`、`sz399300` 被误当成独立 canonical，违反注册表唯一判型真源）；仅 watchlist 原始字符串缺少类型时，才用已加载 `stocks.index.json` 的 `assetType=index` 行 canonical/display/显式 alias 精确命中（不先 normalize、不用前缀正则猜测；加载期间禁用批量分析，加载失败或请求超过 10 秒时按既有股票语义 fail-open）；行选中、active task 与完成自动选中按资产类型分桶，`sh000016` 指数行与 `000016` 股票行状态独立，完成后自动选中正确 canonical 指数报告。
- [修复] Chat 消息恢复、发送与报告追问对显式 SH/SZ/CSI 已登记指数统一按注册表 canonical 判型并覆盖默认 LiteLLM 与 Codex：所有 Chat 后端首帧加载 registry，加载期间延迟 URL/历史恢复并禁用发送，settle 后 `sh000016`/`sz399001`/`000016.SH`/`930955.CSI`/`csi930955` 只产出一个 lowercase canonical；后端 `resolve_stock_scope`、工具守卫与 cache key 复用 parser `INDEX` 身份，保持指数与裸同码股票隔离；未登记、空 registry 或加载失败时维持既有股票 fail-open，绝不按前缀猜指数。

## [3.31.0] - 2026-08-23

### 发布亮点

- feat: Agent 工具调用新增按类别和单工具配置的超时契约，并补齐防重试、协作取消、并发预算与热重载一致性。
- feat: 股票名称解析、`canonical_id` 双写和 A 股指数多数据源路由共同完善股票身份与行情降级链路。
- improve: 新闻检索为空时在报告中如实披露证据边界，Anspire 默认覆盖全球区域，公共 SearXNG 实例改为显式启用。
- fix: 定时任务恢复、无报告失败反馈、通知发送和大盘复盘历史/诊断信息进一步收敛，减少静默失败与误导展示。
- security: 桌面端升级 `builder-util-runtime`，修复 CVE-2026-54673 涉及的重定向凭据头信息泄露风险。
- docs: 增加 xAI Grok 的 LiteLLM 配置示例、Grok Bot 集成说明与可复用 Skill。

### 变更明细

- [修复] 大盘复盘历史列表与详情统一展示持久化短摘要；旧记录缺少摘要时从完整 Markdown 生成无内部标记的纯文本节选。
- [修复] 大盘复盘按实际执行的生成后端和模型记录诊断，避免 Codex CLI 或 fallback 被误显示为配置模型。
- [修复] 已配置钉钉 Webhook 时不再误报“未配置通知渠道”；钉钉 Stream 仍仅用于交互，不作为定时静态推送渠道。
- [修复] WebUI/API/Desktop 以 `--serve-only` 重启后会恢复已启用的定时任务，同时保持启动时不立即执行分析；通知路由示例补充钉钉 Webhook 渠道。
- [改进] AIHubMix 注册与引流链接统一使用 inferera.com，改善中国大陆网络直连体验。
- [新功能] 回测指标新增风险调整收益：夏普比率、年化夏普、最大回撤、卡尔玛比率、盈亏比、平均盈亏、年化收益率与累积收益率，并在前端 PerformanceCard 展示。
- [改进] BacktestSummary 表新增 9 个风险指标列，附带 SQLite ALTER TABLE 自动迁移。
- [新功能] 新增三层仓位管理服务 position_sizer.py：基础仓位（风险敞口）× 账户净值动态调整 × 机会分类风险映射，支持全局总仓位上限与配置化注入。
- [新功能] 新增策略自动停止服务 strategy_autostop_service.py：回撤超阈值或连续跑输基准时暂停策略并发送告警，状态持久化到 strategy_autostop_state 表，支持手动暂停/恢复与自动恢复两种模式，active→paused 状态变化时去重通知。
- [新功能] 新增策略自动停止 API（/api/v1/strategy-autostop）：含评估、状态列表、单个状态查询、手动暂停、手动恢复 5 个端点。
- [改进] config.py 与 .env.example 同步 position_sizer 与 strategy_autostop 相关配置项。
- [chore] .gitignore 补充 apps/dsa-desktop/dist-v*/ 与 old-installers/ 忽略规则，避免历史打包产物入库。
- [新功能] 新增独立量化研究子项目 quant_research/（P0 骨架）：配置层（Hydra + Pydantic）、数据层（Tushare/Baostock/AKShare 多源 fallback + Parquet 缓存 + 退市股 universe 还原）、下载脚本 download_data.py 与单元测试，与主项目解耦。
- [chore] .gitignore 为 quant_research 子项目加例外：保留 data/ 目录结构入库，仅忽略 cache/ 与 raw/ 大文件缓存。
- [改进] quant_research 转债 PIT 覆盖率 39.71%→100%：新增 scripts/backfill_cb_from_akshare.py（AKShare 退市券补采，41 只 iFinD 缺失券全部入库），PIT 重组为 59,609 行/68 只/0 脏行，双低回测幸存者偏差消除。
- [新功能] quant_research 新增 scripts/build_feeds_snapshot.py：ETF 冻结快照构建工具（强制源选择、基线 diff、收益率断点扫描校验）；universe 由 13 只扩展至 17 只（中证2000/红利低波/黄金/货币停靠）。
- [修复] quant_research 规避 tushare ETF 日线不处理份额分拆的数据缺陷（分拆日假跳变 ±50%~300%），快照构建统一走 AKShare 东财口径。
- [修复] quant_research DataLoader ETF 数据链改为 akshare 优先，并清除被 tushare 假断点污染的 ETF hfq 行情缓存。
- [改进] quant_research PIT schema 扩展至 8 列（新增 maturity_date/issue_size 静态字段，100% 覆盖）；转债 meta 的 list_date 修正为真实上市时间，backfill 脚本新增 --enrich-only 模式。
- [改进] quant_research 固化月频基线配置 etf_mwu_cb_monthly.yaml；完整 universe（68 只）复核回测显示月频基线 -3.45%（幸存者偏差修复后），修正旧 +0.18% 的失真结论。
- [新功能] quant_research 转债双低专家 v2 生命周期过滤（转股价值上限/临期排除/价格硬顶，默认关闭）；universe 扩至 854 只全量发行券（PIT 624,637 行/849 只/每日中位 362 只候选）。
- [新功能] quant_research 新增候选并集预计算 scripts/precompute_cb_union.py 与 run_backtest --cb-symbols-file，全量池回测 feed 由 849 只压缩至 276 只。
- [改进] quant_research 全量池+v2 过滤月频基线 -1.44%（68 只池 -3.45% → 净改善 2pp）；实证"过滤规则必须在足够大的池上运作"（68 只池上 v2 掏空候选致 -29.21%）。
- [新功能] quant_research Walk-Forward 支持 etf_mwu_cb 策略（冻结三输入 + 三 SHA、网格键自动路由专家、测试指标按测试窗裁剪）；新增 CB 日线冻结快照脚本 build_cb_feeds_snapshot.py 与候选并集全参数化（网格超集原则）。
- [chore] .gitignore 补 quant_research/data/pit/raw/ 忽略规则（原规则漏覆盖 914 个原始缓存目录）。
- [测试] quant_research MWU+转债 8 折 Walk-Forward：4 组合 OOS 全负（-1.65%~-13.2%），训练/测试一致性仅 2-3/8，按预注册纪律不接入实盘；fold 8 巨亏根因为池萎缩集中风险（已诊断，候选缓解留待下轮）。
- [新功能] quant_research 转债双低专家新增 min_candidates 池规模下限（候选不足时专家让位现金；过滤链抽取为单一来源 _filter_double_low_pool helper）。全局验证 4×8 折：OOS 全部改善、最大回撤 21.15%→9.89%、正常期干扰 |Δ|<1.8pp，规则采纳为新基线（阈值结构值 top_n×3，未网格扫）。
- [文档] quant_research 最终战略方案定稿：全品类广域调研后确立三层小资金多策略体系（第 1 层事件套利监控+转债打新提醒 / 第 2 层被动底仓 / 第 3 层 WF 验证通过的进攻策略），数据源经实测验证（全市场公告关键词过滤命中真实要约收购案例）。详见 HANDOFF.md 第 4 节与架构研究文档 6.2.6 节。
- [新功能] 新增市场监控后台任务（schedule 模式）：事件套利监控（扫描全市场公告中的要约收购/现金选择权/私有化/主动退市事件，附巴菲特四问核查表）与转债打新日历（申购/中签缴款/上市首日三段式提醒）。配置项 EVENT_ARBITRAGE_MONITOR_ENABLED / BOND_NEW_ISSUE_MONITOR_ENABLED / MARKET_MONITOR_INTERVAL_MINUTES，含持久化去重与网络失败兜底，17 项单元测试覆盖。
- [改进] 转债打新日历新增 tushare 备源（cb_issue 申购日期 + cb_basic 上市日期/简称），AKShare 主源失败时自动降级；实测 1135 条发行记录与主源近期券申购/上市日期全部一致（YYYYMMDD/YYYY-MM-DD 双格式由 _parse_date 统一兼容）。
- [改进] 事件套利监控关键词新增"吸收合并"（私有化/合并退市标准措辞），同时排除"吸收合并全资子公司"类母公司内部重组噪声，降低误报。
- [新功能] quant_research P2 回测层：封装 Backtrader 引擎（BacktestEngine + BacktestResult），实现 A 股佣金/印花税/过户费/最低5元、T+1 限制、涨跌停过滤（主板10%/创业板科创板20%/北交所30%），内置 SMA 双均线示例策略与 run_backtest.py 脚本。
- [修复] quant_research 回测佣金计算：CommInfoBase 缺少 percabs=True 导致 backtrader 将 commission 从绝对小数再乘 0.01，费率缩小 100 倍；同步修复 TradeAnalyzer 盈亏比提取（pnl.total 而非 pnl.net.total）。
- [测试] quant_research 新增回测引擎测试 test_backtest.py（21 项）：覆盖佣金计算、T+1 限制、涨跌停过滤、涨跌停幅度判定、引擎完整流程与边界场景。
- [chore] 仓库目录重构：主项目内容（api/apps/bot/data_provider/docker/docs/scripts/src/strategies/templates/tests/main.py/server.py 等）移入 daily_stock_analysis/ 子目录，与 quant_research/ 并列；仓库根保留 .github/.claude/AGENTS.md/CLAUDE.md/LICENSE/.gitignore 等治理文件。
- [改进] 同步更新 CI workflows（6 个文件加 working-directory: daily_stock_analysis 或路径前缀）、AGENTS.md 路径引用、.gitignore 锚定路径、check_ai_assets.py/ci_test_shard.py/ci_gate.sh 脚本的 ROOT 解析与 .github/ 路径引用。
- [改进] 目录重构路径修复（第二轮）：修复 ai_review.py/build_release_notes.py/generate_notification_actions_env_table.py 脚本路径；.env.example 移入 daily_stock_analysis/；更新 pr-review.yml isReviewable 逻辑、copilot-instructions.md、instructions/*.md applyTo glob、PULL_REQUEST_TEMPLATE.md 路径引用；修复 README.md 和 docs/ 下 8 个文档的快速开始命令链与失效链接；修复 8 个测试文件的 .github/ 路径解析与 CI 契约断言。
- [修复] 单股推送模式在未配置通知渠道时仍会落盘本地个股报告；CLI 启动分析若因空股票列表、个股结果全失败或本地报告保存失败而未生成报告，会显式返回失败并记录原因。
- [修复] 合并推送模式下即使个股汇总报告落盘失败，仍会先发送已有的合并通知；仅启用大盘复盘但最终未生成任何复盘内容时，分析任务会显式返回失败。
- [修复] 修复 DatabaseManager.delete_analysis_history_records 定义写事务后未实际执行的问题：删除历史记录功能失效并使删除接口返回 500，同时影响数据清理统计与观点样本孤儿数据清理。
- [chore] 修复 CI 后端依赖清单 .github/requirements-ci.txt 在目录重构后的相对路径引用（../requirements.txt → ../daily_stock_analysis/requirements.txt），backend-tests 依赖安装不再失败。
- [修复] 修复 Web 设置页 API 测试面板在渲染期间调用 performance.now() 触发 react-hooks/purity lint 错误导致 web-gate 阻断的问题，总耗时改为已完成测试耗时求和。
- [测试] test_pipeline_single_stock_notify 断言的报告文件名由硬编码日期改为动态当天日期，消除跨日运行失败。
- [修复] history_service.py 在 TYPE_CHECKING 块中补充 RunFlowSnapshot 导入，修复 flake8 F821 未定义名称导致 backend-gate 阻断的问题。
- [新功能] quant_research P1 ETF 动量轮动策略（ETFMomentum）：双动量（绝对+相对）+ 年线过滤 + 流动性过滤 + 周度调仓 + 100 份整手下单，策略参数与 universe 独立配置（config/strategy/etf_momentum.yaml），download_data.py / run_backtest.py 支持 --strategy-config。
- [改进] BacktestEngine 支持多标的日期对齐（晚上市 ETF 早期 NaN 填充，由策略层过滤）与 commission_overrides 费率覆盖；ETF 回测免印花税与过户费。
- [修复] AKShare ETF 数据新增新浪源兜底（fund_etf_hist_sina）：东财源在部分网络环境 TLS 失败时自动降级，并检测 ETF 份额折算跳变（拆分/合并，如 512480 单日 -50%）做前复权调整，消除动量/均线计算的致命失真。
- [修复] Parquet 缓存 get_bars 增加区间覆盖校验（容差 10 天）：缓存起点晚于请求起点或终点早于请求终点时视为 miss 触发重新下载，消除"请求 2013、静默返回 2015 起切片"的截断问题。
- [改进] run_backtest.py etf_momentum 数据加载起点自动前移预热期（最长窗口 ×1.7 日历日），回测起点不再因均线窗口数据不足而空转。
- [测试] 新增 ETF 份额折算调整测试（5 项）与缓存覆盖校验测试（2 项），quant_research 测试全量 83 项通过；12 只 ETF 11.5 年真实数据端到端回测验证。
- [新功能] quant_research P2 策略增强：ETFMomentum 支持多窗口动量、波动率调整排序、止损带与国债 ETF 防御资产轮动（含债券 ETF 10 张申报单位、买入手数 2% 现金缓冲）。
- [新功能] 新增参数网格扫描脚本 run_grid.py：笛卡尔积组合 + 训练/测试分段（--split-date）+ 数据快照冻结（--snapshot）+ 组合预算上限防过拟合。
- [修复] 回测引擎多标的对齐时对标的存续期内缺失交易日（数据洞）前向填充：此前 NaN 收盘价使持仓按 0 估值，产生单日 -95% 级伪回撤并全面扭曲参数扫描结论（含回归测试）。
- [修复] run_grid.py 无 --split-date 时 full 段未按起止日期切片，训练段扫描实际包含测试期数据（未来函数泄漏）。
- [测试] 同步 test_config 对 universe 13 只（含 511010 防御资产）的断言；quant_research 测试全量 89 项通过。
- [文档] 架构文档新增 6.2.2 P2 参数优化验证记录：OFAT/组合扫描/一次性样本外验证均按纪律执行，调优参数在 2022-2026 测试段失效（年化 -15%），不予采纳，维持 P1 默认参数。
- [新功能] quant_research P3 多策略融合（MWU，Multiplicative Weights Update）：三类专家（动量 / 均值回归 RSI2+MA200 / 国债防御）+ 乘性权重更新 + Fixed Share 回拉防权重锁死，策略配置 config/strategy/etf_mwu.yaml。
- [新功能] quant_research P4 执行模块：AbstractBroker 统一券商抽象 + PaperBroker 模拟账户（T+1/手续费/滑点）+ TDXJKBroker 通达信交易 DLL（TJyaid.dll，9 个导出函数 ctypes 绑定）；离散整数手分配 sizer（债券 ETF 10 张/手，买入现金约束等比缩减）；执行引擎 Executor（先卖后买/5% 容忍/交易时段风控/拆单预留）；实盘 MWU 信号层 LiveMWU 复用回测专家。
- [新功能] 新增实盘运行入口 scripts/run_live.py：paper 模拟（默认，全链路演练）/ tdxjk 实盘两种模式，单次或定时轮询调仓。
- [修复] quant_research 执行层两处缺陷：同 bar 多买单按差额升序提交（先提交大额单会挤占现金导致连环 Margin 拒单，实测拒单率 ~1/3）；离散分配器卖出回笼必须遍历 current（被清仓的非目标标的也要计回笼，否则误判现金不足把新目标缩成 0）。
- [测试] quant_research 新增执行模块测试 test_trade.py（17 项）与 test_mwu.py（18 项）：PaperBroker 撮合/费用、整数手分配、交易时段风控、先卖后买、LiveMWU 权重随胜负更新；全量 124 项通过。
- [文档] 架构文档新增 6.2.3 P3/P4 验证记录：MWU 融合方法论（知乎执行模块文章 + 评论区修正）、执行模块架构、tdxjk 通达信接口调研结论、Paper 全链路实测（MWU 选出 3 只动量 + 国债防御，4 笔委托成交）。
- [新功能] quant_research P5 可转债双低轮动策略：双低评分纯函数（价格+转股溢价率×100，含评级/溢价率上限/价格中位数/次新债过滤）、ConvertibleDoubleLow 回测策略（快照注入 + top_n 等权轮动 + 10 张/手申报单位 + 月频调仓）、可转债列表/日线数据通道（AKShare 当日快照 + Tushare cb_daily 日线）。
- [修复] run_backtest.py 可转债双低分支把 yaml 中策略类不认识的参数（candidate_pool/exclude_force_redeem 等选债过滤项）直接传给 Backtrader 导致 TypeError；现按策略 params 白名单过滤后再传入。
- [测试] quant_research 全量 137 项通过；可转债双低 2026-01~08 真实数据回测跑通（5 只持仓整 8 个月未轮出，0 笔平仓，-3.97%/回撤 7.72%），验证数据通道与策略执行链路正常。
- [新功能] quant_research 新增 `etf_mwu_cb` 四专家组合回测入口，运行期合并 ETF 与可转债日线并注入双低快照，支持动量/均值回归/可转债双低/国债防御专家融合。
- [修复] ETFMWU 按标的类型区分申报单位：可转债 10 张/手、国债 ETF 10 张/手、普通 ETF 100 份/手，避免混合组合把可转债按 ETF 手数下单。
- [测试] `etf_mwu_cb` 真实回测（2026-01~08）跑通：最终资金 9,373.01 元、总收益 -6.27%、最大回撤 15.27%、0 笔已平仓 round-trip；quant_research 全量 145 项通过。TradeAnalyzer 缺失节点的 KeyError 已修复，订单数与换手统计仍需独立订单级分析器。
- [修复] BacktestEngine 对 TradeAnalyzer 缺失 `closed/won/lost` 节点做安全读取，持仓到期末的回测不再触发交易统计提取异常。
- [新功能] BacktestEngine 新增订单级分析器，统计已成交订单、买卖成交额、手续费、拒单/保证金不足/撤单和区间换手率，并在回测摘要中展示。
- [新功能] 新增严格 PIT 可转债历史快照契约与 `PITConvertibleDoubleLowExpert`，`--pit-snapshots` 仅接受含 `as_of/symbol/price/premium_rate/rating/list_date` 的 csv/parquet/pkl 文件，缺少 PIT 数据时不回退到当日快照。
- [测试] `etf_mwu_cb` 订单级复核得到 105 笔成交订单、78,282.17 元成交额和 7.87x 区间换手率；仓库当前没有完整 PIT 快照文件，长期 PIT 训练/测试回测暂不输出收益结论。
- [新功能] quant_research 新增 iFinD 可转债 PIT 采集链路：`IFinDClient`（HTTP 直连 bond MCP，token 从 `IFIND_AUTH_TOKEN` 环境变量读取）+ `build_pit_snapshot.py`（按单券×单年分批，截断后下载全量 CSV；历史评级/上市日期按表头定位解析并前向映射；断点续跑、空 meta 不缓存）。
- [新功能] 新增 `validate_pit_snapshot()`：硬性校验字段/空表/`as_of` 可解析/无未来数据泄漏，软性统计每日期标的数量、评级覆盖、未来上市行等，输出 coverage 报告。
- [测试] 生成真实 PIT 快照 `data/pit/convertible_pit.parquet`（2021-01~2026-08，10,675 行、1,356 交易日、8 只转债、评级覆盖 100%、无未来数据）；`etf_mwu_cb` 真实 PIT 长期回测：测试段 2022-01~2026-08 总收益 -0.62%、最大回撤 26.38%、35 笔成交、换手 3.40x；长窗口 2021-06~2026-08 总收益 -7.09%、最大回撤 31.76%、115 笔成交、换手 9.04x。universe 偏小且有幸存者偏差，不构成盈利结论。
- [测试] quant_research 全量 164 项通过（新增 iFinD 解析 / PIT 校验 15 项，覆盖 CSV/markdown 解析、评级列序不固定、未来泄漏拒绝）。
- [新功能] build_pit_snapshot.py 支持 `--assemble-only`（仅从 raw 缓存重建 parquet，不发网络）；年份切块按上市日期裁剪；`parse_daily_csv` 对列宽不一致的坏 CSV 跳过坏行、缺列返回空表，采集循环对单只 try/except 隔离（已退市券不再导致整批崩溃）。
- [测试] 扩容真实 PIT 池：universe 从本地 8 只扩到 2019-2021 上市现代券 27 只（日均候选 14.22 只、26,172 行、2018-2026 窗口、评级覆盖 100%）；实证 2007-2013 老券 iFinD 无转股溢价率、仅现代券可用；`etf_mwu_cb` 真实 PIT 回测：周频长窗 2020-2026 总收益 -38.52%/回撤 55.86%/换手 22.16x，**月频**长窗 +0.18%/回撤 25.89%/换手 12.13x、测试段 2022-2026 -0.67%/回撤 17.59%/换手 4.75x——调仓频率是 1 万本金下决定性成本变量。
- [测试] quant_research 全量 165 项通过（新增坏行回归等，覆盖 `on_bad_lines` 与缺列空表行为）。
- [改进] 可转债 PIT 覆盖率报告增加原始采集缺失分类和每只有效数据的首末观测日期，避免将数据缺口误判为退市或到期。
- [新功能] 新增 ETF 动量滚动 Walk-Forward 验证入口：每折只用训练窗选参并锁定后运行测试窗，输出冻结快照 SHA-256 与逐折样本外指标。
- [测试] quant_research 全量 170 项通过，覆盖 PIT 原始采集审计和滚动 Walk-Forward 参数锁定。
- [文档] 2026-08-28 广域调研（知乎 API 12 组 + 论文 + GitHub + 社区实盘）：确认 MWU regret 界为相对保证、2025-2026 转债存量坍缩至 5578 亿/双低中位数 >170 高估（fold 8 失败为周期尾段 regime 现象）、PBO/CSCV/DSR 可从现有 WF 数据近零成本升级；产出改进计划 P0-P3（.scratch/quant-improvement-plan-20260828.md）。
- [文档] 2026-08-29 架构调研与彻底重构方案：六框架深读（qlib/rqalpha/vnpy/vectorbt/LEAN/Nautilus）+ 平台 API 与 xtquant 实盘通道；定稿六层蓝图与 R1-R5 渐进迁移路径（.scratch/quant-refactor-plan-20260829.md）。
- [新功能] quant_research 新增 core 包：MarketView/BarSeries 数据视图协议（适配方向反转，协议真源不再模仿 backtrader line）与 MWUCore 融合核（回测 ETFMWU 与实盘 LiveMWU 共用唯一权重更新实现）；合成数据等价性对拍逐位一致。
- [新功能] quant_research 新增 RuleBook 交易规则表（core/rules.py）：品种 × 日期 → 费率/涨跌停/T+1 查表，含印花税 2023-08-28 减半、创业板 2020-08-24 注册制 10%→20%、创业板/科创板 ETF ±20%、转债 2022-08-01 起 ±20% 等历史口径。
- [改进] TPlus1Broker 升级为执行路径强制闸：涨停不可买/跌停不可卖/T+1 品种当日买入不可卖出在 broker 提交路径拒单（此前为被动组件且无策略调用，规则实际未生效）；买入成交日期由 broker 自动记录。
- [改进] MWU loss 映射修正为理论一致的线性映射（ρ=clip(r/scale,-1,1)，ℓ=(1-ρ)/2），空选专家按现金收益 r=0（ℓ=0.5）参与更新，消除"盈利冻结权重/空选豁免"的语义漏洞（P0-1）；MWU+CB 路线 t5p20 8 折 OOS 由 -0.89% 变为 -0.50%，定性结论不变（不接实盘）；ETF 动量路线不经过 MWU 层、逐折无影响。
- [修复] run_live.py `_load_price_window` 返回 dict 与 LiveMWU.update 的 tuple 解包契约不匹配（paper 模式必崩），统一为 {symbol: (closes, amounts)}。
- [改进] 过户费默认值修正为 2022-07 降费后口径 0.001%（原 0.002%）；止损测试场景改用物理可行的 -9.5%×2 连续暴跌（原 -20% 单日超出主板跌停，在强制闸下不成立）。
- [测试] quant_research 全量 218 项通过（新增 MWUCore 算术/RuleBook 查表等 27 项，支撑后续 R3-R5 重构与 P1 策略实验）。
- [新功能] quant_research 重构 R3-R5 完成：Runner 编排层（参数表/网格/warmup/切片/锁参准则单一来源，WF 输出逐字节一致）、实验注册层（run_id + index.jsonl，试验数 N 机器可追溯供 DSR 校正）、Panel 截面矩阵与因子层（命名注册因子 + RankIC/IC 评估）、两段式转债回测（PIT 全量池矩阵选债 → 仅被选券 feed 执行模拟，废除候选并集预计算）。
- [修复] 两段式回测暴露共享执行逻辑隐性 bug：目标外 1 手持仓因"超额×0.98 取整"永远无法卖出（小资金每仓恰好 1 手 → 永不换仓 → 现金耗尽），修正为目标外整仓卖出（与 ETFMomentum 的 close() 全平语义对齐）；PIT 合成 feed 对退市券价格前向填充至区间末端（否则 NaN 估值防御把持仓计为 0、净值塌方）。
- [测试] 两段式新基线（转债双低月频 v2 过滤，2020-2026，1 万本金，收盘价近似口径）：+15.28%/6.6 年（年化 +1.85%）、回撤 25.30%、成交 190 笔、换手 52.12x——新管线不可与旧 MWU 组合基线逐位对比，作为独立基线记录。
- [测试] 因子 IC 报告（849 只全量池 2019-2026）：双低因子 RankIC -0.05~-0.06、|t| 12.8-16.6（远超 HLZ t>3 门槛）且周期越长越强——横截面预测力统计显著，为"WFO 负结果源于成本/regime 吞噬而非信号缺失"提供证据；单独溢价率因子不显著（RankIC≈0）。
- [测试] quant_research 全量 237 项通过（新增 Panel/因子 IC/两段式 14 项）。
- [新功能] quant_research P0-2 过拟合审计工具：CSCV/PBO、Deflated Sharpe、HLZ t 门槛（无 scipy 依赖）；预注册修订有记录——固定矩阵互补恒等式诅咒实测推翻两次预期，PBO 降级为诊断参考，主门槛为 DSR<0.5 + HLZ |t|>3；run_overfit_audit.py 宽矩阵审计入口（诚实试验数 N 显式输入）。
- [测试] ETF 动量路线审计（18 配置×3387 日，N=44）：DSR 0.6447 通过主门槛（非纯噪声），但 HLZ 日频 t 全部 18 配置不显著（最高 1.06≪3）——无统计显著日频预测力，叠加 WF OOS 全负维持不实盘。CB 两段式路线审计（6 配置，N=10）：DSR 0.7363 通过，HLZ 同样全不显著（最高 1.78）。
- [测试] P1-1 温度计门控预注册验证不采纳：8 折 WF 门控版 OOS -13.66% vs 基线 +1.94%（Δ-15.6pp）——2023-2025 双低中位数几乎全程处于 140-171 中间带（三年砍半仓位砍掉正收益），2026 全程清仓但 fold 8 仅改善 0.4pp（亏损源于 ETF 腿与存量持仓）；教训：静态绝对阈值施加在漂移市场统计量上 regime 脆弱，相对阈值留待未来独立预注册。
- [测试] quant_research 全量 250 项通过（新增过拟合审计 10 项、温度计门控 3 项）。
- [改进] 事件套利监控加厚（P2-1）：公告标题提取要约价并获取现价计算价差，价差 ≤0 自动降级为"投机盘，非套利"标注（假套利排除）；推送新增事件检查清单（前置生效条件/审核中止立案/部分要约比例/时间成本）与格雷厄姆年化公式骨架；抢权配售类公告排除出套利信号。
- [新功能] 转债打新日历新增配债登记阶段（P2-2）：申购日前一交易日提醒持有人可优先配债，显式标注抢权正股敞口风险（社区教训：龙建股份抢权正股 -9%）；原三段式语义与消费方保持兼容。
- [新功能] 新增 ETF 溢价率买点监控（P2-3）：底仓（默认 511380 转债 ETF）溢价率超过阈值（默认 1.5%）时提醒暂缓买入，帮助执行低溢价分批建仓纪律；新增配置 ETF_PREMIUM_MONITOR_ENABLED / ETF_PREMIUM_SYMBOL / ETF_PREMIUM_MAX_PCT（默认关闭，已同步 .env.example）。
- [测试] 主项目监控测试 38 → 44 项（新增要约价提取/价差分类/配售噪声/配债登记/溢价监控 14 项）；全量离线套件 5843 通过（78 失败为既有的跨模块测试隔离污染——基线对照运行同样 57 失败且集合混沌不重合，涉改模块单文件与邻接运行均全过，非本次改动引入）。
- [修复] 测试环境根治收尾：conftest 双层环境隔离重构（session 级干净基线 undo collection 阶段写入 + 逐测试恢复"本测试开始前"状态并保留 setUpClass 设置、回填 PYTEST_CURRENT_TEST）；进程级默认 ENV_FILE（极简 .env）随基线捕获——任何测试 teardown pop 掉它之后由快照恢复自动带回，后台线程（AlertWorker/task_queue worker）任意时刻 get_config() → setup_env() 永远读干净文件而非真实 .env（跨线程污染终级根治，泄漏审计 fixture 留作长期哨兵）；ConfigManager .env 原子替换在 Windows 共享冲突（WinError 5/32）下重试 8×0.2s 后回退 in-place 重写。
- [测试] 全量离线套件失败数 57-81（混沌）→ 稳定 ≤3：跨测试环境污染多层全部根治（env 注入/collection 写入/bootstrap 标志/teardown pop/后台线程/WinError 5 竞态），残余为 intelligence fail_open 单个负载敏感测试（隔离必过，全量长负载下偶发 saved_count=0，feedparser 全局状态考古留待专项）。
- [修复] 修复六类测试环境/Windows 平台 bug（全量离线失败 57-81 → 24）：①conftest 新增 autouse 的 os.environ 快照/恢复 + Config bootstrap 捕获标志重置（根因：ENV_FILE 未设时真实 .env 被 load_dotenv 进进程环境且只增不减，跨测试污染 LLM 配置类测试）；②serve/Web 模式 runtime scheduler 此前未注册事件套利/转债打新/ETF 溢价三个监控（仅 CLI schedule 注册），已按 agent_event_monitor 同模式接入；③run_live.py 重放日期硬编码 2026-08-08 改为从快照数据推导；④codex 传输/工具进程测试在 native Windows 上按产品平台护栏跳过（POSIX 进程组语义，31+12 项）；⑤local CLI 后端三个真生产 bug——子进程 stdio 强制 UTF-8（Windows locale GBK 导致中文 JSON 乱码）、终止时 taskkill /F /T 树杀（原只杀直接子进程，孙进程泄漏持锁临时目录）、诊断预览 CRLF 归一化后再截断；⑥macOS 签名审计测试与 provider_cache 的环境/超时假设修正。

- [修复] 选股结果持久化恢复：选股页新增历史记录区块，任务完成后保留 run_id，刷新页面可从历史 API 恢复上次选股结果（此前刷新后结果丢失）。
- [修复] Web/API runtime scheduler 使用跨平台独立进程执行分析，并在默认 45 分钟硬超时或服务停止后清理进程树；停止返回后不再派发新的自动任务，避免一次卡死阻断后续调度。
- [修复] SearXNG 公共实例发现的默认值由启用改为关闭：公共实例普遍存在限流、下线或不返回 JSON 的情况，默认开启会让未配置搜索 key 的用户每次分析多耗 30~60 秒且新闻面最终为空。运行时默认值、配置模板、中英文档与工作流诊断同步调整；显式设为 true 的用户行为不变。
- [改进] 新闻检索未执行或零命中时，报告中如实标注结论未纳入新闻面证据：零命中与「未配置搜索渠道」使用各自独立的文案，覆盖日报 / dashboard / brief / 个股 / 企业微信与模板渲染的详细与摘要分支、历史报告与分享导出、报告详情 API 与 Web 报告详情页，并按 `zh` / `en` / `ko` 分别本地化。此前该情况下消息面章节直接消失，读者无从区分「确实没有新闻」与「检索静默失败」。披露以本次分析实际收到的消息面证据为准，涵盖实时检索、社交情绪与本地已落库的资讯池三路来源；搜索命中数仅用于在确无证据时说明原因（未配置渠道 / 检索零命中），避免把已用到本地或社交证据的分析误报成「未纳入新闻面证据」。Agent 模式的命中数取自 Agent 实际消费的搜索工具结果，而非分析结束后为持久化情报而补打的查询。

- [新功能] Agent 工具调用支持按类别（data/search/analysis/action/market）配置默认超时，并允许单工具声明 `timeout_seconds`；有效超时按 first-wins 优先级解析（显式 per-run `tool_call_timeout_seconds` > 单工具显式 `timeout_seconds` > 类别默认 > 无限制），剩余 wall-clock 预算仅作不可突破的外层 cap，超时后返回结构化 `{"timeout": true}` 错误（标记 `retriable: false` 并写入 `non_retriable_tool_results` 防重试重复执行）供 Agent 继续执行而非中断循环（fixes #1890）。
- [修复] Agent 工具注册表（`src/agent/factory.get_tool_registry`）由模块级缓存改为按「类别超时映射的值」比对失效，规避 CPython 回收对象后地址复用（`id(config)` 相同）导致配置 reload 后的 `Config` 被误判为未变、沿用过期超时的真 bug；新增 `_coerce_config_timeout` 类型白名单，使调用方传入 `MagicMock` / 缺属性 stub / 脏字符串（如 `float(MagicMock())` 静默得到 1.0）时降级为「无类别限制」而非崩溃或强加 1 秒超时；`build_agent_executor(config)` / `build_agent_chat_executor(config)` 现已把调用方 `config` 透传给 `get_tool_registry(config)`（不再无参调用冻结首构 registry）；`main._reload_runtime_config` 与 `SystemConfigService._reload_runtime_singletons`（及 `update()`→`reload_now` 路径）在配置热重载时调用 `reset_tool_registry()` 强制重建；回归测试补充「传入新 config 后 registry 重建」「reload 后新超时应生效」及「builder 透传 config」三类场景（#1890 的 review follow-up，闭环 OR-COM-dd1e8fa7 / OR-COM-bff42110）
- [修复] Agent 工具超时 review 闭环（fixes #1890 的 4 个 blocker）：超时解析由 min 契约改为 first-wins（显式 per-run `tool_call_timeout_seconds` > 单工具 `ToolDefinition.timeout_seconds` > 类别默认 > 无限制，剩余 wall-clock 预算只作不可突破的外层 cap；research 路径不再传 `tool_call_timeout_seconds` 以免覆盖类别限制）；超时结果标记 `retriable: false` 并写入 `non_retriable_tool_results` 阻断 LLM 同调用重试重入，且超时触发时为仍在后台运行的 handler 武装协作取消信号（`is_tool_cancellation_requested()` 与既有 `check_tool_execution()` 检查点均响应，handler 从不轮询则行为不变），作为 review 要求的「handler 内协作取消」缓解，规避 Python 线程无法 force-stop 导致的重复执行与副作用；`_coerce_config_timeout` 对 `inf`/`nan`/负数降级为「无限制」，根绝 `future.result(timeout=inf)` 触发 `OverflowError`；`get_tool_registry` / `reset_tool_registry` 加 `threading.Lock` 双检锁，且重建后返回本次构建的局部 registry（而非全局缓存），消除并发重建竞态与跨调用超时串扰；`@tool` 装饰器将 `ToolPolicy.timeout_seconds` 折叠进 `ToolDefinition` 单一来源；统一单/并行工具超时包装（单一 executor + deadline 驱动的 wait loop，消除并行路径嵌套 executor 与线程翻倍，duration 精确到各工具自身超时值），并新增快慢工具混合并行回归；同步 `docs/full-guide_EN.md` 的超时环境变量文档；测试覆盖 first-wins、non-retriable、协作取消接线、finite 校验、缓存线程安全与快慢混合并行。
- [修复] 按最新 review 复核收敛 3 处正确性问题（OR-COM-7f3d3f5b / 3d6b61f8 / a1e8b0c2）：`BaseAgent._filtered_registry()` 携带源 registry 的类别超时映射（工具子集仍生效类别上限，不再绕过 #1890 类别超时）；并行批次 >5 时排队调用的 per-tool 超时自 worker 实际开始起算（不再提交即烧预算导致对未启动调用的假超时）；`get_tool_registry()` 缓存命中快路径在锁内读取一致对（消除与 `reset_tool_registry()` 竞态返回 `None` 或错配 registry）。新增对应回归测试。
- [新功能] 股票名称解析引擎重构增强：新增 `resolver_name_to_code_list()` 公开 API，返回按市场排序（A 股→港股→美股）的 `Stock` 候选列表（最多 5 个），新增 `US_stock_code_match()` 匹配美股 ticker（1~5 位字母且仅限本地库已存在代码，避免 hello/open 等英文词误判为股票）；AkShare 全量 A 股数据经幂等 `extend_AkShare()` 合并进全局 `stockDB`（30 分钟缓存 + 失败 5 分钟退避 + Future 单飞：TTL 过期 stale-while-revalidate 零等待、冷启动等待上界由拉取超时推导（拉取经子进程封顶 25s）、worker 先清账唤醒等待者再做日志/落盘（finally 兜底 BaseException）、成功拉取落盘 `data/cache` 跨重启复用，非中文输入跳过网络扩展），匹配策略升级为「精确→子串（≥2 汉字）→拼音子串（≥5 字母）→difflib 模糊（0.8，单字误写 0.7 兜底）」；`resolve_name_to_code()` 保持既有本地优先语义（本地精确命中零网络，调用方离线低延迟契约不变），跨市场候选能力由 `resolver_name_to_code_list()` 独立提供；解析全链路线程安全（`stockDB` 读写加锁、名称/拼音索引随库变更自动失效），新增 40 个单元测试覆盖精确/跨市场排序/子串/拼音/模糊/幂等扩展/失败退避/多候选场景。
- [改进] `StockDaily` 表新增可空 `canonical_id` 列并支持双写（Expand-Contract PR2，issue #2207）：自愈式迁移幂等加列 + 普通索引 `ix_stock_daily_canonical_id`，存量行与 `save_daily_data` 未显式传参时均经 index-aware 推导（裸指数码命中注册表时统一到指数 `canonical_id`，避免同一指数按输入形态分裂到不同桶——例如裸 `000300` 与显式 `sh000300` 现在都收敛到 `sh000300`，而非裸码被推导为 `sz000300`），推导失败写 NULL 降级；读路径仍用 `code` 列，`(code, date)` 唯一约束保留不变。显式登记契约漂移：PRD Glossary/FR-1/DD-3 与架构 AD-1/AD-7 中 canonical_id 的点分格式描述（`000016.SH`）已被 Phase 1 已合入代码的前缀格式（`sh000016`）取代，本变更遵循代码，PRD/架构文档的同步修正留待后续 PR 统一收敛。
- [新功能] 当前注册表已识别的 5 个沪深 A 股指数以显式市场输入按 canonical 身份路由：名称优先使用注册表并以 Tencent、AkShare、TickFlow 兜底，日线固定使用 Tencent、AkShare、TickFlow、yfinance 多源降级链，不读取普通 A 股日 K 的 `*_PRIORITY` 配置；裸代码仍按股票处理，并隔离同码股票名称缓存。
- [修复] Anspire 搜索默认切换为全球区域模式（`region_mode=2`），使海外股票新闻检索能够覆盖境外信息。
- [修复] 桌面端将 `builder-util-runtime` 升级至 9.7.0，修复 CVE-2026-54673 涉及的 HTTP 重定向凭据头信息泄露风险。
- [文档] 增加 xAI Grok 的 LiteLLM 配置示例、Grok Bot 集成指南和异步分析 Skill，明确分析模型与 AI teammate 两类接入边界。
- [测试] 固定 yfinance 股息 TTM 与单股报告文件名的时间夹具，消除跨日期和合并后时间基准冲突造成的 CI 波动。

## [3.30.0] - 2026-08-09

### 发布亮点

- feat: LLM 渠道新增显式 Chat Completions / Responses API Surface，统一连接测试、分析、选股、图片识别与状态诊断的协议路由。
- feat: Agent Chat 按会话持久化 Skill 选择，刷新和切换会话后可恢复，并完整保留省略、显式空列表与非空选择三态。
- feat: Electron 桌面端恢复历史报告、市场复盘和完整报告分享图；未配置自定义品牌时，统一使用随包二维码与默认“小红书@霸天土小豆”账号文案。
- feat: 新增单条分析目标解析契约，收紧交易所后缀、指数别名及美股代码的规范化边界。
- fix: 修复移动端侧栏滚动、自选股详情状态、长通知标题分片及 Lark 国际域名等用户可见稳定性问题。
- improve: 后端 CI 按测试文件分成三个 runner 并行执行，并收紧 Web 共享资产与跨层契约的门禁范围。

### 新功能

- LLM 渠道支持显式选择 Chat Completions 或 Responses API Surface，兼容 Responses-only 模型，并让连接测试、主分析、选股、图片识别和状态诊断复用统一路由契约。
- Agent Chat 将 Skill 选择按会话持久化，刷新或切换会话后恢复；历史会话继续保留运行时默认，非法 Skill 请求不会误清空已有选择。
- Electron 桌面端复用安装包自带 Chromium，为历史个股报告、市场复盘和完整报告生成 PNG 分享图；Web 与桌面端统一使用随包分发的小红书二维码。
- `STOCK_LIST` 新增 `parse_analysis_target()` 单条目解析契约，并对外暴露可注入的指数注册表及解析结果类型。

### 改进

- 优化首页侧栏任务面板和自选股工作区，支持折叠任务摘要、自选股直接打开最新详情，并压缩头部操作以释放列表空间。
- 后端 CI 按完整测试文件拆分为三个 runner 并行执行，由统一 `backend-gate` 汇总结果；继续保留稳定的离线测试语义并降低全局状态竞态风险。

### 修复

- 固定分享图小红书二维码下方的账号文案格式为“小红书@昵称”，未配置自定义昵称时默认显示“小红书@霸天土小豆”；不再渲染数字 ID，历史 ID 配置不会改变分享图模板。
- 修复首页移动端历史、自选和今日列表无法稳定纵向触摸滚动的问题，同时保持桌面端卡片裁剪行为。
- 长通知包含一级 Markdown 标题时按对应标题边界正确分片，避免错误递归耗尽长度预算并中断发送。
- 收敛自选股详情与今日状态语义：刷新和查询期间不再开放过期报告，失败时不会将旧历史误标为今日分析，并限制逐股票 fallback 并发及取消失效批次。
- 收敛 Responses 渠道的协议、provider、route alias 与 wire-model 契约，拒绝协议不匹配、同名 alias 混用 Surface 及非法历史配置。
- 飞书交互机器人在 `FEISHU_DOMAIN=lark` 时让 Stream 长连接和消息回复统一使用 Lark 国际版 API 域名，避免连接国内域名后返回 `Incorrect domain name`。
- 显式交易所后缀、畸形混合 alias、dotted-prefix 及外盘半显式后缀的非法输入不再静默改写或降级为错误市场。
- `us` 前缀保持大小写不敏感，但 ticker base 必须满足规范的大写美股代码形态；非法小写、标点或数字输入直接返回 `unsupported`。
- 带 `.US` 后缀的规范美股代码不再因前两字符碰撞 `sh`、`hk`、`bj` 或 `us` 前缀而被错误拆分。

### 测试

- 非 Web 改动默认执行完整后端门禁；纯 Web、共享 public 资产、渠道模板和设置帮助的过滤语义补充回归测试，Docker 构建继续按实际输入过滤。

### 文档

- FAQ 补充 macOS 桌面应用被 Gatekeeper quarantine 阻止启动时，对受信任安装包进行临时放行的步骤。

## [3.29.0] - 2026-08-02

### 发布亮点

- feat: 将参考 AlphaSift 实现的选股核心与策略正式纳入 DSA，新增选股运行历史、数据源历史和候选深度分析链路。
- feat: 新增 1080px 个股决策卡与高密度市场复盘分享图，支持 Web 原生分享和下载回退。
- feat: 新增 Skill Opinion Outcome 计算、表现统计与基于真实样本的有界运行时权重。
- improve: 优化选股快照复用、热点按需加载、多源并发和候选轮换，缩短长流程等待并提升结果多样性。
- fix: 加固关闭认证、短凭证诊断脱敏、CI 超时取证和桌面冻结包启动链路。
- fix: 修复 Longbridge 量比、股票代码窗口解析、选股后置重排及分享图交互等稳定性问题。

### 新功能

- SkillAggregator 基于独立满足 30 条 evaluated 门槛的真实 Skill Outcome bucket，使用 Beta 先验收缩、unable 惩罚和多周期证据加权生成有界运行时权重；缺失、低样本或异常统计保持中性。
- 选股结果按 `run_id` 持久化到 DSA 数据库，新增运行历史和数据源历史 API，接入公告事件上下文及其搜索缓存，并支持将候选连同筛选策略映射的 skill 交给单股深度分析。
- 新增按 skill、horizon 与 outcome engine version 独立聚合的只读 Skill Opinion 表现统计；少于 30 条 evaluated 样本时仅返回观察性计数，不输出表现指标或调整运行时权重。
- 新增按 individual SkillAgent 自身 signal、版本化 engine 与本地已存同源日线窗口计算并持久化 `skill_opinion_outcomes` 的核心服务。

### 改进

- 选中热点后先展示榜单已有摘要和核心股，后台再补充完整详情，并将单次热点源等待上限收紧为 8 秒。
- 热点成分股并行获取东方财富与同花顺数据，并按固定数据源优先级合并；真实供应商调用增加限流、可终止 timeout、并发槽和 worker 回收，题材详情可按需复用 DSA 原生搜索服务补充安全且带链接的近期消息。
- 精简选股页面的重复说明，将任务标识、快照统计和排序诊断折叠到运行详情。
- 将参考 AlphaSift 实现的选股核心与策略正式纳入 DSA，统一使用 `ScreeningService`、`SCREENING_ENABLED` 和 `/api/v1/screening`，并保留 Apache-2.0 归因与来源版本记录。
- Web 选股使用浏览器匿名种子与运行 ID 在最终评分后的有界近分池中生成每次运行的候选组合；本地评分覆盖完整短名单，远程分析继续遵守数量上限，硬过滤、风险否决和得分保持不变。
- 热点榜单刷新与选股长流程解除双向串行等待，热点详情改为选中后按需加载；选股默认复用 5 分钟内且数据源优先级一致的成功全市场快照，并展示快照、候选上下文、LLM 重排、最终评分和新闻事件增强阶段。
- 图片报告改用独立的 1080px 个股决策卡和高密度市场复盘卡，优先从结构化 payload 精确填充数据并保留 Markdown 回退；小红书账号与二维码支持关闭或替换，Web 支持原生分享与下载回退。

### 修复

- Web 分享图在按需生成完成后通过第二次用户点击同步打开系统分享，避免首次异步生成使原生分享退化为下载。
- Web 分享图改为用户点击“分享”后才按需生成，不再在报告加载时自动请求。
- 移除基础设置选股卡片中仍跳转到“数据源”的过期“查看配置项”按钮，并将 `SCREENING_ENABLED` 及 Web 选股功能开关归入“基础设置”。
- `scripts/ci_gate.sh` 的离线测试增加单测 timeout 与 faulthandler 取证，并同步 Docker 发布流程的 CI 依赖，避免无 traceback 卡住或发布门禁缺少 `pytest-timeout`。
- 选股策略栏稳定展示完整中文策略列表，并保留自定义策略 ID 入口。
- 选股热点详情统一使用中文业务文案，不再显示内部类名、字段名和原始数据源错误。
- 刷新热点榜单并保留当前题材时同步绕过详情缓存重拉该题材，避免新榜单继续搭配旧路线与成分股。
- 选股尾部轮换以分析器输入顺序为权威并保留并列分候选顺序；热点消息增强、共享缓存 owner、全局并发容量和新闻搜索 deadline 统一收敛。
- Outcome 候选按上次尝试时间公平调度，避免持续新增的缺失 key 使旧 `pending` outcome 永久得不到重试。
- 选股主模型返回空内容、非 JSON 或低覆盖结构时继续尝试备用模型；全部失败时明确展示确定性因子排序状态，且不把 `reasoning_content` 当作最终结果。
- 选股日线增强改用请求级 DSA-first fetcher 注入，多个后置分析器按最新分数逐级重排，远程分析状态跟随实际提交候选，避免重叠请求泄漏 wrapper 或改写未提交候选。
- 统一等价股票代码的本地日线候选与同源窗口解析；冲突沪深交易所代码不再降级匹配裸码，回测仅接受快照或交易日历确认的起点。
- 关闭认证时强制再次校验当前管理员密码，命中 rate limit 时返回 429，前端在当前密码缺失时阻止提交并显示内联提示（#1970）。
- 本地 CLI 的 `stdout_preview` / `stderr_preview` 按环境变量、JSON、YAML/日志标量与 URL 的独立契约脱敏短凭证，避免 API key、secret 或 token 进入诊断（refs #1784）。
- PyInstaller 冻结包在 NLTK 3.10 导入保护下误判内置 `_internal` 标准库时不再启动失败；Windows/macOS 打包脚本统一接入兼容 runtime hook。
- 分享图按字段合并历史结构化数据与 Markdown，多市场逐区域复用持久化 payload，隐藏不可用市场灯号维度并保留配色方案；中英韩模板跟随报告语言，原生分享失败时自动回退下载。
- 飞书文件报告在写入或上传前清理隐藏的市场 metadata；桌面运行时默认隐藏未随包提供 renderer 的 Web 分享按钮。
- `redact_diagnostic_text()` 的命令替换扫描不再吞掉尾随非敏感诊断字段，并统一 `export FOO=$(...)` 与 `FOO=$(...)` 的脱敏行为。
- Longbridge 量比改用 adaptive keyword args 调用 `history_candlesticks_by_offset`，兼容 0.2.74 与 4.x SDK 参数顺序（fixes #2100）。

## [3.28.0] - 2026-07-26

### 发布亮点

- feat: Multi-Agent 多策略综合支持分层 deliberation、mediator/self-review、revision projection 与 multi-round，并统一最终动作和解释契约。
- feat: AI 建议页新增按决策风格分组的历史表现，specialist opinion 样本可持久化并用于后验评估。
- feat: 新增 `--portfolio futu`，可只读导入 Futu OpenD 真实账户的沪深 A 股、港股和美股 LONG 正股持仓。
- feat: Web 首页与 API 支持按单个或多个市场临时触发大盘复盘，不修改全局配置。
- feat: Tushare 支持通过 `TUSHARE_HTTP_URL` 接入自建网关或兼容镜像。
- fix: 改进港股行情路由与缓存、外股英文新闻匹配、数据源兜底顺序及桌面端发包稳定性。

### 新功能

- Multi-Agent 多策略综合新增受控 deliberation v0、可注入 mediator/self-review v1-v2、只读 revision projection v3 与 multi-round v4；增强层相对上一层 baseline 只能保持或继续 softened，不覆盖权威最终信号。
- `specialist` 模式最多选择 4 个策略专家，并通过 `AGENT_SKILL_CONCURRENCY` 控制 1–4 个 worker 并发；worker 继承主管线冻结的 target date 等上下文，单个 skill 失败不阻断其它策略或最终决策。
- Multi-Agent 报告按八态用户 action 追踪 Pipeline 最终调整，排除非法 Agent 意见；仅在 canonical action 可唯一解析时生成 explanation 与 DecisionSignal，并以同一个 `final_action` 统一最终动作契约。
- specialist 在分析历史保存成功后持久化版本化、低敏且幂等的有效 opinion 样本，为后续后验评估提供真实数据；本阶段不计算 outcome、不统计表现、不调整权重。
- AI 建议页新增决策风格历史表现，按每个分组独立的 30 个已完成样本门槛展示命中、区间涨跌、无法评估和最大不利波动，并保持旧统计接口兼容。
- 新增 `--portfolio futu`，只读导入 Futu OpenD 真实账户的沪深 A 股、港股和美股 LONG 正股持仓作为分析列表。
- Web 首页与 `POST /api/v1/analysis/market-review` 支持用严格校验的 `region` 临时选择单个或多个复盘市场；一次性覆盖不读写全局配置，并贯穿任务提交、状态、SSE、结果与历史记录。
- Tushare 数据源支持通过 `TUSHARE_HTTP_URL` 自定义接入地址；留空时继续使用官方默认地址（fixes #1985）。

### 改进

- 暂停 PR Review 的自动触发，仅保留 `workflow_dispatch` 手动入口，避免辅助评审重复运行及评论权限失败产生误导性红灯；正式 CI 检查保持不变。
- `.env.example` 与每日分析 workflow 同步映射 `TUSHARE_HTTP_URL`，保持本地和云端配置入口一致。

### 修复

- 修复外股代码映射到中文显示名时英文新闻相关性漏判，统一外股代码、英文名和别名解析，并对展开后的检索词去重（fixes #2026）。
- 特权 `pull_request_target` 流程不再检出 fork PR head；敏感步骤仅执行主分支可信脚本，PR 元数据与 diff 通过 GitHub API 读取（fixes #2051）。
- PR Review 事件载荷缺失、不可读或 JSON 非法时输出可定位且不泄露载荷的警告，并保留原有降级行为（fixes #2070）。
- 修复 Windows 上 `mimetypes` 冷启动读取注册表导致进程卡死的问题。
- 统一 `DataFetcherManager`、AkShare 与 Longbridge 对 4–5 位裸港股码的识别，避免 4 位代码被错误路由或静默失败（fixes #2091）。
- AkShare 港股实时行情增加 20 分钟全市场缓存与并发冷启动 single-flight，热缓存命中不再等待网络限速，主接口异常时仍保留新浪备用接口降级（refs #1852）。
- 将 `TencentFetcher` 默认优先级调整为 A 股日 K 数据源的最终兜底，并新增 `TENCENT_PRIORITY` 显式覆盖项（refs #2032）。
- Web 设置页和通知测试入口补齐普通钉钉群机器人配置，支持安全遮罩保存 webhook 与 secret、查看帮助并发送测试通知（refs #1957）。
- Agent Chat 普通与流式接口在请求未指定 `report_language` 时继承全局 `REPORT_LANGUAGE`，显式请求值仍优先。
- WebUI 分开展示发布版本、代码版本与构建时间，并用构建输入摘要避免复用时间戳未变化的旧静态资源（fixes #2093）。
- macOS unsigned 打包显式禁用 Electron 签名与 Hardened Runtime，在冻结后端和 electron-builder 阶段清理残缺签名，并审计原始应用与 DMG 产物；该缓解不替代 Apple Developer 签名与公证（refs #2075）。

### 文档

- 修复文档中的失效相对链接。
- [修复] #2026 外股代码映射到中文显示名时英文新闻相关性判定漏判：新增同源 STOCK_ENGLISH_NAME_MAP 单一真源、canonicalize_foreign_stock_code 规范化入口与 _foreign_english_query_terms 别名解析，使 AAPL/00700/BABA 等 ticker 即使 stock_name 为中文也能在查询构建、相关性打分与多维度情报路径上复用 canonical 英文名，并补齐 .US/.HK suffix / HK 前缀全形式的归类与回归用例；同时在 _score_news_relevance 对 alias 展开 term 做去重，避免 legal alias 展开短名与显式 short alias 重复计分。
- [新功能] Tushare 数据源支持通过 `TUSHARE_HTTP_URL` 环境变量自定义接入地址，便于网络无法直达 `api.tushare.pro` 时切换自建网关或第三方兼容镜像；留空保持官方默认地址不变（fixes #1985）
- [文档] `.env.example` 与 `.github/workflows/00-daily-analysis.yml` 同步映射 `TUSHARE_HTTP_URL`，避免出现"配置项有但 workflow 漏映射"的半修状态
- [修复] #2051 PR Review 的特权 `pull_request_target` 流程不再检出 fork PR head：敏感文件、标签、报告与 AI 审查统一通过 GitHub API 将 PR 元数据和 diff 作为数据读取，只执行主分支可信脚本；Python 语法、Flake8、确定性检查和离线测试继续由无 secrets 的 `pull_request` CI / `backend-gate` 执行，兼容 `actions/checkout` 新增的 fork checkout 安全保护。
- [修复] 修复 Windows 上 mimetypes 冷启动时读取注册表导致的进程卡死

## [3.27.0] - 2026-07-19

### 发布亮点

- feat: 新增 Codex App Server single-agent 问股实验原型，并保持 LiteLLM、Multi Agent、普通报告和定时任务等默认链路不变。
- feat: Web AI 建议页支持保存基于历史报告快照重算的决策风格信号，补齐去重、续期、失效和可审计 guardrail 语义。
- feat: 引入多策略观点结构化输出第一阶段契约，覆盖观点标准化、基础冲突检测、聚合元数据和报告兼容边界。
- improve: 报告页明确展示输入数据状态、来源、异常影响、处理建议和诊断码，并区分页面资讯与本次分析输入。
- fix: 修复 MiniMax 推理内容污染最终 JSON、字符串 `<think>` 包装兼容及多 Agent 风险覆盖后结论未按最终信号收敛的问题。
- fix: 补齐美股实时行情 PE/PB 估值字段、多市场工具描述和 macOS Gatekeeper 安装排障说明。

### 新功能

- 新增 #1743 Phase 6 Codex App Server single-agent 问股实验原型，仅开放三个既有只读 Tool Surface 工具；默认 LiteLLM、Multi Agent、Deep Research、普通报告、定时任务与 Phase 1/2 `codex_cli` 路径保持不变。
- Web AI 建议页支持确认保存基于历史报告快照重算的决策风格信号，以 created/existing/refreshed 区分新建、原样复用和既有记录续期或维度补齐，并复用 profile-aware 去重与失效语义。
- 多策略观点结构化输出第一版新增策略观点标准化、基础冲突检测与聚合 metadata，作为 #1964 的阶段性基础契约；本版本不声明完成并发执行、完整策略调度 MVP 或前端完整多语言展示。

### 改进

- Codex 设置页仅检查配置、命令和所需协议是否允许尝试，用户保存后可直接提问；Chat 以服务端 `accepted` 事件提交问题并按实际 backend 停止。
- Web 报告页输入数据块沿用状态、来源、告警和说明字段，在说明中补充异常影响、处理建议与诊断码，并区分报告页资讯和本次分析输入。
- 更新 Anspire 数据源的项目展示信息，并将 `get_stock_info` 工具说明从 A 股限定修正为覆盖 A 股、港股和美股。

### 修复

- 修复 MiniMax 分析与渠道 JSON 测试把推理内容和最终文本拼接后导致结果无法解析、无法持久化的问题；字符串响应仅剥离开头完整的 `<think>` 包装，并保留 JSON 内容中的同名字面标签。
- 修正多 Agent 内部 runtime facts 的 timeout 归因，并让 risk application 覆盖后的 dashboard 决策字段及一句话核心结论基于 post-risk signal 完成 finalization。
- 收敛多策略综合器语义：正确处理 Signal 枚举、缺失 signal、有效 opinion_count 和 deterministic synthesis，并兼容历史与外部 dashboard 的宽松字段形状。
- Codex 问股只接受 App Server 明确完成的终态回答，并统一整体时限、累计输出、事件、工具预算和进程回收边界。
- `codex_cli` 普通分析显式固定无人值守批准策略与只读沙箱，避免新版 Codex 在非交互任务中因请求人工批准而中断。
- yfinance 美股实时行情补齐 `pe_ratio` 和 `pb_ratio`，供估值分析和下游报告使用。

### 文档

- 补充 macOS 未签名、未公证 DMG 被 Gatekeeper 拦截时的架构选择、安全排查与官方安装包临时放行步骤。

## [3.26.1] - 2026-07-12

### 发布亮点

- feat: Web 首页新增历史、自选与今日工作区，支持批量分析、今日覆盖判断和评分排行。
- feat: 新增 A 股市场结构与题材主线上下文，并贯通报告、Agent、DecisionSignal 与 Web 展示。
- feat: 飞书支持文件形式推送报告，多 Agent 支持子 Agent 独立超时钳位。
- feat: 补齐内部 DSA Tool Surface、DecisionAgent 分歧摘要和 DecisionSignal profile 契约。
- fix: 统一报告动作口径，修复按股票代码批量删除历史记录和通知理由静默截断问题。
- fix: 改进 Web、桌面端、数据源缓存及发行包资源的稳定性。

### 新功能

- 新增 A 股市场结构与题材主线上下文，并在报告、Agent、DecisionSignal 和 Web 市场位置卡中复用。
- 飞书推送新增文件上传能力：`FeishuSender.send_feishu_file(file_path)` 通过 App Bot SDK (`im.v1.file.create`) 上传文件并发送文件消息；Webhook 模式回退为发送文件内容文本；新增 `FEISHU_SEND_AS_FILE=true` 配置开关，开启后飞书以文件形式发送报告而非文字消息。
- 多 Agent 编排 Pipeline 新增子 Agent 独立超时钳位：支持 6 个环境变量为 TechnicalAgent、IntelAgent、RiskAgent、DecisionAgent、PortfolioAgent、SkillAgent 各自配置独立硬上限，互不挤占配额；默认 0 表示关闭钳位。

### 改进

- 为 multi-agent DecisionAgent 增加内部低敏分歧摘要输入管线，作为 #1904 P1 解释输出的前置 plumbing；不改变 public API、dashboard schema 或最终解释字段。
- GitHub Actions 每日分析工作流补齐 TickFlow 数据源环境变量映射，并收敛 README 数据源稳定性说明到完整指南。
- Web 首页个股栏新增历史 / 自选 / 今日切换，保留历史分析默认视图，并支持在自选页一键分析全部或仅分析今日未覆盖股票、在今日页按评分查看当天分析排行；分块提交部分失败时保留已确认计数、停止后续提交并刷新任务列表。
- GitHub Actions 每日分析工作流新增钉钉通知环境变量映射，支持在云端定时任务中直接使用钉钉机器人。
- `STOCK_LIST` 自选股解析支持中文逗号、顿号、分号、空格和换行等常见粘贴分隔符，运行时、定时热刷新、CLI `--stocks`、Web 设置保存和自选 API 统一识别，并在写回时规范为英文逗号。
- 新增 `NEWS_INTEL_AUTO_FETCH_ENABLED` 单开关，开启后个股分析、Agent 分析和大盘复盘会 fail-open 自动初始化并刷新 RSS/Atom/NewsNow 本地资讯池。
- Web AI 建议页新增主股票上下文，复用最近分析和股票索引候选，并改进表现统计零样本说明。
- DecisionSignal 将 `decision_profile` 升级为正式 nullable 字段，统一 same-profile 查询、去重、续期和失效语义，并保持 create metadata `null` 兼容与 SQLite 幂等回填诊断。
- 设置页移动端分类导航改为横向滚动列表并保证设置内容首屏可见，桌面端保留分类说明并收紧字段布局层级与间距。
- 新增 #1743 Phase 6a 内部 DSA Tool Surface 契约，统一工具 schema、stock scope fail-closed guard、结构化错误、审计摘要和脱敏诊断边界，并明确外部 AgentBackend 工具能力仍需 wire-level probe 证明。
- `src/services/analysis_service.py` 在 `report` 详情层新增 `details.raw_result` 回填，补齐与 API/历史详情的报告载荷一致性；不改变 provider、model、Base URL 或配置迁移语义。

### 修复

- 按股票代码删除历史记录时分批清理全部匹配项，并拒绝空白代码，避免超过 10000 条后残留记录或无筛选删除。
- 市场结构概念排行为空或超时时复用本轮负结果，避免批量个股分析重复请求同一概念排行数据源。
- Windows/macOS 桌面后端打包显式收集并校验 AkShare `file_fold/calendar.json`，避免发行包因缺少交易日历 package data 导致热点题材和选股日线增强降级。
- 邮件、Telegram 与报告共享的 DecisionSignal 摘要完整展示已脱敏的理由，避免固定 120 字符在句中无提示截断；Telegram 按最终 Markdown payload 长度安全分片。
- 推送报告、Jinja 报告与历史 Markdown 导出复用 Web/API 的评分-action 口径：高分但旧 `operation_advice` 仍为持有且无降级原因时，建议文案与三类统计展示为买入；有明确 guardrail reason 时继续保留持有/观望。
- WebUI 启动时显式 `--host` / `--port` 不再被 `.env` 中的 `WEBUI_HOST` / `WEBUI_PORT` 覆盖，未传 CLI 参数时统一使用解析后的运行时配置。
- Web 首页今日状态与排行使用带时区偏移的历史时间戳和完整分页数据，在查询失败、跨服务器时区边界或任务完成刷新时保持安全且准确。
- Web 首页 stock bar 刷新序列化：并发或乱序返回时仅最新请求可清除 `stockBarRefreshFailed`，避免旧响应覆盖任务完成后的刷新结果。
- Web 持仓页首屏快照改用 `include_realtime=false` 快速估值，跳过逐票实时行情预取后先展示持仓列表，避免外部实时行情源变慢时长时间空白等待。
- 修复任务状态接口重建报告动作字段时把合法情绪分 `0` 当成空值的问题，确保低分报告能按评分口径纠正为卖出建议。
- 修复 Agent 流式回复在未收到完成事件就断开时被显示为“（无内容）”的问题，改为提示流式响应中断并保留用户消息。
- 修复桌面端 `WEBUI_HOST=*` / `WEBUI_HOST=[::]` 会被原样传给端口探测和后端启动导致无法监听的问题，启动前分别规范化为 `0.0.0.0` / `::`。

### 文档

- 在 README 快速开始中补充行情数据源配置说明（`TUSHARE_TOKEN` / Longbridge），明确未配置时仍可使用 AkShare、Baostock、YFinance 等免费兜底源，并同步中英文完整指南。

## [3.25.0] - 2026-07-03

### 发布亮点

- feat: 新增 `claude_code_cli`、`opencode_cli` generation-only 本地 CLI backend，并补齐生成后端状态诊断、预览、冒烟测试 API 和 Web 状态面板。
- feat: 台股报告完整接入三大法人资料，覆盖报告渲染、LLM prompt、TWD 币别标示、收盘集合竞价识别和 fetcher 韧性加固。
- feat: 新增钉钉群机器人通知、韩语报告输出和 AI 建议决策风格重评估预览。
- feat: Agent `/chat/stream` 标准化 progress event，新增阶段开始/完成、pipeline timeout 和预算跳过语义。
- fix: 修复桌面端 WebUI host/port 绑定、macOS Homebrew CLI PATH 诊断、Discord 长报告分片、AlphaSift 超时、yfinance 分红解析、A 股回测代码归一化等稳定性问题。

### 新功能

- 钉钉群机器人通知支持 `DINGTALK_WEBHOOK_URL` 和 `DINGTALK_SECRET`，并对长文本自动切片以适配 20KB 限制。
- 报告输出语言新增韩语（`REPORT_LANGUAGE=ko`），覆盖个股报告、大盘复盘、Prompt 输出语言、决策护栏、通知模板标签与 Web 报告详情页文案。
- 新增 `claude_code_cli` 与 `opencode_cli` generation-only 本地 CLI backend，保留 LiteLLM 默认路径、Agent 工具调用边界、per-preset extractor、最小 env allowlist 与结构化错误。
- 新增生成后端状态、预览和冒烟测试 API，以及 Web 生成后端状态面板，区分轻量检查与 JSON 冒烟测试，并保持本地 CLI “仅生成、不支持问股工具调用”的边界。
- Agent `/chat/stream` progress event 新增 `stage_start`、`stage_done`、`pipeline_timeout`、`pipeline_budget_skipped`，补齐阶段进度、超时和预算跳过语义。
- 台股个股报告的 institution 区块展示 TWSE T86 / TPEx 三大法人原始买卖超净额，并将三大法人净买卖超表格注入 LLM 分析 prompt 作为台股筹码过滤器。
- 新增 AI 建议决策风格重评估预览接口与页面预览。

### 改进

- 台股三大法人 fetcher 增加并发缓存防击穿、TWSE/TPEx 分市场熔断、TPEx 日期保护和剩余 stage 预算复用，降低限流、端点故障和冷抓取超时带来的降级概率。
- AlphaSift 默认依赖 pin 更新到 `9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf`，接入 wrapper 数据源 caller-side timeout、东财直连限速/抖动、策略目录元数据和防守策略。
- 选股任务状态轮询遇到可恢复超时时提示后台任务仍会自动重试，`.env.example` 补充相关超时调优项。
- 收敛个股分析评分与 DecisionSignal action 口径，统一 80/60/40/20 分段，并在风控降级时记录 raw/adjusted score、final action 与原因。
- Web 设置页左侧分类切换时仅在相关分类展示首次启动检查和 AlphaSift 辅助卡片，减少跨分类残留。

### 修复

- 修复 Windows 桌面端启动后端时固定传入 `--host 127.0.0.1` 导致 `.env` 中 `WEBUI_HOST=0.0.0.0` 不生效、局域网无法访问 WebUI 的问题；桌面端仍默认使用 `127.0.0.1`，仅在显式配置 `WEBUI_HOST` 后按配置绑定。
- 修复桌面端启动时 `.env` 中 `WEBUI_PORT` 与 Electron 自动选择端口不一致，导致窗口继续等待旧端口并连接超时的问题。
- 修复 macOS 桌面端从 Finder/Dock 启动时后端 PATH 看不到 Homebrew Codex CLI 的问题，并明确 Codex CLI 主分析与 Agent LiteLLM 工具调用分流诊断。
- 修复 Discord 长报告推送按 2000 字符上限分片逐段发送，遇到 429 限流会按 `retry_after`/`Retry-After` 有限重试，避免中途失败后只收到前半段报告。
- 修复日股、韩股和台股 `market_phase` 收盘集合竞价识别，避免临近收盘阶段仍被标记为普通 `intraday`。
- 修复 A 股个股分析遇到空 `belong_boards` 占位时不会继续补查所属板块、关联板块模块展示不稳定的问题。
- 修复大盘复盘在 LLM 标题漂移或正文缺少板块段时，Web 与推送报告偶发缺少板块主线的问题。
- 修复 Web 大盘复盘结构化数据成交额、指数点位、涨跌幅和高/低值格式化，避免浮点长尾或缺失值 `0.00` 直接展示。
- 修复 Web 首页个股栏在 stock-bar 摘要字段缺失或动作建议无法归类时隐藏情绪分与建议标识的问题。
- 修复 Web 设置页定时任务“立即执行一次”后台线程未传 `stock_codes` 导致任务崩溃的问题。
- 修复 `opencode_cli` 静态指令，避免全局 JSON-only 约束影响 `generate_text()` 与大盘复盘自由文本输出。
- 修复 yfinance 1.2.x 将 `Ticker.dividends` 返回为单列 DataFrame 时分红解析被丢弃的问题，恢复 TTM 每股分红与分红次数计算。
- 修复台股财务金额币别标示，将 TWD 金额标注为“新台币”，避免在 A 股语境下误读为人民币。
- 修复回测日线补全将 `605066.SH`、`SS605066`、`SS.605066` 等 A 股等价代码误向数据源请求 `SS605066`，导致回测数据不足的问题。

### 文档

- 新增 Agent `/chat/stream` progress event 契约文档，说明新增事件字段语义、Web 兼容边界、验证方式和回滚方式。
- 同步本地 CLI backend 隐私/部署边界，明确 local CLI 不是离线模型，Docker/CI/远端需自行安装登录，DSA 不读取 Claude/OpenCode credential 文件。
- 更新 README 三语入口和市场支持边界，说明台股 `.TW` / `.TWO`、三大法人报告区块、TWD 标注与收盘竞价识别能力边界。

### 测试

- 台股三大法人 fetcher 新增 live-smoke 脚本与 `@pytest.mark.network` 漂移检测测试，用于非阻断 network-smoke 定时任务核对 TWSE T86 / TPEx 核心字段与解析结果。

## [3.24.1] - 2026-06-28

### 修复

- 修正 Longbridge SDK 版本约束为按平台选择可安装版本，避免桌面与 Docker 发布在 `pip install -r requirements.txt` 时因不存在的 `0.2.75` 版本失败。

## [3.24.0] - 2026-06-28

### 发布亮点

- feat: 扩展台股、日股、韩股市场支持，覆盖台股 suffix-only 分析、台股三大法人资料层、JP/KR 大盘复盘和跨服务市场枚举。
- feat: 新增 GenerationBackend 抽象、`codex_cli` 本地 CLI backend、reserved Hermes 本地 HTTP 渠道和 prompt cache capability registry。
- feat: Web/API/Desktop 支持多时间定时推送与 runtime scheduler 热重建，Web 设置页补齐首次启动检查与定时任务面板。
- feat: 报告链路补齐信号归因、单股信号时间线、概念板块排行和通知/报告关联板块展示。
- fix: 修复 Docker/启动探针、静态资源 MIME、回测空结果、组合估值、通知 Markdown、AlphaSift 数据源和测试环境隔离等稳定性问题。

### 新功能

- 新增台股 suffix-only 个股分析 MVP：`.TW`/`.TWO` 代码可走 YFinance 日线与近实时行情，并补齐市场识别、交易日历和 Prompt 能力边界。
- 台股 `tw` 纳入 DecisionSignal、Portfolio、Intelligence 服务层、API 枚举和 Web 筛选，避免台股分析信号被市场归一化静默丢弃。
- 新增台股三大法人资料层 fetcher `TwInstitutionalFetcher`，支持 TWSE/TPEx 来源、日期转换、单日缓存和 fail-open 退化。
- 大盘复盘新增 `jp`/`kr` 市场，支持日经225/TOPIX、KOSPI/KOSDAQ 指数复盘，并扩展 `MARKET_REVIEW_REGION`、交易日过滤和 Web 设置枚举。
- 新增 GenerationBackend Phase 1 抽象和显式 opt-in 的 `codex_cli` 本地 CLI generation backend，提供结构化错误、fallback、stream 降级和 usage unavailable contract。
- 新增 reserved Hermes 本地 HTTP generation 渠道，提供 JSON generation、no-proxy 本地调用和 saved secret endpoint 绑定。
- 新增 Provider Cache Capability Registry，按 provider、API surface、gateway 与 verification status 建模 prompt cache 能力。
- 支持 `SCHEDULE_TIMES` 多时间定时推送，长运行 Web/API/Desktop 进程保存调度配置后可热启停或重建 runtime scheduler。
- 新增信号归因分析和 Web AI 建议页单股信号时间线，并为自动生成与历史回填的 DecisionSignal 写入默认 `decision_profile` metadata。
- 大盘复盘、Web 报告页和通知关联板块补齐概念板块排行与概念信号展示。

### 改进

- TickFlow 扩展为可选 A 股日 K、实时行情、股票列表/名称数据源，并增加 count、完整性校验和批量预取缓存保护。
- 硬化 JP/KR/TW suffix 识别、日韩股票种子索引、YFinance 报价/基本面上下文，以及 JP/KR Portfolio 与 Market Light 边界。
- Web 设置页新增首次启动配置检查卡与定时任务面板，隐藏内部 `SCHEDULE_TIMES` 键，并改善重复任务提示的关闭与自动消失体验。
- Web 历史报告详情不再内嵌 AI 建议卡片，结构化决策信号集中到 AI 建议页，并保留来源报告 ID/URL 参数精确定位。
- `GENERATION_BACKEND=codex_cli` 下普通分析与大盘复盘不再因缺少 LiteLLM API Key 被误判不可用，并改用 `--output-last-message` 文件读取最终响应。
- 本地 CLI backend 对 stdout/stderr 诊断预览和最终响应实行执行期总量上限，并补齐新增 generation backend 数字配置最大值校验。
- AlphaSift 默认依赖 pin 更新到 `0a7b9cd59e81718f851890535241bc105d4ddc64`，并默认走 DSA EastMoney 兜底 provider、暴露 source health 诊断。
- Docker Compose 默认内存建议提升到 1G；每日分析 workflow 兼容误将 `STOCK_LIST` 配到同名 Environment variables 的场景。
- Agent 路径同步 signal attribution prompt，通知报告摘要不再展开 AI 决策信号明细，完整信号保留在个股详情与单股报告。

### 修复

- API 异步批量分析共享概念板块排行缓存，避免同批多股重复拉取全市场概念排行。
- 修复通知 Markdown 表格转换在空单元格后将后续内容错配到错误表头的问题。
- 修复 Market Light 区域归一化拒绝 `jp`/`kr`、日韩历史列表市场阶段摘要误传 `analysis_phase` 和默认通知报告缺少 `dashboard.phase_decision` 的问题。
- 固定 Docker 可安装的 Longbridge SDK 版本为 0.2.75，并修复 Docker 镜像中 efinance 缓存目录属主导致 A 股数据源降级的问题。
- 持仓快照今日估值改为受限并发预取实时价，减少持仓较多时 Web 组合页面刷新超时。
- Web 首页重新分析完成后自动切换到同一股票最新报告，并修复 Windows 环境下 Web/Desktop 静态 JS 资源可能以 `text/plain` 返回导致黑屏的问题。
- 修复 `--serve --schedule` 与 Web/API runtime scheduler 状态脱节、立即执行忙碌状态误提示、重建定时任务重复监听和启动参数语义丢失。
- 修复 `main.py --serve-only` 在低配主机上因惰性 import 应用超出 uvicorn 启动自检窗口而反复重启的问题。
- 修复 Web 回测未传分析日期范围、股票代码未归一化导致成功响应但结果为空的问题，并为空候选、行情不足和非法后缀提供诊断信息。
- 修复 unsupported `GENERATION_BACKEND` 被当成空响应/模板 fallback、`codex_cli` stdout 重复计入输出上限和主分析 JSON schema fallback 语义回退的问题。
- Docker 部署中 Web 设置页保存自定义 Webhook 模板时会转义 `$content_json` 等占位符，并在运行时还原，避免 Compose 重新部署展开为空。

### 文档

- 补齐概念板块排行字段契约、通知报告行业/概念类型列展示和数据源稳定性与故障处理图示。
- 补充 JP/KR/TW suffix-only MVP、`MARKET_REVIEW_REGION` 保存/校验/回退矩阵、Market Light 边界和 PR 提交流程约束。
- 补充本地 CLI backend 隐私边界、非离线模型说明、Docker/CI 登录态限制和 `codex_cli` experimental/limited 状态。
- 补充回测请求链路说明，并同步更新 `docs/full-guide.md` 与 `docs/full-guide_EN.md` 示例。

### 测试

- 新增/更新台股、JP/KR 大盘复盘、GenerationBackend、`codex_cli`、Hermes、本地 CLI、runtime scheduler、回测和概念板块排行相关回归测试。
- 加强 `tests/test_analysis_api_contract.py`、`tests/test_analysis_history.py` 与 `tests/test_backtest_service.py` 的临时 `.env` 隔离，避免本地真实 `.env` 污染系统配置测试。

## [3.23.0] - 2026-06-20

### 发布亮点

- feat: DecisionSignal 贯通报告提取、Web 展示、反馈/后验、告警通知和组合风险，AI 建议信号进入可追踪闭环。
- feat: 新增合规 RSS/Atom 与 NewsNow 资讯源情报池，分析、Agent 和大盘复盘可 fail-open 复用本地资讯 evidence。
- feat: 新增日本/韩国 suffix-only 个股分析 MVP，支持 `.T`、`.KS`、`.KQ` 标的通过 YFinance 获取行情与技术上下文。
- feat: 新增 Token 用量监控看板、legacy LLM usage telemetry 和 message stability audit，增强 LLM 调用可观测性。
- fix: 修复运行流 live 状态、AlphaSift 缓存/字段兼容、发布说明诊断和日韩股票输入/历史展示等稳定性问题。

### 新功能

- 个股分析历史成功保存后会从最终报告 best-effort 提取 `DecisionSignal` 决策信号，复用现有信号去重、计划质量计算和脱敏契约。
- 新增 Web AI 建议页、持仓页 latest active 信号摘要、历史报告信号展示和更完整的信号详情卡片，展示评分、置信度、价格计划、催化、风险与失效条件。
- 新增 DecisionSignal 用户反馈、信号级日线后验评估、统计 API 与 Web 展示，使用 outcome/feedback sidecar 表并保留主信号表契约。
- 将 DecisionSignal 复用到告警、通知和组合风险：告警触发关联 latest active 信号或创建最小 alert 信号，通知追加低敏信号摘要，持仓风险聚合 active sell/reduce/alert 信号并保持 fail-open。
- 新增合规 RSS/Atom 资讯源配置、拉取、去重、入库、查询、retention 与基础安全校验 API，作为个股/市场资讯情报池基线。
- 资讯源新增 `newsnow` 类型、`NEWSNOW_BASE_URL` 配置和 `/api/v1/intelligence/sources/defaults` 默认源初始化接口，内置财联社热门、雪球热门股票、华尔街见闻快讯、金十数据和格隆汇事件等财经热点源。
- 个股分析、Agent 分析和大盘复盘会 fail-open 读取本地资讯/情报池，并把来源链接作为新闻上下文和 evidence 输入。
- 新增日本/韩国 suffix-only 个股分析 MVP：手输 `.T` / `.KS` / `.KQ` 代码可走 YFinance 日线与近实时行情，补充市场识别、交易日历、Prompt 语义、Web/API 类型和能力边界文档。
- 新增 Token 用量监控看板与 `/api/v1/usage/dashboard` 接口，展示 LLM 调用总量、Prompt/Completion 拆分、模型用量、调用类型分布和最近调用明细。

### 改进

- 为 `DecisionSignal` 补齐默认生命周期、同源窄 relaxed 去重、相反 active 信号自动 invalidated、terminal 状态不可 PATCH 复活和低敏 market phase hints 提取。
- 补充 Web decision-signals typed API wrapper 与契约隔离测试，并将历史报告 AI 建议查询收口到精确报告懒提取。
- DSA 数据源链路新增 Tencent 日 K 直连 fetcher、daily source health 短期熔断，并升级 AlphaSift 默认 pin/runtime bridge。
- 默认启用 `DAILY_SOURCE=auto`、Sina snapshot 优先级、候选级 quote context 与 LLM ranking timeout/max tokens 边界。
- 新增 legacy LLM usage provider/cache telemetry、message HMAC 诊断字段和普通个股分析 legacy message stability audit，不改变公开 Usage API、prompt 或 provider 参数。
- 问股页移动端策略选择改为默认收起的按钮入口，展开后仍可多选策略并在发送后自动收起，减少对对话内容的遮挡。

### 修复

- 修复运行流 live SSE 脱敏、后期 LLM/通知卡片重复、数据源聚合卡片过早成功、Web 首页窄侧栏挤压股票信息，以及个股分析自动生成大盘上下文时运行诊断互相串扰的问题。
- 修复 AlphaSift 热点题材 EastMoney 瞬断且无缓存时的空态、桌面更新热点缓存保留，以及 `leader_stocks` / `stocks` 双字段兼容问题。
- 修复 Web AI 建议页筛选/状态更新分页、价格计划单边入场价展示、持仓 latest 信号刷新、详情 JSON 安全渲染和卡片交互语义问题。
- 仅允许历史报告存在明确 `action` 或可解析动作时才触发决策信号懒回填，避免 `decision_type=hold` 等统计口径在建议不明确场景误回填。
- 修复 #1390 P6 DecisionSignal 在组合风险快照语义和默认聚合通知展示中的遗漏。
- 默认禁用 `/api/v1/intelligence/sources/defaults` 新建源，避免公开示例 NewsNow 实例被默认启用，同时统一 500 响应细节仅入日志、响应返回通用错误信息。
- Web 股票自动补全、输入校验、历史/任务展示和筛选补齐日韩 Yahoo 后缀代码、常用日韩股票索引与股票池裸码解析，避免 `000660`、`005930`、`7203.T`、`005930.KS`、`035720.KQ` 等场景崩溃、误入 A 股语义或历史分裂展示。
- 日韩个股分析在本地历史上下文缺失时会用 YFinance 日线兜底构造 K 线与技术指标上下文，避免报告误称日股/韩股核心行情和技术数据不可用。
- 发布说明生成查询 PR 作者失败时保留降级并输出包含 PR 编号和异常类型的 warning，便于排查 token、权限、网络或 GitHub API 异常。

### 文档

- README、完整指南和市场支持文档补充日股/韩股示例（`7203.T`、`005930.KS`），并明确 `.T/.KS/.KQ` 当前为 YFinance-only MVP。
- 新增 DecisionSignal 决策信号专题文档，补齐字段/API/Web/告警通知/组合风险/后验评估、脱敏、迁移与回滚说明，并收口 Web i18n 显示边界。
- 补充 AlphaSift 迁移与回退边界：明确 `ALPHASIFT_INSTALL_SPEC` 显式覆盖语义、`requirements.txt + DEFAULT_ALPHASIFT_INSTALL_SPEC` 与运行时兼容边界。
- 补充资讯源基线文档，说明 `NEWS_INTEL_*` 配置、NewsNow 自建建议、模型/provider/base URL 不变更边界，以及禁用或移除情报源变量的回退路径。

### 测试

- 新增/更新 DecisionSignal 服务、提取、反馈/后验、摘要、文档、通知、告警、持仓风险、Web 展示和 label 的回归覆盖。
- 新增/更新 RSS/Atom / NewsNow 情报源服务、API、安全校验、分析接入和配置兼容测试。
- 新增/更新日韩市场识别、股票索引、YFinance 行情兜底、Web 自动补全和输入校验测试。
- 新增/更新 LLM usage、运行流、AlphaSift、发布说明生成和移动端交互相关回归。


## [3.22.0] - 2026-06-13

### 发布亮点

- feat: 新增 DecisionSignal 独立存储与 API、运行流快照 API 和 Web 运行流视图，补齐建议动作结构化字段与历史/回测展示链路。
- feat: AlphaSift 热点题材链路升级为新版合约，支持热点榜单、题材详情、发酵路线、概念股详情、缓存与兜底数据源。
- feat: 个股分析默认注入当日大盘环境摘要，并在高风险/退潮环境下软化激进买入建议。
- fix: 修复问股历史追问标的上下文、自选股等价代码匹配、低质量新闻过滤、运行流脱敏与 AlphaSift 热点详情展示等稳定性问题。

### 新功能

- 新增独立 `DecisionSignal` 存储、Repository、Service 与 `/api/v1/decision-signals` API，支持来源/市场/股票/动作/期限/阶段去重、查询、续期、状态更新、懒过期、持仓过滤和敏感信息脱敏。
- 新增分析任务与历史报告运行流快照 API，提供 lanes、nodes、edges、events、summary 等统一契约，并从任务队列、运行诊断和 AnalysisContextPack overview 构建脱敏数据流/信息流。
- Web 端为活跃任务、历史报告和大盘复盘报告补充运行流视图入口，支持查看运行摘要、拓扑节点、事件流和基础排障详情。
- 新增 AlphaSift 热点题材链路：后端提供 `/api/v1/alphasift/hotspots` 与 `/api/v1/alphasift/hotspots/{topic}` API，Web 选股页新增热点题材区域并支持发酵路线与概念股查看。

### 改进

- 个股分析新增按当日/市场复用的大盘环境摘要，普通 Pipeline 与 Agent 分析 Prompt 可读取低敏大盘背景；新增默认开启的 `DAILY_MARKET_CONTEXT_ENABLED` 配置，用户仍可显式关闭。
- 个股分析与历史/回测展示新增可选八态 `action` / `action_label` 建议动作字段，保留 `operation_advice` 自由文本和 `decision_type=buy|hold|sell` 统计口径。
- 补充 Web decision-signals typed API wrapper 与契约隔离测试，暂不接入 UI。
- 完善运行时日志上下文，补充 logger name、触发来源、市场统计与实时行情预取链路状态，便于排查调度、API、Bot 和数据源降级路径。
- 持仓管理页新增持仓账户删除入口，复用现有账户软删除接口，误建账户会从默认列表、快照、风险、录入入口和事件列表隐藏且不物理清理历史流水。
- AlphaSift 依赖锁定更新到 `d038c52c468543726fc1fd830b53c27d3f09d6da`，并为新版 last-good snapshot、日线历史、行业/概念 provider cache、hotspot 榜单、题材发酵路线、概念股详情、上次成功热点缓存与 post-analysis 元信息补齐 DSA 运行期和 Web 适配。
- AlphaSift 热点题材读取默认优先使用上次成功缓存，手动刷新才实时拉取并覆盖缓存，实时拉取失败时尽量回退旧缓存。
- AlphaSift 热点题材区域改为默认折叠，展开并选中具体题材后再读取详情；发酵路线改为带时间标记的时间线展示，概念股可点击进入首页并直接启动分析。
- AlphaSift 热点题材数据链路复用同一次东方财富板块异动快照，并从真实涨跌幅、异动次数和高频个股推导趋势分、持续分、阶段与龙头样本。
- AlphaSift 热点题材刷新在合约层返回少量或缺少关键字段时改用 DSA 东方财富板块异动直连榜单，忽略少于 3 条的本地热点缓存，并补齐板块兜底字段。
- AlphaSift 热点题材卡片改为更紧凑的多列布局，概念股列表改为独立“分析”按钮触发个股分析；详情优先合并东方财富成分股、同花顺解析和板块异动龙头兜底并按日聚合发酵时间线。
- AlphaSift 热点题材详情新增 DSA 侧 30 分钟磁盘缓存，重复点开同一题材时复用发酵时间线与概念股详情；题材事件仅展示 AlphaSift 合约时间线、同花顺摘要、已配置新闻搜索或东财板块异动等真实来源。
- AlphaSift 热点题材消息催化改为摘要展示：配置 LLM 时优先压缩为一句题材催化摘要，未配置或调用失败时回退本地短摘要。
- AlphaSift 热点题材列表新增可选 `include_details` 详情预取，Web 默认随热点列表批量带回 Top 题材发酵路线与概念股并复用前端内存缓存；新闻催化在 LLM 不可用时改为本地事件归纳。
- 改造 `main.py --webui-only` 启动行为：若 FastAPI 监听端口已被占用，启动即 fail-fast 抛出明确错误并退出。

### 修复

- 问股从历史报告进入后的追问会持续携带当前标的，切回或重载已有会话时可从历史消息恢复基础当前标的，并由后端阻断未明确切换时的错误股票工具调用、交易所片段和指标缩写误路由。
- 自选股加入和删除按等价股票代码匹配港股及大小写美股变体，避免 `00700`、`HK00700`、`00700.HK` 或 `aapl`、`AAPL` 被误判为不同标的。
- 收紧建议动作 legacy fallback：否定/回避表达、中文金融上下文、`buy or sell`、多 guard 歧义文本以及英文复合词不再误渲染成 action badge；有结构化 `action` 时回测/历史趋势等入口按界面语言显示 action 标签。
- 股票新闻与多维情报搜索在相关度排序后新增域名无关的准入过滤，剔除下载/安装包/应用评分页及成人/招嫖服务垃圾页，并在同批已有有效标的/行业候选时移除 `score=0` 背景填充项。
- 修复历史报告运行流快照在混合时区事件时间戳下返回 500 的问题。
- 修复运行流 live SSE 事件未复用快照层递归脱敏规则的问题，避免本地路径、prompt/raw response、代理头等敏感诊断字段在 refetch 前短暂暴露。
- AlphaSift 热点题材默认加载在无缓存且旧适配层缺少 `alphasift.hotspot` 模块时返回空态，不再一打开选股页就显示 AlphaSift 未就绪；手动刷新仍会提示依赖需更新。
- 为 THS 发酵路线补充列名兜底：当 `stock_board_concept_summary_ths` 返回缺列时仅跳过该来源富化，不影响热点题材详情 API 返回。
- 桌面发布打包改用冻结可执行文件运行时探针校验 `alphasift.dsa_adapter`，避免 macOS PyInstaller 将模块内嵌进可执行文件时被文件系统/zip 扫描误判为缺失。
- AlphaSift 热点题材详情展示改为优先使用后端融合后的 `route`，避免旧 `timeline` 覆盖新闻/LLM 摘要；手动刷新热点榜单时会同步绕过同题材详情缓存。

### 文档

- README 与繁中 README 快速开始入口补充视频教程链接，并将桌面客户端入口文案调整为客户端配置教程。
- 补充 `docs/alphasift-integration.md`：明确 AlphaSift 锁定 commit 来源、Hotspot 契约边界、LLM/LiteLLM 兼容语义与关闭开关下回退路径。
- 补充 #1381 运行时范围、兼容边界、官方语义依据与常规发布回滚说明。

### 测试

- 覆盖 #1381 后端 runtime 与兼容核验：`tests/test_main_schedule_mode.py`、`tests/test_pipeline_daily_market_context.py`、`tests/test_daily_market_context.py`、`tests/test_daily_market_context_guardrail.py`、`tests/test_agent_executor.py`、`tests/test_config_env_compat.py`、`tests/test_config_registry.py` 与 `apps/dsa-web/tests/system_config_i18n.test.ts`。
- 新增/更新 AlphaSift 后端回归：`python -m pytest tests/test_alphasift_api.py -q`、`python -m pytest tests/test_docker_entrypoint.py -q`、`python -m pytest tests/test_main_schedule_mode.py -q -k "start_api_server_fails_before_thread_when_port_is_busy"`。

## [3.21.0] - 2026-06-07

### 发布亮点

- feat: 新增 Web UI 中英文界面语言切换和飞书 App Bot 通知模式，提升多人部署和企业通知场景体验。
- feat: 大盘复盘报告、历史入口和个股栏继续收口到结构化数据与统一 Markdown/GFM 渲染，Web/API 人工触发入口不再被交易日 gate 短路。
- feat: AlphaSift 选股链路改为可恢复后台任务，并完善 DSA LLM runtime bridge、默认适配层预置和兼容回归。
- fix: 修复英文界面残留中文、诊断展示、运行时环境变量展示、健康检查、桌面更新路径、工作流变量读取和多处 Web 窄布局问题。

### 新功能

- WebUI 新增独立界面语言状态与中英文切换入口，覆盖主导航、首页、登录、设置页和通用控件文案；UI 语言与 `report_language` 解耦，不改写报告语言链路。
- 飞书通知新增应用机器人（App Bot）模式，支持通过 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID` 配置，无需额外创建自定义机器人。
- Web 大盘复盘报告新增专用展示视图，历史入口和首页即时结果统一使用 Markdown/GFM 渲染并隐藏个股专属模块。
- 大盘复盘新增结构化 `market_review_payload`，Web、历史详情和推送统一基于结构化数据渲染，并保留 Markdown 兼容展示。
- 新增默认关闭的 AlphaSift 选股页签，通过 `ALPHASIFT_ENABLED` 明确控制，并保留 `/install` 作为显式修复路径。

### 改进

- Web/API 大盘复盘人工触发入口不再因交易日检查或相关市场休市而短路跳过；定时任务、GitHub Actions 手动运行和 CLI 默认入口仍保持原交易日 gate。
- AlphaSift Web 选股改为后台任务提交与状态轮询，新增可恢复任务状态展示，避免外部快照、行情或 LLM 变慢时浏览器长请求超时。
- AlphaSift 选股 API 与服务层收敛到 `AlphaSiftService`，endpoint 仅做路由参数接收与错误映射。
- AlphaSift 与 DSA 的运行时 LLM 兼容桥接改为调用期注入，保留 `provider/model/base_url/custom headers/fallback` 语义链路，不做持久化迁移。
- Web 首页侧栏不再单独展示大盘复盘历史集合，最新大盘复盘作为 `MARKET` 并入个股栏，按最近分析时间参与排序，并复用个股栏的选择、删除、完整报告与历史趋势查看能力。
- 多股通知报告将市场阶段收敛为总览下方单行 `市场状态`，不再在每只股票摘要下重复展示数据质量和限制详情。
- API 错误响应构造收敛到共享 helper，保持既有错误 envelope 形状并降低 endpoint 重复代码。
- WebUI 绑定公网地址或 CORS 全开放且未启用管理员认证时新增运行时 warning；仅增加可观测性，不阻断启动、不改写配置。
- 数据库初始化新增 `schema_migrations` baseline 标记表与幂等记录，用于后续 schema 演进追踪；不迁移、不清理、不改写既有业务表数据。
- #1386 P6 复用市场阶段与 AnalysisContextPack 公开摘要联动告警、持仓手动分析、历史、回测和通知展示，不新增数据库迁移。

### 修复

- Web 英文界面补齐回测、组合风险与告警规则相关文案本地化，避免英文模式下残留中文筛选器、按钮和枚举标签。
- 综合情报搜索中的机构分析与业绩预期维度改用 180 天 provider 请求窗口，避免默认短新闻窗口漏掉财报、研报等周期性财经材料。
- Web 个股栏和历史卡片在窄布局下不再让市场阶段标签遮挡股票名称。
- 问股自由文本追问不再将 TTM、PE、YOY 等金融缩写误识别为新股票代码。
- [修复] GitHub Actions 每日分析工作流读取 SearXNG 自建实例地址时支持 Variables 优先、Secrets 回退，修复仅配置 Variables 时 URL 不生效的问题。
- Web/桌面端左侧导航选中态改用 border 实现，避免蓝色竖条指示器溢出侧栏边界；侧栏展开宽度 116px -> 136px，新增 rail 紧凑模式。
- Windows 桌面端自动更新安装目录不再预先加引号，避免带空格路径在自动安装时触发“缺少快捷方式 / 找不到 Daily Stock Analysis.exe”的系统弹窗。
- Agent 分析路径生成 AnalysisContextPack overview 前复用已落库日线分析上下文，避免日线已抓取成功仍显示 `daily_bars_missing`。
- 修正大盘复盘结构化 `breadth` 的可用性判断：当市场不支持或抓取失败时不下发 `breadth`，前端展示“暂无数据”，避免误导性 0 值。
- 大盘复盘语言行为遵循全局 `report_language`，并在美股中文场景下本地化市场标签与策略蓝图，避免混入英文策略段落。
- Docker Web 设置页读取配置时在活跃 `.env` 文件缺项时回退展示启动注入的同名环境变量，并补清相关挂载边界文档。
- 报告页运行诊断会区分数据源抓取成功与进入 LLM 分析输入，相关新闻区标注为报告页补充/后续检索资讯，避免与输入数据块状态互相误读。
- `/health` 根路径健康检查现在始终返回 JSON，避免静态 Web fallback 吞掉健康探针；`/api/health` 与 `/api/v1/health` 继续保持兼容。
- `ALPHASIFT_ENABLED` 关闭时不触发 `alphasift` 运行时注入；开启后优先复用已配置的 DSA/provider 配置并注入 `LITELLM_*` 与 `LLM_*` 运行时变量。
- 补齐 openai-compatible 场景下 base URL、`extra_headers` 与 `LITELLM_FALLBACK_MODELS` 的兼容路径与回退链验证。
- 桌面/镜像打包链路保持与运行时一致的 AlphaSift 适配层预置，避免 `pip install` 作为线上修复依赖。

### 文档

- 明确 Issue #777 UI 语言切换采用仓内 `UiLanguageContext` + `uiText` 实现，持久化 key 为 `dsa.uiLanguage`，并补充对应可视化验收指引。
- 明确大盘复盘展示链路、结构化 payload、语言行为、交易日 gate 差异和回滚边界。
- 补充 LLM / LiteLLM 兼容键在 Settings 展示与校验上下文中的回退边界，说明不改写、不迁移、不清理用户现有 provider/model/base URL 持久化配置。
- 补齐 #1602 运行诊断口径修复覆盖范围，说明仅统一输入与展示口径，回滚方式为常规发布回滚。
- 明确 AnalysisContextPack P6 文档、迁移与回滚边界，并同步既有 `SAVE_CONTEXT_SNAPSHOT` 到 `.env.example`、配置注册表、Web 设置帮助和完整指南。
- 补齐 #1386 P7 盘前/盘中/盘后分析的入口、迁移、回滚和用户可见说明。
- 为 AlphaSift runtime bridge 增加官方兼容依据落点，明确 provider/model/base_url/extra_headers/fallback 与回退边界。

### 测试

- Web 方向执行 `npm run lint`、`npm run build`、相关 Vitest 和 smoke 命令；未设置 `DSA_WEB_SMOKE_PASSWORD` 时 smoke 用例按设计 skip。
- Web 测试运行时声明 Node `>=20.19.0 <27` 与 npm `>=10`，并补 localStorage 测试兜底以稳定 Vitest。
- 增补 AlphaSift runtime bridge 与打包脚本静态验证，覆盖 `LLM_CHANNELS`、`LITELLM_FALLBACK_MODELS`、`alphasift.dsa_adapter`、`--collect-all alphasift`。

### chore

- 移除随 issue / PR 验收流程误入库的截图资产，并明确一次性截图证据应保留在 PR 描述、评论、附件或 artifact 中，不作为仓库文件合入。

## [3.20.0] - 2026-06-03

### 发布亮点

- feat: 新增 AlphaSift 选股入口、自动安装与稳定适配层，支持 Web 策略执行、LLM 重排展示和默认关闭的可控启用。
- feat: 完善个股历史、自选队列、市场阶段与 AnalysisContextPack 可见性，增强 Web 报告和 API 的结构化上下文能力。
- feat: MiniMax 默认模型升级到 `MiniMax-M3`，并补齐相关价格、预设和测试覆盖。
- fix: 修复健康检查、Windows 桌面更新与首次运行编码、ETF 日线 secid、LLM base_url 校验和 Agent 日线上下文误判等稳定性问题。

### 新功能

- 新增默认关闭的 AlphaSift 选股页签，通过 `ALPHASIFT_ENABLED` 开启后经由稳定适配层读取策略并执行选股。
- Web 首页左侧栏改为个股栏，按股票去重展示，大盘复盘置顶，点击个股加载最新报告，支持按代码变体（.SZ/.SH/.SS）归一化去重合并。保留全选、批量删除和删除确认入口；新增按股票代码批量删除 API `DELETE /api/v1/history/by-code/{stock_code}`。
- 报告详情右侧栏新增自选操作入口，支持查看当前股票是否在自选队列、一键加入或移除；大盘复盘报告不显示该操作。
- 问股页面输入区上方新增自选操作按钮，用户发送包含股票代码的消息后自动显示加入自选/从自选删除入口。
- Web 报告页新增同股历史趋势抽屉入口，历史列表摘要补充趋势、摘要、模型和分析时行情字段，支持按当前股票查看历史分析并加载更多。
- AnalysisContextPack P4 低敏 overview 接入历史详情、同步分析响应、completed 任务状态和 Web 报告页，展示数据块状态、来源、缺失原因与降级摘要。
- #1386 P5 为个股分析报告新增 `dashboard.phase_decision` 盘中决策护栏，并在保存历史前按市场阶段与数据质量限制高置信盘中买卖结论。
- #1386 P4a 新增 `analysis_phase=auto|premarket|intraday|postmarket` API 参数，并在异步任务 accepted、内存 status、list、SSE 与分析 pipeline 中透传请求阶段。
- #1386 P4b Web 报告页新增最终市场阶段标签，任务面板展示请求阶段，并复用 AnalysisContextPack 低敏数据质量摘要。
- MiniMax 渠道模型列表升级：新增 `MiniMax-M3` 并作为默认，按官方 OpenAI-compatible 文档支持 1M 输入上下文（项目保守注册为 `<=512K` 价格档：context_window 512K、`max_tokens` 128K，对应 $0.6/M 输入、$2.4/M 输出，>512K 输入价格档未建模），保留 `MiniMax-M2.7` 与 `MiniMax-M2.7-highspeed`，并保留 `MiniMax-M2.5` legacy 价格条目以兼容现有用户配置的成本估算。Web 设置页 MiniMax 预设模型与价格按 M3 刷新。
- 新增 AnalysisContextPack P1 内部契约与脱敏序列化测试。
- 市场阶段低敏摘要接入历史详情、同步分析响应和 completed 任务状态的 report metadata。

### 改进

- 首次运行配置校验补充缺失 AI Key、空 STOCK_LIST、Telegram/邮件成对字段和 Webhook URL 前缀诊断。
- AlphaSift 选股入口在 Web 侧边栏中移动到“问股”下方，贴近 Agent/研究辅助工作流。
- Docker 镜像构建阶段预置默认 AlphaSift 适配层，与桌面发布包一样避免运行期额外安装。
- AlphaSift 选股改为依赖 `alphasift.dsa_adapter` 的稳定接口，Web 策略列表由 AlphaSift 动态提供，不再在前端硬编码。
- AlphaSift 选股页补充 Run ID、快照数、过滤后数量、因子和风险详情，展开候选时展示真实明细，并暂时仅开放当前支持的 A 股市场。
- Web 设置页新增 AlphaSift 选股开关卡片，可直接开启或关闭选股页签。
- 开启 AlphaSift 选股时先切换 `ALPHASIFT_ENABLED` 并检查适配层可用性，缺失时自动调用受控安装接口，不再要求用户额外点击安装。
- AlphaSift 已开启但适配层缺失时，策略列表和选股接口会串行化自动安装锁定来源，并强制重装以覆盖旧版 `alphasift` 包。
- AlphaSift 选股页合并重复的快照源 fallback 提示，并保留 AlphaSift 自身的 Tushare 优先快照源逻辑。
- AlphaSift 选股页在 LLM 重排降级时展示 warning/source error/parse error，并避免把本地因子评分误显示为 LLM 判断。
- Web 设置页不再把 `ALPHASIFT_ENABLED` 作为普通数据源配置项重复展示，该值仅作为“开启选股”按钮背后的持久化状态。
- AlphaSift 关闭时隐藏 Web 左侧“选股”导航入口，避免误导未开启用户。
- 补充 AlphaSift 选股自定义策略显示逻辑，避免未匹配预设项时误显示“均衡多因子”。
- 新增 GET /api/v1/history/stocks 端点按 code 分组返回不重复个股列表；新增 GET /api/v1/stocks/watchlist、POST /api/v1/stocks/watchlist/add、POST /api/v1/stocks/watchlist/remove 端点支持自选队列增删查。STOCK_LIST 读写保持原样，不做自动归一化；add/remove 时归一化比较判断等价代码变体。
- 新增 useWatchlist hook 统一管理自选队列前端状态，复用 SystemConfigService 的 STOCK_LIST 配置项实现持久化。
- AnalysisContextPack P5 增加数据质量评分、`fetch_failed` 状态、Prompt 数据限制区块和 Web 低敏质量展示。
- #1386 P2-full 在 AnalysisContextPack Prompt 数据限制中追加市场阶段与降级数据的交叉约束，并修正中文分析 Prompt 的阶段化行情标签。
- 通知报告默认发送路径恢复既有渠道兼容转换与分片逻辑，新增 renderer 能力仅保留为未来扩展基础。
- 关联板块缺少类型数据时改为单行展示板块名称，避免生成整列 `N/A` 的板块表格。
- 优化 Web 报告详情页信息层级，将输入数据块和运行诊断下移为主体内容后的折叠辅助信息。
- 盘中分析补齐实时行情获取时间、provider 时间、stale、fallback 与 partial/estimated 标记，供 AnalysisContextPack 映射输入数据限制。

### 修复

- Agent 分析路径生成 AnalysisContextPack overview 前复用已落库日线分析上下文，避免日线已抓取成功仍显示 `daily_bars_missing`。
- 注册 /api/v1/health 路由并加入认证豁免，修复该路径返回 404 以及开启 ADMIN_AUTH_ENABLED 后健康探针收到 401 的问题。
- Windows 本地首次运行环境检查兼容非 UTF-8 控制台输出，并将 `requirements.txt` 注释改为 ASCII 以降低默认代码页下的依赖安装失败概率。
- AlphaSift DSA 适配层默认开启 LLM 重排，后端显式请求 `use_llm=True`，选股页展示 LLM 分数、判断、覆盖率和关注项。
- AlphaSift 嵌入 DSA 时复用 DSA 已解析的 LLM 模型、渠道和密钥配置，避免 Web 已配置 LLM 但选股 LLM 重排仍因缺少 provider key 降级。
- AlphaSift 选股复用 DSA LLM 路由时过滤未声明的托管 provider 备选模型，并把已声明渠道模型补入回退链，避免残留 Gemini fallback 覆盖可用的 DSA 渠道。
- AlphaSift 默认安装来源改为锁定 commit 的受信任 GitHub 地址；桌面模式自动安装不要求管理员会话，非桌面部署要求管理员认证会话，并继续限制安装来源。
- 修复 Web 开启 AlphaSift 时先安装后写配置导致默认关闭状态无法开启的问题。
- AlphaSift 状态与安装接口不再返回 `install_spec` 明文，仅返回 `install_spec_is_default` 等非敏感状态字段。
- AlphaSift 状态探测区分可选依赖缺失与非预期异常，异常场景记录 warning 并返回非敏感诊断信息。
- 调整 AlphaSift 筛选调用兼容：`screen` 以 `max_results` 为主并支持历史 `max_output` 关键词，同时允许策略透传以对齐前端手动策略参数。
- AlphaSift Web 选股请求使用独立长超时，避免开启 LLM 重排后被通用 30 秒 API 超时提前中断。
- 桌面端打包阶段预置 AlphaSift 并收集适配层，避免发布包运行时再要求管理员自动安装。
- AlphaSift 自动安装仅在 `status` 诊断为 `missing_module` 时触发（仅模块缺失场景）；适配层可导入但运行时异常不再自动 `pip install`，而是返回 `424` 并保留诊断，避免把真实运行时故障掩盖为重装。
- 收口 Web 中文界面残留英文文案与设置页 help 缺口，回测页改为中文展示，并让 Web 设置页仅展示已注册且带说明的配置项。
- Windows 桌面端自动更新静默安装时显式复用当前安装目录，避免自定义安装目录场景下卸载旧版本文件失败。
- Windows 安装器重试旧卸载器时对 `_?=` 安装目录参数加引号，修复旧版本安装在带空格路径时返回 2 导致自动更新失败。
- Windows 桌面端自动更新传给 NSIS 的 `/D=` 目录参数在包含空格时自动加引号，避免安装位置注册表被截断。
- 加固 LLM channel base_url 校验，避免解析差异导致 SSRF 绕过。
- 修正 efinance ETF 日线 Eastmoney secid 路由，避免沪市 ETF 被按深市 quote id 查询导致日线为空。

### 文档

- 明确 AlphaSift 与 LiteLLM 兼容边界：仅桥接 DSA 已声明 provider/model/base URL 为调用期注入，不对 `.env` 做 provider/model 路由迁移；回退方式为关闭 AlphaSift 并恢复原有 `LITELLM_*`/`LLM_*` 配置。
- 明确 AlphaSift 仅复用 DSA 现有 LLM/LiteLLM 配置语义，不新增 `LITELLM_MODEL`、`OPENAI_MODEL`、`OPENAI_BASE_URL`、`LLM_TIMEOUT_SEC` 等模型语义迁移；失败提示与回退路径统一沿用既有系统配置链路，仅影响 AlphaSift 选股能力本身。
- 明确 AlphaSift 自动安装来源锁定、`missing_module` 与运行时异常行为边界，以及 LLM/provider/base URL 与自定义通道回退路径，便于问题溯源与回滚到原有 LLM 配置。
- 明确同股历史趋势新增模型字段为历史快照展示元数据，不影响运行时 LLM Provider/Model/Base URL 路由与配置迁移清理；回退方式为按常规发布回滚本变更。
- 明确 #1311 的兼容性边界：渲染层仅消费分析结果 `model_used` 展示字段，未改动 `wechat/slack/feishu/telegram` sender 发送链路，不触发 provider/model/base_url 兼容迁移。
- 明确 AlphaSift 锁定 commit 的 `alphasift.dsa_adapter` 契约依据，以及当前 DSA API/Web 调用结构的兼容边界。
- 明确 Settings 页面对 LLM 配置仅做展示分组与字段归并，不改写或触发 LLM 迁移/回退路径；兼容现有 `LLM` 配置保存与回退语义。
- 新增 AnalysisContextPack P0 上下文盘点。
- 补齐告警中心 P8 文档与配置收口说明，明确 legacy JSON、高级规则、Web/API、Docker、GitHub Actions 与 Desktop 边界。

### 测试

- 同步更新 `llmProviderTemplates`、LiteLLM fallback pricing 与 MiniMax 预设相关单测，断言新默认模型。
- 补充 ETF 日线数据源路由、输入变体、fallback 与 MA 字段回归覆盖。

### chore

- 新增通知报告渠道能力画像、PreparedMessage 与结构感知 Markdown 分片基础设施，为 #1311 全渠道渲染适配打底。
- 预置企业微信、飞书、Telegram、钉钉、Slack 平台 renderer 元数据，暂不改变默认推送报告入口和可见版式。

## [3.19.0] - 2026-05-29

### 新功能

- 落地 #1391 Phase 1 运行诊断最小链路：任务/SSE 追加 trace_id，并记录日线与实时行情 ProviderRun 快照。
- 告警中心新增 P7 大盘红绿灯结构化规则，支持 `market_light_status` 与 `market_light_score_drop` 并复用现有 worker、触发历史、通知和冷却链路。
- 落地 #1391 Phase 2 运行诊断摘要：生成用户可读 RunDiagnosticSummary，提供历史报告诊断 API 与脱敏复制文本。
- 落地 #1391 Phase 3 运行诊断可见性：报告详情和任务面板默认折叠展示运行状态、trace 与可复制排障信息；后端通过 `api/v1/history/{record_id}/diagnostics` 与 `context_snapshot.diagnostics` 提供历史链路回填。
- 新增 AnalysisContextPack P1 内部契约与脱敏序列化测试。
- 新增 AnalysisContextPack P2 builder，从普通分析 pipeline 已有 artifacts 组装内部上下文包。
- 问股新增默认关闭的可见对话上下文压缩，支持 Web 开关、Agent 高级 preset、滚动摘要和最近轮次原文保护，降低长会话 token 消耗。
- 股票自动补全索引默认支持从 GitHub main 远程刷新并缓存到本地，Web/CLI 分析入口失败时自动降级到内置索引，降低摘帽和更名后旧简称污染分析的概率。
- 普通分析与 Agent 运行时 Prompt 接入 AnalysisContextPack 低敏摘要，保持 history/API/Web 输出兼容。

### 改进

- `scripts/fetch_tushare_stock_list.py` 可对 A 股中带 `XD`/`XR`/`DR`/`N`/`C` 前缀的名称进行回填修正，供自动补全刷新流程默认使用。
- Web 路由页面改为按需加载，降低首包体积并增加路由加载失败恢复提示。
- Web 完整报告 Markdown 抽屉改为按需加载。
- 新增市场阶段推断基线并明确盘前、盘中、午休、临近收盘、盘后和非交易日语义。
- 新增运行态市场阶段上下文构造与降级测试。
- 设置页配置帮助阶段性补齐 Web 设置页实际展示/可配置字段的中英双语文案，覆盖 Agent、回测、报告、通知路由、系统运行时、AI legacy、数据源和通知高级配置。
- P2-min：LLM Prompt 注入市场阶段上下文。

### 修复

- 股票自动补全索引生成缺少 `pypinyin` 时改为直接失败，避免写出缺失拼音字段的降级索引。
- 归一腾讯实时行情成交量为股口径，避免量能变化倍数被放大并误导分析报告。
- Docker 默认部署移除 `.env` 单文件挂载，避免 WebUI 保存配置时因 `os.replace` 更新挂载点触发 `Device or resource busy`。
- 收敛 #1391 Phase 0 A 股代码归属边界：补齐 `SH`/`SZ` 前缀场景的归属一致性，明确 `data_provider/baostock_fetcher.py`、`data_provider/pytdx_fetcher.py`、`data_provider/tushare_fetcher.py` 的本轮修复范围。
- 修复 `STOCK_LIST` 使用裸 A 股代码时 Baostock 等数据源 fallback 的内部格式转换，保持用户配置继续使用 6 位股票编号。
- Windows 桌面端自动更新在用户确认重启安装后改为静默执行安装器，并在停止内置后端后清理进程引用，降低安装器提示“每日股票分析无法关闭”的概率。
- macOS 桌面端将运行时配置迁移到用户数据目录，并在旧 `.app` 包内文件仍可访问时迁移 `.env`、数据库和日志，避免后续替换升级后重新配置。
- 恢复 Agent/历史兼容快照中的关联板块与板块联动字段提取，修复新版首页报告缺少“板块联动”的回归问题。
- 修正 Web 设置帮助中 legacy 告警 JSON 字段名与静默时段投递语义说明。
- 修复 Web 中文设置页在数据源、通知、系统与 Agent 区域的配置标题、说明和关键下拉选项漏翻问题。
- 修复问股会话切换和首页任务重连后可能残留 Agent/分析任务进行中状态的问题。
- 问股 single-agent 新增 provider-aware trace 分轨，跨轮保留 DeepSeek V4 thinking + tool-call 的 `reasoning_content` 与工具协议材料。
- 为 Akshare 新浪/腾讯 A 股历史兜底接口增加调用级超时，并补齐 Tushare `605xxx` 沪市代码路由回归测试，避免定时分析因数据源无响应而挂起。
- 将 `exchange-calendars` 依赖下限提升到 `4.13.0`，避免 pandas 3 环境导入交易日历时因 Timedelta 单位 `T` 失效导致分析失败。
- 交互式命令（钉钉会话、飞书会话、Telegram）触发的分析结果只回到来源会话，不再同时广播到静态通知渠道。
- 适配 Longbridge OAuth 2.0 认证与 token 缓存恢复，避免新后台无 Legacy Access Token 时长桥数据源被误判为未配置。
- Longbridge OAuth 路径在当前 SDK 不支持 `OAuthBuilder` / `Config.from_oauth` 时明确日志降级，避免 Linux/Docker 仅可安装旧 SDK 时构建失败。
- 兼容 YFinance 日线返回未命名日期索引的场景，避免标准化后缺少 `date` 列导致美股日线 fallback 中断。

### 文档

- 新增 #1391 Phase 0 运行诊断契约文档，明确 trace_id、诊断摘要、关键链路范围与脱敏/fail-open/retention 边界。
- 补齐告警中心 P8 文档与配置收口说明，明确 legacy JSON、高级规则、Web/API、Docker、GitHub Actions 与 Desktop 边界。
- 说明本次桌面修复仅覆盖 Windows NSIS 更新安装链路与后端进程生命周期清理；未改动设置项保存/模型运行时清理语义。移除此前误入的 `docker/Dockerfile` `npm registry` 变更，恢复部署构建与更新修复的职责隔离。
- 新增 AnalysisContextPack P0 上下文盘点，明确字段质量状态、现有状态映射和首版 pack 边界。
- 明确 #1391 Phase 2 的结构化检测告警为非配置迁移信号：`agent_max_steps`/`agent_orchestrator_timeout_s` 非法值会 fallback 至默认并产生日志告警，新增诊断链路仅新增 `context_snapshot`/`RunDiagnosticSummary` 读写字段，不改写 `litellm_model`、`agent_litellm_model`、`openai_base_url`、LLM channel 路由或配置迁移语义。
- 补充 #1391 Phase 3 兼容性说明：记录后端诊断持久化、历史查询与通知回写链路变更边界与回滚策略，并补齐后端门禁级验证要求。

### 测试

- 收敛 #1391 Phase 3 后端/API 与 Web 回归检查：`./scripts/ci_gate.sh`、`test_pipeline_market_phase_context.py`、`test_analysis_api_contract.py`、`test_analysis_history.py`、`npm run lint`、`npm run build`。
- 执行 `python -c "import exchange_calendars as xcals; xcals.get_calendar('XSHG'); print('ok')"` 通过验证，以覆盖导入与交易日历初始化兼容性。

## [3.18.0] - 2026-05-21

### 发布亮点

- feat: 告警中心扩展到 P2-P6，补齐后台评估、真实通知结果、业务冷却、技术指标规则，以及自选股 / 持仓 / 账户联动规则。
- feat: 个股分析支持策略选择，新增热点题材、事件驱动、成长质量和预期重估策略，并为 HK/US 报告补充基本面、财务摘要、股东回报和关联板块。
- feat: 新增 Finnhub / AlphaVantage 美股数据源适配器，扩展美股日线 failover 链，提升美股行情获取韧性。
- fix: 修复桌面端发布打包、分析状态接口、AlphaVantage 涨跌幅、持仓实时估值、告警历史去重、数据库冷启动和 fallback pricing 注册等稳定性问题。

### What's Changed

- feat: Add alert-center P2-P6, Web strategy selection, HK/US fundamental context, static-report financial sections, and Finnhub / AlphaVantage US-market fallback.
- improve: Refine LiteLLM parameter recovery, yfinance currency/dividend handling, RSI calculation, market-review presentation, stock-news relevance ranking, and report table rendering.
- fix: Harden desktop packaging/update assets, completed analysis-status responses, AlphaVantage pct_chg routing, portfolio realtime snapshots, alert trigger dedupe, DatabaseManager cold start, and fallback pricing registration.
- docs/tests: Add beginner setup and settings-help docs, document compatibility/rollback boundaries, and extend regression coverage for API, alert, packaging, and release paths.

## [3.17.1] - 2026-05-16

### 发布亮点

- fix: 桌面端 Windows / macOS 打包脚本显式关闭 electron-builder 自动发布，避免 tag 构建时因缺少 `GH_TOKEN` 在本地打包完成后失败；Release workflow 继续负责上传和发布产物。

### What's Changed

- fix: Add `--publish never` to the Windows and macOS Electron packaging scripts so tag builds only create local artifacts and GitHub Actions handles release upload/publish.

## [3.17.0] - 2026-05-16

### 发布亮点

- feat: 新增 Alert API MVP，支持告警规则 CRUD、启停、一次性测试以及触发/通知结果查询，首版覆盖 `price_cross` / `price_change_percent` / `volume_spike` 并保持 legacy 配置兼容。
- feat: 通知网关新增 ntfy 与 Gotify 一等渠道，并补齐通知降噪、静态渠道隔离、诊断、Web 测试和 GitHub Actions env 对照校验。
- feat: Windows 桌面安装版接入自动更新安装链路，支持后台下载、确认重启安装、运行时文件备份/恢复和发布产物元数据校验。
- improve: 大盘复盘新增概念排行、人气股、涨停池等底层数据源，支持指数涨跌颜色语义配置，并将复盘结果写入历史记录。
- improve: Web 设置页支持 `.env` 配置备份导入/导出和通知/Agent 区域局部错误兜底；报告新增 `REPORT_SHOW_LLM_MODEL` 开关控制模型信息展示。
- improve: Docker 启动入口自动修复挂载目录权限并在日志目录不可写时降级到控制台，减少普通部署的手动修复步骤。
- fix: 数据源缺凭据或连接失败时更温和降级，Longbridge / Pytdx 加入冷却，资金流缺失时避免输出高置信买入结论。
- fix: 分析与报告链路兼容 OpenAI-compatible `content_blocks` 响应，归一策略价格字段，并修复大盘复盘滚动和历史记录丢失问题。
- docs: 补齐通知、告警中心、桌面打包、README / 指南和 PR title 治理说明，明确多处配置兼容边界与回滚路径。
- test: 增加 Alert API、通知降噪/路由、Docker entrypoint、数据源预取、桌面更新链路和分析历史等回归覆盖。

### What's Changed

- feat: Add an Alert API MVP with rule CRUD, enable/disable, one-shot testing, trigger history, notification results, and legacy config compatibility.
- feat: Promote ntfy and Gotify to first-class notification channels with Web tests, routing, Actions integration, diagnostics, and noise control.
- feat: Add the Windows desktop auto-update install flow with runtime state backup/restore and release artifact metadata verification.
- improve: Extend market review data sources, add configurable index color semantics, and persist market review results into analysis history.
- improve: Add Web `.env` backup import/export, local settings panel error boundaries, and a report model visibility toggle.
- improve: Harden Docker startup by repairing mounted directory permissions and falling back to console logging when mounted logs are not writable.
- fix: Cool down unavailable optional fetchers, reduce noisy Longbridge/Pytdx retries, and downgrade buy advice when capital flow data is missing.
- fix: Handle OpenAI-compatible `content_blocks`, normalize strategy price fields, and recover market review scrolling/history behavior.
- docs/tests: Update notification, alert, desktop packaging, README/guide, and governance docs; add focused regression coverage for the new release paths.

## [3.16.0] - 2026-05-10

### 发布亮点

- feat: Web 首页新增“大盘复盘”触发入口、任务轮询与完成后报告直出；首次启动配置状态可提示缺口并引导到系统设置。
- feat: 新增通知路由策略，支持按 report、alert、system_error 将通知收窄到指定渠道；Web 设置页支持通知渠道一键测试。
- feat: 系统设置页新增配置项帮助入口与多语言帮助文案基础设施，首批覆盖自选股、LLM 主模型、LLM 渠道、飞书 Webhook 与 WebUI 监听地址。
- improve: 大盘复盘 API、CLI、Bot 共用 `build_market_review_runtime` 装配路径，补齐 `litellm_model` / `llm_model_list` 与 legacy key 回退说明。
- improve: 个股报告操作建议结合支撑/压力、量能、筹码与主力资金流校准，减少买入/卖出剧烈切换，并补强 Agent 决策兜底。
- improve: Docker 镜像支持非 root 用户运行，LiteLLM 依赖约束放宽到后续安全 1.x 修复版本。
- fix: 修正 LLM 渠道测试中 `Model disabled`、provider blocked 等错误分类，避免被误报为网络异常。
- fix: 港股日线跳过不支持港股的内置历史数据源；北交所 `BJ` 前缀与 `.BJ` 后缀代码校验保持一致。
- fix: Web 大盘复盘按钮可观测性、Windows fallback 锁进程探测和催化线索展示更稳健。
- docs: 新增文档中心与配置帮助维护说明，清理 README、完整指南与配置指南中的临时 PR/文档同步说明。

### What's Changed

- feat: Add a Web home market-review trigger with task polling and inline report display; setup status now points users to missing configuration.
- feat: Add notification routing by report, alert, and system_error; add one-click notification channel testing in Web settings.
- feat: Add settings field help infrastructure with multilingual help text for the first batch of core configuration fields.
- improve: Share `build_market_review_runtime` across API, CLI, and Bot market review paths; document `litellm_model` / `llm_model_list` and legacy key fallback behavior.
- improve: Calibrate stock advice with support/resistance, volume, chips, and main-force capital flow; strengthen Agent decision fallback behavior.
- improve: Run Docker images as a non-root user and relax LiteLLM constraints to allow safe future 1.x fixes.
- fix: Classify `Model disabled`, provider blocked, and related LLM channel test errors more accurately instead of reporting them as generic network failures.
- fix: Avoid unsupported built-in historical providers for Hong Kong daily data; align Beijing Stock Exchange `BJ` prefix and `.BJ` suffix validation.
- fix: Improve Web market-review observability, Windows fallback lock probing, and market catalyst snippet rendering.
- docs: Add the documentation index and settings-help maintenance guide; remove temporary PR/doc-sync notes from README and user-facing guides.

## [3.15.0] - 2026-05-05

### 发布亮点

- LLM 渠道配置体验继续升级：新增 Anspire OpenAI-compatible 网关接入，并补齐常用服务商预设、官方来源、能力标签、配置注意事项和 GitHub Actions 显式映射。
- Web LLM 配置检测更可诊断：细分错误 reason，并支持用户显式触发 JSON、tools、vision、stream 运行时 smoke。
- LLM 运行时配置清理更稳健：只清理托管 provider 的失效运行时选择，并保留 `cohere/*`、`google/*`、`xai/*` 等直连 provider 兼容语义。
- 通知与 Bot 状态可观测性增强：自定义 Webhook 支持 JSON body 模板，Bot `/status` 展示更完整的 LLM、Agent 与通知渠道状态。
- 大盘复盘、实时告警、Agent weak 兜底和持仓估值继续补强，降低默认值覆盖、缺价污染和配置排障成本。

### 新功能

- 支持 `ANSPIRE_API_KEYS` 默认接入 Anspire OpenAI-compatible 大模型网关，并在 LLM 渠道编辑器补充 Anspire Open 预设。
- 自定义 Webhook 支持 `CUSTOM_WEBHOOK_BODY_TEMPLATE` JSON body 模板，便于适配 AstrBot、NapCat 和自建推送服务。
- 大盘复盘结构化区块新增大盘红绿灯结论，基于盘面温度输出 green/yellow/red、核心原因和操作建议。
- EventMonitor 支持 `price_change_percent` 涨跌幅阈值规则，可按上涨或下跌方向触发实时告警。
- Web LLM 渠道编辑器新增常用服务商配置模板与预设，覆盖 MiniMax、火山方舟、OpenAI、Claude、Gemini、Kimi、Qwen、GLM、豆包等入口。

### 改进

- Web LLM 配置检测补充细分错误分类，并新增显式触发的 JSON/tools/vision/stream 运行时 smoke；默认测试和保存流程不变，检测结果仅作为当前配置的一次 best-effort 诊断。
- Bot `/status` 展示统一 LLM 主模型、Agent 模型、渠道模式、YAML 配置和更多通知渠道状态。
- Web LLM 渠道编辑器展示 provider 能力标签、官方来源链接和配置注意事项提示；这些标签仅用于配置参考，不代表运行时能力已验证通过。
- 抽出 Web LLM provider preset 单一模板数据源，保持现有配置保存语义不变。
- 补齐 LLM provider channel 在 GitHub Actions 中的显式映射，并同步 `.env` 示例与配置文档。

### 修复

- Agent weak 完整性兜底在模型缺少评分、趋势、操作建议或 dashboard 关键块时优先保留本地趋势分析结果，并只补齐真正缺失的仪表盘字段，避免首页评分被默认 50 覆盖。
- 统一持仓快照输出现价、市值、浮盈亏、收益率与价格元信息，避免缺价或 stale 价格污染持仓估值。
- LLM 渠道测试补充结构化诊断与设置页排障提示，便于定位 provider、模型、Base URL 和鉴权配置问题。
- 明确 runtime 清理兼容边界：仅对托管 provider（`gemini`、`vertex_ai`、`anthropic`、`openai`、`deepseek`）触发保存前失效值清理，`cohere/*`、`google/*`、`xai/*` 直连值按 legacy 兼容路径保留，不做无提示迁移或覆写。
- 将 MiniMax 预设调整为官方 OpenAI-compatible Base URL 和当前模型示例，并补充 MiniMax、火山方舟、LiteLLM 兼容来源与回退说明。
- 移除截图识别对 Gemini 3 Vision 模型的过时降级逻辑，默认推断改用当前 Gemini 模型配置。

### 文档

- 完善 LLM provider 配置文档，补充配置方式选择、Actions 变量对照、运行时检测边界、错误 reason 排障和回滚路径（#1180）。
- 补充 LLM 渠道编辑器的官方来源、依赖兼容窗口、保存时的运行时模型清理规则，以及旧配置回退路径说明。
- 为 `cohere/*`、`google/*`、`xai/*` 直连语义补充官方 provider/model 说明、`litellm>=1.80.10,<1.82.7` 兼容依据引用，并明确示例模型名仅为配置保留行为说明而非可用性背书。
- 明确 `price_change_percent` 事件告警仅为配置与运行时规则扩展，未变更模型/provider/base URL/LiteLLM 兼容语义；回退路径为关闭/移除 Event Monitor 配置。
- 同步 README、DEPLOY、full-guide、Anspire、AIHubMix 与 SerpAPI 相关说明，统一外链、配置口径和评审一致性说明。

### 测试

- 补齐 AI 配置页与 `task_queue` 的 LLM 运行时清理/同步回归证据：恢复渠道模型时保留 fallback、编辑模型列表期间不静默清空运行时选择，渠道无可用模型时清理失效 runtime 引用，并覆盖 legacy key 与 `cohere/*`、`google/*`、`xai/*` 直连 provider 保留语义。
- 覆盖 Web LLM 配置检测的细分错误分类，以及 JSON、tools、vision、stream 运行时 smoke 的显式触发路径。

## [3.14.2] - 2026-04-30

### 发布亮点

- 大盘复盘扩展到港股，并让 Bot `/market` 与 CLI/调度入口使用一致的交易日过滤语义。
- 问股与 Agent 链路增强配置缺失、决策 fallback 和多策略选择体验。
- LLM 与分析报告链路提升稳定性：非法 JSON 响应会继续尝试备用模型，LiteLLM DEBUG 日志默认降噪。
- 新增只读首次启动配置状态接口，为后续配置向导和 smoke run 奠定基础。

### 新功能

- 大盘复盘支持港股市场：`MARKET_REVIEW_REGION` 新增 `hk` 选项；`both` 扩展为 A股+港股+美股，并新增港股指数（HSI/HSTECH/HSCEI）复盘链路。
- 新增只读首次启动配置状态接口 `GET /api/v1/system/config/setup/status`，用于识别 LLM、Agent、自选股、通知和本地存储配置缺口；该接口不会重载运行时、写入 `.env` 或创建数据库文件。

### 改进

- 问股页面支持组合选择多个 Agent 策略。

### 修复

- Bot `/market` 命令复用 `get_open_markets_today()` / `compute_effective_region()` 做交易日过滤：结果作为 `override_region` 透传给 `run_market_review`；若结果为空字符串则跳过复盘并推送“今日相关市场休市”，与 CLI/调度入口行为一致。
- 问股 Agent 在未配置可用 LLM 时保留后端真实错误原因并维持 `done.success=false` 失败语义，避免前端把配置缺失误当成成功回答。
- Agent 模式未生成有效决策仪表盘时保留本地趋势分析的评分、趋势和操作建议，并将强买/强卖 fallback 归一到兼容的 `buy`/`sell` 决策类型，避免首页结果被 `50 / 观望 / 未知` 缺省值覆盖。
- 持仓快照现价缺失时不再静默回退为持仓成本；当天快照优先使用历史收盘价，仅在缺失时使用实时价 fallback，缺价持仓不再污染市值与未实现盈亏汇总，并为持仓明细返回价格来源、日期、stale 与缺价状态。
- 分析 Prompt 在注入 `trend_analysis` 前按最终 `trend_status` / `ma_alignment` 清洗互斥理由：空头结构移除看多理由、多头结构移除空头结构风险，并在事件/技术冲突与异常放量（>10 倍）时强制提示“事件先行、技术待确认”与量能降权。
- LLM 返回非 JSON 响应时同样触发备用模型切换：主模型成功返回但无法解析 JSON 时，不再立即降级为纯文本 fallback，而是依次尝试 `LITELLM_FALLBACK_MODELS` 中的备用模型；所有模型均无法返回合法 JSON 时，再降级为文本 fallback。
- LiteLLM 内部 DEBUG 日志默认压低到 WARNING，避免流式生成时 token 级日志污染 `stock_analysis_debug_*.log`；如需排查 LiteLLM 内部细节，可临时设置 `LITELLM_LOG_LEVEL=DEBUG`（Fixes #1156）。

### 文档

- 补充 LLM 配置指南与 FAQ，明确问股 Agent 对 `LITELLM_CONFIG` / `LLM_CHANNELS` / legacy `GEMINI_*` `OPENAI_*` `ANTHROPIC_*` 的兼容优先级、回退路径与“不静默迁移旧配置”的结论。

### 测试

- 新增 `tests/test_bot_market_command.py`，覆盖 `MARKET_REVIEW_REGION=both` + open markets `{"cn","us"}` / `{"cn","hk"}` 的 `override_region` 透传断言，并覆盖全市场休市跳过与关闭交易日检查路径；新增 `tests/test_yfinance_hk_indices.py` 覆盖港股指数符号映射与部分/全部失败降级路径。
- 补齐 `task_queue` 轻量导入 stub 的股票代码规范化函数，恢复 `tests/test_task_queue_config_sync.py` 收集与运行。

## [3.14.1] - 2026-04-26
- [测试] 修正大盘复盘 prompt 测试对“明日交易计划”标题的断言，并同步桌面端版本号，恢复发布 gate。

## [3.14.0] - 2026-04-26

### 发布亮点

- 📊 **大盘复盘升级为盘后工作台式结构** — A 股复盘固定输出盘面温度、指数明细、板块 Top 表、新闻催化、明日交易计划和风险提示，减少纯文字复盘的重复与空泛。
- 🖥️ **桌面端新增 GitHub Release 更新提醒** — Windows/macOS 桌面端启动后自动检测新版本，也可从设置页手动检查并跳转下载页。
- 🤖 **Pipeline Agent 数据加载大幅降噪** — K 线工具改为 DB-first 并预热 240 天历史数据，避免同一只股票重复 HTTP 请求。
- 🐳 **Docker 发布链路整理** — 发布工作流收敛为正式发布与手动补发两条路径，官方 Docker Hub 镜像名统一为 `zhulinsen/daily_stock_analysis`。
- 🔧 **LLM 渠道与 DeepSeek V4 配置补强** — GitHub Actions 定时分析补齐多渠道变量透传，DeepSeek 官方渠道预设与示例同步到 V4。
- 🧩 **桌面端静态资源一致性校验** — 打包链路和运行时都能更早发现静态资源错配，降低 Release 包白屏排查成本。

### 新功能

- 🏠 **Web 首页历史报告区新增重新分析入口** — 支持基于原始 prompt 重做同一只股票同日期的分析。
- 🖥️ **Windows/macOS 桌面端新增 GitHub Release 更新提醒** — 启动后自动检测新版本，并支持从设置页手动检查后跳转下载页。

### 改进

- 📊 **A 股大盘复盘报告改为结构化盘后工作台版式** — 固定输出盘面温度、指数明细、板块 Top 表、新闻催化和明日交易计划。
- 🐳 **Docker 发布工作流收敛** — 更清晰地区分正式发布与手动补发链路，并统一官方 Docker Hub 镜像名为 `zhulinsen/daily_stock_analysis`。
- 🤖 **Agent 日线工具优先复用本地缓存** — 同时持久化新获取的日线与新闻情报，减少重复数据源调用。

### 修复

- 🤖 **Pipeline Agent K 线工具 DB-first 加载** — `get_daily_history` / `analyze_trend` / `calculate_ma` / `get_volume_analysis` / `analyze_pattern` 改为优先读取本地 DB，消除同一只股票 9x5=45 次重复 HTTP 请求（Fixes #1066）。
- 🤖 **Pipeline Agent 执行前按需预热 240 天 K 线历史到 DB** — 正常情况下 K 线工具调用无需重复网络请求。
- 🕒 **冻结 `target_date` 并通过 ContextVar 透传到 Pipeline Agent K 线工具线程** — 消除跨收盘边界时间漂移。
- 🪟 **Windows 桌面端后端日志转抄编码修复** — 转抄 stdout/stderr 时优先使用 UTF-8，并兼容本地代码页回退，避免中文日志乱码。
- ⚙️ **GitHub Actions 每日分析工作流补齐 LLM 渠道变量透传** — 支持 `LLM_CHANNELS`、多 Key 与常用 `LLM_<NAME>_*`，避免本地可用的多模型配置在云端定时任务中失效（Fixes #1063, #872）。
- 📈 **历史报告详情接口修正 `change_pct` 取值** — 使用 `is None` 判断避免把 0.0（平盘）当作缺失值丢弃，移除错误的 `change_60d` 兜底，并在缺失时回退到原始实时行情字段（Fixes #1084）。
- 🔧 **DeepSeek 官方渠道预设与示例配置同步到 V4** — 保留 legacy `deepseek-chat` 默认值并增加废弃提示，同时修正模型发现后旧运行时选择导致保存失败的问题（Fixes #1108, #1109）。
- 🧩 **桌面端打包链路新增静态资源一致性检查** — `scripts/check_static_assets.py` 会在源 `static/` 与 PyInstaller 产物中校验 `index.html` 引用的资源是否真实存在，运行时也会在错配时写入明确日志，避免重现 Release 包打开后白屏（Refs #1064 / #1065 / #1050）。
- 🧩 **后端 `/assets/*` 改为显式路由托管** — 资源缺失时返回与请求扩展名匹配的 `text/javascript` / `text/css` 404，减少默认 JSON 错误响应带来的排查误导（Refs #1064）。
- 🌙 **`kimi-k2.6` 自动使用固定温度** — 主分析、大盘复盘和 Agent 调用该模型时自动使用 `temperature=1.0`，避免模型拒绝默认温度请求（Fixes #1102）。

### 文档

- 🐳 **补充官方 Docker 镜像使用说明** — 增加镜像拉取、`docker run` 用法与 `.env` / 数据目录映射说明，不再只覆盖 Compose 部署路径。
- 📨 **修正飞书自定义机器人 Webhook 示例** — `feishu_sender.py` 中的示例改为 interactive card JSON，并补充飞书自动化 Webhook 触发器配置教程。
- 📚 **优化根 README 结构** — 保留首页级功能特性、技术栈、快速开始、推送效果、Web、Agent、赞助商和新闻源入口，将细配置、交易纪律和基本面语义收口到完整指南，并将 Docker 徽章指向官方镜像页。
- 🌐 **同步英文与繁中 README 的精简入口结构** — 同时补齐完整指南中的 LLM 用量 API 与持仓管理说明。
- 🤝 **调整 AI 协作与 PR 模板中的 README 维护规则** — 明确 README 非必要不更新，细节优先进入专题文档。

### 测试

- 🧪 **稳定市场复盘相关测试的 LiteLLM stub 行为** — 避免本机安装的 LiteLLM 在测试收集顺序变化时影响市场复盘单元测试。
- 🧪 **pytest 默认跳过前端依赖目录** — 本地存在 `apps/dsa-web/node_modules` 时不再被后端测试递归扫描，避免发布前 gate 被无关目录拖慢。

## [3.13.0] - 2026-04-21

### 发布亮点

- 🌉 **长桥 OpenAPI 数据源接入** — 美股/港股行情优先使用 Longbridge，YFinance / AkShare 自动兜底；未配置时行为不变。
- 📈 **Tushare 港股全链路扩展** — 港股日线通过 `hk_daily` 获取；筹码分布对港股返回 `None`；换算单位跟随港股口径，不再套用 A 股手/千元规则。
- 🔍 **Anspire Search 语义搜索接入** — 配置 `ANSPIRE_*` 后即可使用 Anspire Search 获取实时行情及资讯，未配置时完全透明。
- 🚀 **普通分析链路支持 LLM 流式生成** — 首页任务 SSE 新增 `task_progress` 事件，进度更细化；不支持流式的 provider 自动回退到非流式调用。
- 🤖 **Web 渠道编辑器支持按需拉取可用模型列表** — `/v1/models` 统一模型发现入口，多选写回 `LLM_{CHANNEL}_MODELS`，拉取失败时保留手动输入降级。
- 🛡️ **Agent 稳定性与预算护栏全面补强** — `AGENT_MAX_STEPS` 语义统一、技能降级不中断管线、SSE 异常透传、技能加载 warning 日志补齐。
- 🛠️ **SQLite 写入链路原子化** — 批量原子 upsert + WAL + `busy_timeout` + 有限写入重试，显著降低批量分析并发锁竞争。

### 新功能

- 🌉 **集成 Longbridge OpenAPI 作为美股/港股可选数据源**（fixes #981）— 配置 `LONGBRIDGE_*` 后优先使用长桥获取日线与实时行情，YFinance / AkShare 兜底；未配置时行为与此前一致。联调使用 `tests/longbridge_live_smoke.py`（手动脚本，不参与 pytest 收集）。
- 📈 **Tushare 支持港股日线查询** — 配置 Tushare 凭证后调用 `hk_daily` 接口获取港股数据；权限不足时抛出异常，与原流程一致。
- 🔍 **集成 Anspire Search 可选语义搜索后端** — 配置 `ANSPIRE_*` 可使用 Anspire Search 获取实时行情及新闻资讯；未配置时行为与此前一致。联调使用 `tests/test_anspire_search.py`（手动脚本）。
- 🚀 **普通分析链路支持 LiteLLM 流式生成与更细任务进度** — 股票分析在 LLM 阶段优先尝试 `stream=True` 并在服务端累积 chunk，首页任务 SSE 新增 `task_progress` 事件与更细的 `message/progress` 更新；仅在最终 JSON 解析成功后持久化历史报告；不支持流式的 provider 自动回退到非流式调用。
- 🤖 **Web AI 模型配置支持按渠道获取可用模型列表** — 渠道编辑器支持调用 `/v1/models` 拉取可用模型，并以多选方式写回 `LLM_{CHANNEL}_MODELS`；拉取失败时保留手动输入作为降级路径。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；仅对极少数高位且摘要不足的结果做延迟补抓，优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案统一为"主模型 / Agent 主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键保持兼容。
- 🧠 **IntelAgent 新增公司公告搜索与主力资金流工具** — 增加上交所/深交所/cninfo 公告搜索维度与 `get_capital_flow` 工具，修复 Agent 模式下公告和资金流数据经常缺失的问题。
- 📦 **后端股票名称解析优先复用 `stocks.index.json`** — 懒加载缓存前端静态索引，纯后端/缺失静态资源场景静默降级回 `STOCK_NAME_MAP` 与原有数据源回退链路。
- 📊 **TushareFetcher 港股单位适配** — `get_chip_distribution` 对港股直接返回 `None`（港股暂不支持筹码分布）；`_normalize_data` 对港股（`hk_daily`）不再做 A 股手→股、千元→元的缩放，与 Tushare 港股字段语义一致。
- ⏱️ **Agent 超步数错误增加 `AGENT_MAX_STEPS` 调整提示** — 帮助用户自助排查步数限制问题。
- ⚙️ **GitHub Actions 分析任务超时支持 `vars` 配置** — `daily_analysis.yml` 任务超时从 repository variables 读取，无需修改代码即可调整运行超时上限（fixes #1014）。

### 修复

- 📣 **大盘复盘链路接入 `REPORT_LANGUAGE`** — `REPORT_LANGUAGE=en` 时，A 股/合并复盘的 Prompt、章节标题、模板兜底文案与通知包装标题统一输出英文，避免英文正文搭配中文标题的混排问题。
- 📈 **EfinanceFetcher 指数开盘价映射兼容**（fixes #1043）— `get_main_indices()` 的开盘价映射改为兼容 `今开 → 开盘 → open`，修复部分 efinance 版本下指数开盘价被读成缺失值的问题。
- 🤖 **AGENT_MAX_STEPS 语义统一**（fixes #1026）— 在 orchestrator 多 Agent 模式下明确为"各子 Agent 步数上限而非硬覆盖"；TechnicalAgent 等高默认值 Agent 会被封顶，低默认值 Agent 保持原值；用户主动调高（>10）时统一覆盖所有子 Agent。修复了用户设置 12 但 TechnicalAgent 仍以默认 6 步运行并报 "Agent exceeded max steps" 的问题。
- 🛡️ **Specialist（Skill）Agent 失败改为优雅降级** — 技能 Agent 失败不再中断整个分析管线，与 intel/risk 保持相同的降级策略。
- 🔧 **MiniMax-M2.7 连接测试修复** — 修复 LLM 通道连接测试在 MiniMax-M2.7 下返回 "Empty response" 的问题；将 `max_tokens` 上限从 8 提升至 256 以容纳思考过程，并添加 `content_blocks` 格式解析逻辑。
- 📊 **移除 `sentiment_score` 范围约束**（fixes #942）— 移除 `HistoryItem` 与 `ReportSummary` 响应 Schema 中 `sentiment_score` 的 `ge=0/le=100` 约束，历史库中存储的超范围值不再触发 Pydantic ValidationError。
- 🖥️ **WebUI 前端资源缺失时发出明确警告** — `webui_frontend.py` 在 `static/index.html` 存在但 `static/assets/` 缺失时发出 warning，避免 CSS/JS 资源缺失导致页面异常变大却无从排查（fixes #944）。
- 🔗 **分析管线可选服务降级初始化** — `StockAnalysisPipeline` 搜索服务与社交舆情服务任一初始化异常时，记录 warning 并以禁用状态继续运行，避免外部依赖抖动阻塞主分析链路。
- 🖥️ **桌面端版本展示统一读取 `package.json`** — 统一读取 `apps/dsa-desktop/package.json`，移除 preload 中硬编码的 `0.1.0`，设置页展示真实桌面端版本；修复版本号显示错误（fixes #1048）。
- 🐋 **港股名称获取失败修复**（fixes #940）— 修复主数据源字段缺失时无法正确回退到备用字段获取港股名称的问题。
- 🔄 **SSE 任务流断开时 `CancelledError` 正确 re-raise**（fixes #967）— 修复 SSE 流中断时异常被静默吞掉导致故障无日志可查的问题。
- 🔄 **Agent SSE 清理阶段后台任务异常正确上报**（fixes #969）— 流结束时后台执行器异常现在正确记录并上报，避免错误无法感知。
- 🔇 **技能加载异常补充 `logger.warning` 日志**（fixes #970）— 在 `ask.py`、`skills/aggregator.py`、`skills/router.py` 的静默 except 块补充日志，确保技能列表为空时有日志可查。
- 🛠️ **SQLite 写入链路原子化**（fixes #878）— `stock_daily(code,date)` 使用批量原子 upsert；文件型 SQLite 连接默认启用 WAL + `busy_timeout` + 有限写入重试；"新增数"改按本次真正插入窗口计算。
- 💰 **多 Agent / 单 Agent 预算护栏语义统一** — 剩余预算低于最小阈值时主动跳过并降级；已完成阶段可构建降级报告时返回 `success=True` 并携带非空内容，否则返回 `success=False`。
- ⚙️ **GitHub Actions `daily_analysis.yml` 补齐 `REPORT_LANGUAGE` 注入**（fixes #1013）— 修复用户在 Secrets/Variables 中配置 `REPORT_LANGUAGE` 后不生效的问题。
- 📊 **任务状态 API 补齐实时价格字段**（fixes #983）— `GET /api/v1/analysis/status/{task_id}` 从数据库回填已完成任务时补齐 `current_price` / `change_pct`，修复首页报告股票名旁不显示实时价格的问题。
- 📅 **非交易日数据返回最近交易日**（fixes #1009）— 修复非交易日（周末/节假日）筹码分布与板块排行返回倒数第二个交易日数据的问题，现在正常返回最近交易日数据。
- 🔍 **A 股资讯搜索恢复中文优先** — `search_stock_news()` 在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面；非美股查询不再默认沿用 Brave 的 `en/US` 区域语言偏好。
- 📨 **飞书群机器人通知支持签名校验** — 飞书通知现在支持 `FEISHU_WEBHOOK_SECRET` / `FEISHU_WEBHOOK_KEYWORD`；Web 设置与文档明确区分 Webhook 推送模式和 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 应用模式，降低误配风险。
- ⚡ **LLM 适配层新增 `RateLimitError` 和 `ContextWindowExceeded` 检测** — 识别并处理速率限制与上下文窗口超出错误，提升分析链路在高负载或长文本场景下的健壮性（fixes #1002）。

### 测试

- 🧪 **TushareFetcher 港股相关单元测试** — 新增 `get_chip_distribution` 筹码分布获取与 `_normalize_data` 港股/A 股/ETF 单位处理的单元测试，覆盖港股特殊路径。

### 文档

- 📘 **DEPLOY.md 补充 UI 元素异常变大排查步骤** — 新增重建 Docker 镜像或手动执行 `npm run build` 的排查指南；`deploy-webui-cloud.md` 同步更新。
- 📨 **飞书 Webhook 配置说明补全** — 强调 `FEISHU_WEBHOOK_URL` 是群通知必填项、签名校验须两端同时启用或关闭、`FEISHU_APP_SECRET` 仅用于应用/Stream Bot 模式；`.env.example` 补充内联注释；同步英文指南。
- 🤝 **FAQ 补充 Ollama 连接失败排障条目（Q12c）** — 覆盖服务未启动、URL 配置错误、模型前缀缺失、模型未下载、远程防火墙等 5 个检查点（fixes #854）。
- 🌉 **README 补充长桥数据源使用说明** — 中/英/繁 README 明确长桥"首选 / 兜底 / 未配置不调用"边界；`docs/` 内相对路径链接修复；`LONGBRIDGE_PRINT_QUOTE_PACKAGES` 配置与代码及 `.env.example` 对齐。
- 🐋 **Docker 安装场景版本说明** — 补充最小化文档，明确 Docker 安装场景下应以 Git tag / 镜像 tag 判断版本（fixes #1091）。

## [3.12.0] - 2026-04-01

### 发布亮点

- 📊 **回测页新增"次日验证"视图** — 可按股票与日期范围查看 AI 预测 vs 次日实际涨跌，复用历史分析与 1 日回测结果，快速验证分析准确率。
- 🔧 **LLM 接入体验简化** — 用户侧文案统一收口为"主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有配置键保持兼容。
- 🐳 **Docker / WebUI 运行时稳态补强** — 修复系统设置保存后配置不生效、启动早期日志缺失、预构建静态资源复用等问题，降低容器化部署的运维摩擦。
- 🔒 **安全与并发稳定性同步增强** — Discord 入站 Webhook 补齐 Ed25519 验签，修复并发执行时共享状态未加锁、单股推送模式通知并发复用等问题。
- 🖥️ **桌面端与定时任务细节打磨** — Windows 安装器支持自选安装目录，内置定时调度器感知运行中 SCHEDULE_TIME 变更，断点续传改按市场时区判断。

### 新功能

- 📊 **回测页新增"次日验证 / 1 日窗口"视图** — 可按股票代码与分析日期范围查看 AI 预测、次日实际涨跌及筛选区间准确率，复用历史分析与 1 日回测结果实现。
- 🏷️ **Web 设置页新增版本信息卡片** — `apps/dsa-web` 现在会在构建时注入前端包版本与构建时间，系统设置页新增只读"版本信息"区块，展示 `WebUI 版本 / 构建标识 / 构建时间`；当 `package.json` 仍为占位版本 `0.0.0` 时，会自动回退为构建标识，方便 Docker 重建后快速确认当前静态资源是否已经生效。
- 🪟 **Windows 桌面安装器支持自选安装目录** — 安装器改为支持在安装向导中自定义安装目录，安装到非默认盘符后仍沿用现有打包态目录逻辑在安装目录旁读写 `.env`、`data/stock_analysis.db` 和 `logs/desktop.log`，同时保留 `win-unpacked` 免安装分发方式。安装器仅支持当前用户安装、已禁用管理员提权（`allowElevation: false`），并通过 NSIS `.onVerifyInstDir` 阻止选择系统保护目录。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；现在仅对极少数高位且摘要明显不足的结果，在更短超时预算内做延迟补抓，并优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案已统一收口为"主模型 / Agent 主模型 / 备选模型 / 模型渠道 / 高级模型路由配置"；Web 设置页、配置元数据、校验提示与中英文文档不再把 LiteLLM 当作普通用户默认必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键仍保持兼容。

### 修复

- 🚀 **启动早期失败时暴露真实根因** — `python main.py` 现在通过 stderr 暴露真实根因，bootstrap 阶段不再向硬编码 `logs/` 目录写入文件日志，文件日志推迟到 `config.log_dir` 可用后创建，避免健康启动在非预期路径残留日志文件。
- 🐳 **Docker WebUI 运行时优先复用预构建静态资源** — `prepare_webui_frontend_assets()` 现在会先检查镜像内已有的 `static/index.html` 是否可直接复用；当容器运行时不包含 `apps/dsa-web` 源码目录且未安装 `npm` 时，也不会误报"未找到前端项目，无法自动构建"，从而恢复 Docker 部署后的 WebUI 打开能力。
- 🐳 **Docker WebUI 系统设置保存后配置生效** — Docker 场景下 WebUI 保存 `STOCK_LIST`、`SCHEDULE_ENABLED`、`SCHEDULE_TIME`、`SCHEDULE_RUN_IMMEDIATELY`、`RUN_IMMEDIATELY` 后，`Config` 会优先读取持久化 `.env` 中的新值，避免被容器创建时注入的旧环境变量覆盖。
- 📈 **市场复盘 LLM max_tokens 提升** — 市场复盘生成链路将 LLM `max_tokens` 从 `2048` 提升到 `8192`，降低长复盘输出因 `MAX_TOKENS` 提前截断导致内容未完成的概率。
- ⏰ **内置定时调度器感知 SCHEDULE_TIME 运行时变更** — 调度器现在会在运行中感知 WebUI 保存后的 `SCHEDULE_TIME` 变化，并在下一轮检查时重绑 daily job。
- 🪟 **Windows Release 渠道编辑器保留 MiniMax 模型前缀** — 渠道模式下填写 `minimax/<模型名>` 时，后端归一化与 Web 设置页运行时模型列表都会保留该值原样，不再误改写成 `openai/minimax/<模型名>`。
- 🤖 **Discord 入站 Webhook 补齐 Ed25519 验签** — `DiscordPlatform` 现在会基于 `X-Signature-Ed25519`、`X-Signature-Timestamp` 和原始请求体校验 Discord Interaction 签名；缺失签名头、公钥格式非法或签名不匹配时直接拒绝请求，同时对 timestamp 做 ±5 分钟时效窗口校验以防御重放攻击。
- ⚙️ **STOCK_GROUP_N / EMAIL_GROUP_N 配置关系明确化** — 明确与 `STOCK_LIST` 的关系，并在配置校验中对超出 `STOCK_LIST` 的邮件分组给出 warning。
- 🗓️ **断点续传改按市场时区和交易日历判断**（fixes #880）— 股票数据存在性检查不再直接使用服务器自然日，而是按 A 股 / 港股 / 美股各自市场时区解析"最新可复用交易日"。
- 📨 **单股推送模式不再并发复用共享通知实例** — `StockAnalysisPipeline.run()` 现在会保留个股分析并发，但把 `SINGLE_STOCK_NOTIFY=true` 下的即时通知挪到结果收集侧串行发送。
- 🔇 **实时行情降级提示收口为单次告警** — 分析主流程获取股票名称时不再提前触发一次实时行情查询，只有在全部数据源都不可用时才提示已降级为历史收盘价继续分析。
- 🔍 **A 股中文资讯搜索恢复中文优先** — `search_stock_news()` 现在会在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面。
- 🔒 **并发执行时共享状态补齐统一加锁** — 修复并发执行时共享状态缺少统一加锁的问题，避免多线程场景下的数据竞争。

### 测试

- 🧪 **补充设置页版本信息回归测试** — 新增 Web 设置页版本信息渲染断言，并覆盖占位版本 `0.0.0` 自动回退为构建标识的逻辑。
- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

## [3.11.0] - 2026-03-27

### 发布亮点

- 🎨 **Web 工作台完成一轮 UI 统一与双主题升级** — 首页、问股、回测、持仓和设置页进一步收口到统一设计 token、输入表面和状态表达；新增完整浅色主题，并支持浅色 / 深色一键切换与持久化保存。
- 🤖 **Bot / Agent 能力重新补回主分支** — 恢复 `/history`、`/strategies`、`/research` 等命令，`/ask` 继续支持多股对比与组合视角；Deep Research、事件监控与 schedule 轮询链路重新接回主线能力。
- 🔒 **安全性与运行稳态同步补强** — 修复 `X-Forwarded-For` 限流绕过风险，恢复 LiteLLM 官方 PyPI 安装路径，Tushare 初始化不再依赖本地 SDK，降低 Docker、桌面打包和环境重建时的脆弱点。
- 🖥️ **日常使用细节继续打磨** — 修复首页港股自动补全提交、登录页首屏主题闪烁、历史长股票名重叠，以及 Telegram Markdown 解析失败时整条通知发送中断等问题。

### 新功能

- 🎨 **全新浅色主题与双主题切换上线** — Web 工作台新增完整浅色主题，并支持在侧边栏中一键切换浅色 / 深色模式；主题选择会持久化保存，刷新页面后仍保持当前偏好。此次升级不是局部配色微调，而是对卡片层级、边界对比、输入表面、状态提示和页面背景做了一整套 light theme 重绘。
- 🤖 **补回主分支缺失的 Agent / Bot 能力** — `#648` / `#649` 已重新补回 `main`：Bot 恢复 `/history`、`/strategies`、`/research`，`/ask` 保留多股对比与组合视角；Deep Research 与 Event Monitor 的配置重新在 Web 设置页可见并可编辑，schedule 模式也重新接入事件告警轮询。

### 改进

- 🖥️ **核心页面统一到同一套工作台视觉语言** — `Home / Chat / Backtest / Portfolio / Settings` 进一步收口到共享设计 token、`input-surface` 输入体系、空态/错误态表达和抽屉遮罩语义，减少页面之间的视觉割裂与局部私有样式漂移。
- 💬 **问股交互可达性与反馈增强** — 问股页补强了会话导出、通知发送、消息复制、历史删除与追问上下文提示；AI 回复操作不再过度依赖 hover，触屏设备和小屏场景下也能直接触达关键按钮。
- 📊 **回测与持仓页表面和状态表达继续标准化** — 回测页筛选控件、布尔状态、结果表格与汇总卡片统一到共享输入/状态原语；持仓页的导入反馈、汇率刷新提示、空态与警示信息进一步归口到共享组件，减少页面级重复实现。
- 🧭 **导航与页面壳层协同优化** — 侧边栏主题切换、问股完成角标、移动端抽屉遮罩和主内容滚动契约进一步统一，首页、问股和回测在桌面端与移动端的切页体验更稳定。

### 测试

- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

### 修复

- 🌗 **Web 首屏默认主题预设为深色** — `apps/dsa-web/index.html` 现在会在 React 挂载前读取本地保存的主题偏好；若没有已保存值，则立即给 `<html>` 预设 `dark` 并同步 `color-scheme`，避免首页和登录页首屏先闪出浅色主题。
- 🔐 **登录页独立主题层收口** — 登录页输入框、标签、切换按钮和按钮文案现在使用独立的 `--login-*` 视觉 token，不再继承全局浅/深主题文字色；即使浏览器缓存了浅色主题，登录页仍保持稳定的深色视觉与青色密码输入表现，避免密码圆点和文案落成黑色。
- 🖥️ **首页港股代码输入修复** — Web 首页分析输入框现在可正确接受港股代码与自动完成选中的港股项，补齐 `00700.HK` / `HK00700` 等格式识别，避免提交时误报“请输入有效的股票代码或股票名称”。

- 🔒 **认证限流 X-Forwarded-For 取值修复（CWE-345）**（#841 / #842）— `get_client_ip()` 从取 `X-Forwarded-For` 最左值改为最右值，防止攻击者通过伪造首部旋转限流桶绕过暴力破解保护；仅影响 `TRUST_X_FORWARDED_FOR=true` 且单层可信反向代理的部署场景，多级代理环境需按部署文档评估配置。
- 📦 **恢复 LiteLLM 官方 PyPI 安装并锁定安全上限** — `requirements.txt` 重新使用 `pip install litellm` 的官方 PyPI 安装路径，并在保留历史最低要求 `>=1.80.10` 的同时增加 `<1.82.7` 的安全上限，避免误装已被移除的 `1.82.7` / `1.82.8` 风险版本；Windows 桌面打包脚本也同步回退到标准 `pip install -r requirements.txt` 链路，减少特殊下载分支带来的维护成本。
- 📨 **Telegram Markdown 解析失败回退纯文本**（fixes #850）— `src/notification_sender/telegram_sender.py` 现在会在 Telegram 返回 `HTTP 400` 且包含 `can't parse entities` / Markdown 解析错误时，自动去掉 `parse_mode` 后重试纯文本发送，避免 `*ST` 等正文内容直接导致整条通知失败。
- 🔢 **A 股同码实时行情保留交易所提示**（fixes #852）— `DataFetcherManager` 与 `TushareFetcher` 现在会保留 `SZ000001` / `000001.SZ` 这类显式沪深提示，旧版 Tushare 实时行情降级分支不再把深市 `000001` 误判成 `sh000001` 上证指数。
- 🎯 **多 Agent 次优买点不再盲目复制理想买点**（fixes #851）— 当多智能体结果缺少独立 `secondary_buy` 时，仪表盘现在优先展示 `N/A` 而不是把 fallback 值硬拷贝成与 `ideal_buy` 完全相同，减少误导性的双买点展示。
- 🧩 **Tushare 初始化不再强依赖本地 SDK 包** — `TushareFetcher` 现在直接使用内置 HTTP client 访问 Tushare Pro，不再在启动阶段先 `import tushare` 才能初始化；修复了 Docker、桌面打包或环境重建后因缺少 `tushare` 包而提前报 `No module named 'tushare'` 的问题，并补充对应回归测试。
- ⚙️ **`daily_analysis` 工作流补齐 `DEEPSEEK_API_KEY` 映射** — GitHub Actions 每日分析工作流现在会正确透传 `DEEPSEEK_API_KEY`，避免云端任务配置了密钥却在运行时拿不到对应环境变量。
- 🖥️ **历史列表过长股票名称截断与悬停展示**（fixes #815）— 历史列表中过长的股票名称, 现在会按字符类型自动截断（英文15/中文8/混合10字符），默认显示截断结果，悬停时展示完整名称；解决 1920x1080 分辨率下股票名称与右侧状态标签文字重叠的问题。新增 `stockName.ts` 工具函数并补充对应测试。

### 文档

- 🧾 **README 捐赠入口更新为小红书二维码** — README 及中英文说明中的赞助入口更新为小红书二维码素材，保持展示口径一致。

## [3.10.1] - 2026-03-24

### 新功能

- 🔔 **Web 端分析推送通知开关**（#808）— 首页分析按钮旁新增「推送通知」复选框，默认勾选；取消勾选时本次分析不发送 Telegram/企业微信等推送。API `POST /api/v1/analysis/analyze` 新增 `notify` 字段（`bool`，默认 `true`），不传时行为与修改前一致，Bot 和定时任务不受影响。

### 改进

- 🖥️ **问股 / 回测页面布局与壳层协同优化** — 统一 Chat / Backtest 页面容器、共享 UI 状态和跟随问答交互路径，移除部分硬编码高度限制，让导航框架内的填充与滚动行为更连贯。
- 🎨 **全局视觉与共享组件继续收敛** — Light theme 引入动态 HSL 阴影体系，统一侧边栏激活态、告警组件对比度和聊天气泡样式，并把部分零散内联样式收口为语义化 CSS 变量，提升一致性与可维护性。

### 修复

- 🖼️ **系统设置智能导入文件选择恢复** — 修复了“系统设置 > 基础设置 > 智能导入”模块中 “选择图片 / 选择文件” 两个按钮点击无响应的问题。
- 🖥️ **移动端滚动与交互层级修复** — 解决主题切换菜单在移动端被主内容遮挡的 z-index 冲突，并恢复首页长报告场景下的正常纵向滚动，不影响其他页面现有滚动行为。
- 🧾 **Markdown 纯文本复制清洗增强** — 改进纯文本导出算法，复制分析报告时会更稳定地清除表格分隔符等 Markdown 痕迹，提升分享和归档内容的纯净度。
- 🧠 **Trading philosophy injection 覆盖 legacy + Agent 全链路**（#810）— `GeminiAnalyzer`、单 Agent 模式和 skill-aware Prompt 现在共享同一套策略注入状态；只有隐式回落到内置默认 `bull_trend` 时才保留旧的趋势型提示，显式策略选择或自定义默认 skill 不再被偷偷叠加 `MA5>MA10>MA20` 多头基线。
- 🛠️ **后端 CI 依赖安装链路稳态化**（#835）— 拆分 backend gate 阶段、为依赖安装增加重试，并把 CI 用的 `litellm` 安装来源调整为更稳定的 GitHub 源，降低依赖解析抖动导致的 backend gate 偶发失败。
- 🪟 **Windows 桌面发版构建恢复 LiteLLM 安装兼容性** — `scripts/build-backend.ps1` 现在会先过滤 `requirements.txt` 中的 LiteLLM GitHub 源包，再下载对应 tag 的 zipball 到本地移除上游可选 `enterprise/` 目录后安装，绕过 Windows runner 上 Poetry 构建 wheel 时把目录误当文件打包导致的失败；同时补上 `pip install` 退出码检查，避免依赖安装失败后只在后续 `python-multipart` 校验阶段才暴露成次生报错。

### 测试

- 🧪 **问股 / 回测 / 智能导入回归覆盖补齐** — 同步更新 E2E 冒烟期望，补充 `DashboardStateBlock`、Chat 页、智能导入文件选择与相关交互回归断言，确保近期 UI 调整后的关键路径仍可稳定通过。

## [3.10.0] - 2026-03-24

### 发布亮点

- 🔎 **自动补全与索引工具扩展到三市场** — 补全索引生成链路现在同时覆盖 A 股、港股、美股，配套新增 Tushare 股票列表抓取工具与更完整的静态索引数据，让首页搜索入口从“能用”走向“更全、更稳”。
- 🖥️ **Dashboard 与报告查看体验继续收口** — 首页 Dashboard 面板、状态边界、字体层级和完整报告表格密度完成一轮统一；报告详情也补齐了 Markdown/纯文本复制与更可靠的按钮交互，减少历史报告查看与分享时的摩擦。
- 🤖 **Agent skill 与市场语义边界更清晰** — skill bundle、默认策略、回测汇总语义和兼容接口进一步收敛；同时分析 Prompt 不再默认写死 A 股上下文，美股和港股分析也能按各自市场规则生成更贴切的内容。
- ⏰ **定时与桌面配置能力更贴近真实使用场景** — 桌面端支持 `.env` 导入导出；`python main.py --schedule --stocks ...` 也不再把启动时股票快照错误带入后续计划执行，定时任务会跟随最新保存的 `STOCK_LIST`。
### 新功能

- 💾 **桌面端 `.env` 备份/恢复入口**（#754）— 桌面模式下的系统设置页新增 `导出 .env` / `导入 .env` 按钮，可直接备份当前已保存配置，或把备份文件中的键值合并恢复到当前桌面端 `.env`；导入沿用现有 `config_version` 冲突保护与运行时重载链路，不改变现有桌面端便携模式路径。
- 📊 **Tushare 股票列表获取工具** — 新增 `scripts/fetch_tushare_stock_list.py`，支持从 Tushare Pro 获取 A股、港股、美股列表信息并保存为 CSV，配有分页读取、智能限流、错误处理和进度提示；新增对应使用文档 `docs/TUSHARE_STOCK_LIST_GUIDE.md`。
- 🔎 **索引生成脚本多市场支持** — `generate_index_from_csv.py` 重构为支持 Tushare 和 AkShare 双数据源，同时覆盖 A股、港股、美股三个市场；新增按市场分类的别名映射（A股、港股常见别名，美股常用股票英文缩写）；添加 `--source` 参数切换数据源、`--test` 参数验证模式；严格过滤美股 DUMMY 记录。
- 🔎 **索引生成脚本增强** — `generate_stock_index.py` 新增 `--test`/`-t` 测试模式和 `--verbose`/`-v` 详细输出模式，添加市场分布统计，优化 JSON 输出格式。
- 📋 **首页完整报告支持双模式复制** — 历史报告详情头部新增“复制 Markdown 源码”和“复制纯文本”工具按钮；前者保留原始 Markdown 结构，后者去除常见 Markdown 格式符号，方便分享、归档和跨报告比对。复制按钮文案会跟随 `REPORT_LANGUAGE` 保持中英文一致，避免英文报告页出现中文固定文案。
- 🧩 **个股分析页补齐关联板块展示**（#669）— A 股分析写路径现在会把 `belong_boards` 一次性写入 `fundamental_context` / `fundamental_snapshot`，结构化报告详情同步新增 `belong_boards` 与 `sector_rankings` 字段，Web 个股分析页首屏可直接展示所属板块及其是否命中当日板块涨跌榜；无数据时保持 fail-open 隐藏，不影响现有分析主流程。

### 改进

- 🖥️ **Dashboard 面板统一化（PR7-2）** — 新增 `DashboardPanelHeader` 和 `DashboardStateBlock` 作为历史、报告、资讯、任务和透明度等面板的通用组件；统一了各面板标题层级、加载/空态/错误态和 CSS 变量 token。
- 🖥️ **HomePage 状态边界收口（PR7-2）** — 引入 `useHomeDashboardState` hook，集中 `stockPoolStore` 状态选取逻辑，移除 `HomePage` 中重复的本地状态派生和回调定义。
- 🧭 **Agent skill 统一到单一配置语义** — Multi-Agent runtime、API、Web chat 和配置元数据统一围绕 `skill` 概念收敛；`/api/v1/agent/skills` 成为主发现入口，`AGENT_SKILL_*` 成为主配置面，内置 skill 元数据也开始声明默认启用、排序优先级、market regime tag 等信息，减少默认策略散落在代码里的隐式耦合。
- 🔎 **自动补全索引数据更新** — 重新生成 `stocks.index.json`，涵盖 A股、港股、美股三个市场，提升自动补全覆盖率。
- 🧾 **Dashboard 字体与完整报告表格密度微调** — 收敛首页侧栏、空状态、历史操作区的字体层级，并将完整 Markdown 报告表格 `th/td` 的内边距调整到更紧凑的 4-6px 区间，让信息密度与现有 Dashboard 视觉节奏更一致。

### 修复

- ⏰ **定时模式不再锁定启动时 CLI 股票快照** — `python main.py --schedule --stocks ...` 现在不会让后续计划执行沿用启动时的旧股票列表；定时任务每次触发前都会重新读取最新保存的 `STOCK_LIST`，确保 WebUI 或 `.env` 更新后的自选股配置能参与后续推送。
- 🌍 **LLM Prompt 按股票市场动态注入上下文** — 分析链路不再把市场规则写死成 A 股；系统 Prompt 会根据股票代码识别 A 股、港股或美股，并注入对应的角色描述与交易规则提示，减少跨市场分析出现口径错位或结论失真的问题。
- 🔎 **美股自动补全复用 ticker 去重** — `generate_index_from_csv.py` 在导入 Tushare `us_basic` CSV 时会先按 `ts_code` 折叠复用的美股 ticker，优先保留更可能仍在使用的记录，避免 `stocks.index.json` 出现重复 `canonicalCode` 后让 Web 自动补全展示历史名称或提交歧义代码。
- 🧾 **Web 报告详情复制交互稳定性修复**（#749）— `ReportDetails` 中“原始分析结果 / 分析快照”的复制按钮补齐可点击层级，避免被下方 JSON 内容覆盖；两个面板的复制提示也改为各自独立，不再出现复制一个后两个按钮同时显示“已复制”的误导反馈。
- 📊 **Agent skill 回测与兼容接口语义收敛** — `get_skill_backtest_summary` 现在要求显式传入 `skill_id`，缺失时返回明确校验提示；仓库尚未持久化真实 skill 级汇总时会返回明确的 unsupported/info 响应，并保留 `normalized` 与 `*_pct` 兼容字段，避免沿用 overall 指标误导 Agent 或用户。
- 🔧 **Skill 默认选择与兼容层行为加固** — `allowed-tools` 会继续仅作为 `SKILL.md` bundle 元数据保留，不再泄露到运行时工具选择；`/api/v1/agent/strategies` 恢复旧 payload 形状；显式传入 `skills: []` 时会清空陈旧上下文；当用户明确选择策略 skill 时不再偷偷叠加默认 bull-trend，而在 `AGENT_SKILLS` 为空时则统一只回落到单一主默认 skill。

### 测试

- 🧪 **Dashboard 组件测试覆盖率扩展（PR7-2）** — 新增 `ReportNews` 和 `TaskPanel` 测试；对 `HistoryList`、`ReportDetails`、`HomePage`、`useDashboardLifecycle` 和 `stockPoolStore` 增强了断言覆盖，包括删除回退、移动端抽屉和任务生命周期等场景。
- 🧪 **多市场索引生成测试补齐** — 新增 `tests/test_generate_index_from_csv.py`，覆盖 Tushare/AkShare 双数据源解析、多市场判断、美股 DUMMY 过滤与重复 ticker 去重等核心路径。
- 🧪 **关联板块写入与 API 契约回归** — 新增 `tests/test_pipeline_related_boards.py`，并补充分析历史与分析接口契约测试，确保 `belong_boards` / `sector_rankings` 只做增量扩展且保持 fail-open。
- 🧪 **定时模式股票列表语义回归测试** — 新增 `tests/test_main_schedule_mode.py`，覆盖定时模式忽略启动时 `--stocks` 快照、单次运行仍保留 CLI 股票覆盖的边界场景。

### 文档

- 📘 **新增 Tushare 股票列表工具文档** — 新增 `docs/TUSHARE_STOCK_LIST_GUIDE.md`，说明股票列表抓取工具的使用方法、数据格式和常见问题。
- 🌍 **补齐定时模式与关联板块的双语说明** — `docs/full-guide.md` / `docs/full-guide_EN.md` 现在明确说明 scheduled mode 会在每次执行前重新读取 `STOCK_LIST`，并同步补充个股关联板块展示能力说明，减少配置预期偏差。
- 🧭 **调整 Agent 术语兼容文案** — README、双语文档、设置页与问股界面继续以“策略”作为用户入口主称呼，同时补充 `skill` 作为内部统一命名，降低迁移期理解成本。

## [3.9.0] - 2026-03-20

### 发布亮点

- 🤖 **模型链路与报告语言更灵活** — Agent 现在可以通过 `AGENT_LITELLM_MODEL` 独立选择模型链路，普通分析与 Agent 报告也可通过 `REPORT_LANGUAGE=zh|en` 输出统一语言，减少“英文内容 + 中文壳子”这类混排问题，并允许团队分别权衡主分析与 Agent 的成本、速度和能力。
- 🔎 **首页分析体验完成一轮闭环优化** — 首页新增 A 股自动补全，支持代码、中文名、拼音和别名检索；同时 Dashboard 状态收口到统一 store，历史、报告、新闻与 Markdown 抽屉的交互更稳定，“Ask AI” 追问也会优先携带当前报告上下文。
- 💬 **通知与检索能力继续外扩** — 新增 Slack 一等通知渠道；SearXNG 在未配置自建实例时可以自动发现公共实例并按受控轮询降级；Tavily 时效新闻链路修复后，严格时效过滤不再错误丢光有效结果。
- 💼 **持仓与市场复盘链路更稳** — A 股 market review 可选接入 TickFlow 强化指数与涨跌统计；持仓账本写入改为串行化以缩小并发超卖窗口；汇率刷新入口和禁用态提示也更加清晰，减少用户误判。

### 新功能

- 🔎 **Web 股票自动补全 MVP** — 首页分析输入框新增本地索引驱动的自动补全，支持股票代码、中文名、拼音和别名匹配；选中候选后会提交 canonical code，并透传 `stock_name`、`original_query`、`selection_source` 到分析请求、任务状态和 SSE 事件；索引加载失败时自动退回旧输入模式，不阻断原有提交流程。同步补充了静态索引加载器、索引生成脚本和前后端契约测试。分阶段进行开发，第一阶段仅支持 A 股。
- 💬 **Slack 一等通知渠道** — 新增 Slack 原生通知支持，同时支持 Bot Token 和 Incoming Webhook 两种接入方式；同时配置时优先使用 Bot API，确保文本与图片发送到同一频道；Bot Token 模式支持图片上传（raw body POST，不使用 multipart）；新增 `SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID`、`SLACK_WEBHOOK_URL` 配置项，GitHub Actions 工作流同步补齐对应 Secrets 传递。
- 🌍 **报告输出语言可配置**（Issue #758）— 新增 `REPORT_LANGUAGE=zh|en`，默认 `zh`；语言设置会同步注入普通分析与 Agent Prompt，并覆盖 Markdown/Jinja 模板、通知 fallback、历史/API `report_language` 元数据及 Web 报告页固定文案，避免“英文内容 + 中文壳子”的混合输出。
- 🚀 **Agent 与普通分析模型解耦**（Issue #692）— 新增 `AGENT_LITELLM_MODEL`（留空继承 `LITELLM_MODEL`，无前缀按 `openai/<model>` 归一）；Agent 执行链路与 `/api/v1/agent/models` 的 `is_primary/is_fallback` 标记改为基于 Agent 实际模型链路；系统配置与启动期校验补齐 `AGENT_LITELLM_MODEL` 的 `unknown_model/missing_runtime_source` 检查；Web 设置页新增 Agent 主模型选择并与渠道模式运行时配置同步。
- 🔎 **SearXNG 公共实例自动发现与受控轮询**（#752）— 新增 `SEARXNG_PUBLIC_INSTANCES_ENABLED`，在未配置 `SEARXNG_BASE_URLS` 时默认从 `searx.space` 拉取公共实例列表，并按受控轮询顺序选择实例；同次请求内遇到超时、连接错误、HTTP 非 200 或无效 JSON 会自动切换到下一个实例。已配置自建实例的用户保持原有优先级与语义不变；`daily_analysis` GitHub Actions 工作流也已支持显式透传该开关并在启动日志中展示当前状态。
- 📈 **TickFlow market review enhancement** (#632) — 新增可选 `TICKFLOW_API_KEY`；配置后，A 股大盘复盘的主要指数行情优先尝试 TickFlow；若当前 TickFlow 套餐支持标的池查询，市场涨跌统计也会优先尝试 TickFlow。失败或权限不足时立即回退到现有 `AkShare / Tushare / efinance` 链路；板块涨跌榜回退顺序保持不变。接入层同时适配了真实 SDK 契约：主指数查询按单次请求上限分批拉取，并将 TickFlow 返回的比例型 `change_pct` / `amplitude` 统一转换为项目内部的百分比口径。

### 改进

- **Dashboard state slice and workspace closure** — moved Home / Dashboard state into `stockPoolStore`, consolidated history selection, report loading, task syncing, polling refresh, and markdown drawer handling under a single state slice.
- **Dashboard panel standardization** — kept the current dashboard layout contract stable while unifying history, report, news, and markdown presentation with shared tokens, standardized states, and bounded in-panel scrolling for the history list.
- **Dashboard-to-chat follow-up bridge** — routed “Ask AI” follow-ups through report-context hydration instead of direct cross-page state coupling, while keeping chat sends usable when enriched history context is still loading.
- 💼 **持仓账本并发写入串行化**（#742）— 持仓源事件写入/删除现在会在 SQLite 下先获取串行化写锁，减少并发卖出把超售流水写入账本的窗口；直接持仓写接口在锁竞争时返回 `409 portfolio_busy`，CSV 导入保持逐条提交并把 busy 计入 `failed_count`。
- 💱 **持仓页汇率手动刷新入口补齐**（#748）— Web `/portfolio` 页面现在会在“汇率状态”卡片中展示“刷新汇率”按钮，直接调用现有 `POST /api/v1/portfolio/fx/refresh` 接口；刷新后会仅重载快照与风险数据，并以内联摘要反馈“已更新 / 仍 stale / 刷新失败”的结果，减少用户对 `fxStale` 长时间停留的误解。

### 修复

- 🔎 **Web 自动补全 Enter 提交语义修正** — 股票自动补全在搜索命中候选时不再默认高亮第一项；候选列表展开但用户尚未用方向键或鼠标明确选中时，按 Enter 会继续提交原始输入，避免手动输入被第一条候选静默覆盖。
- 🌍 **补齐 `REPORT_LANGUAGE` 启动解析与历史展示本地化边界** — `Config` 在启动时继续遵循“真实环境变量优先、`.env` 兜底”的既有语义，并在两者冲突时输出显式告警，减少 `REPORT_LANGUAGE` 来源不清带来的误判；同时 `/api/v1/history/{id}` 英文详情响应会同步本地化 `sentiment_label`，历史 Markdown 也会正确识别英文 `bias_status` 的风险等级 emoji，避免出现 `乐观` 或 `🚨Safe` 这类中英混排/误报展示。
- 📰 **Tavily 时效新闻检索发布时间映射修复**（#782）— Tavily 在股票新闻和严格时效的情报维度中现在会显式使用 `topic="news"`，并兼容 `published_date` / `publishedDate` 两种发布时间字段；修复了 Tavily 明明返回结果却在后续硬过滤阶段被全部记为 `drop_unknown` 丢弃的问题，同时将机构分析、业绩预期、行业分析等分析型维度恢复为宽源搜索，不再被统一压缩成新闻模式。
- 💱 **持仓页汇率刷新禁用语义修正**（#772）— 当 `PORTFOLIO_FX_UPDATE_ENABLED=false` 时，`POST /api/v1/portfolio/fx/refresh` 现在会返回显式 `refresh_enabled=false` 与 `disabled_reason`，Web `/portfolio` 页面会明确提示“汇率在线刷新已被禁用”，不再误报“当前范围无可刷新的汇率对”。
- 🤖 **Agent timeout and config hardening** — `AGENT_ORCHESTRATOR_TIMEOUT_S` now also protects the legacy single-agent ReAct loop, parallel tool batches stop waiting once the remaining budget is exhausted, and invalid numeric `.env` values fall back to safe defaults with warnings instead of crashing startup.
- 🌐 **CORS wildcard + credentials compatibility** — `CORS_ALLOW_ALL=true` no longer combines `allow_origins=["*"]` with credentialed requests, avoiding browser-side cross-origin failures in demo/development setups.
- 🧭 **Unavailable Agent settings hidden from Web UI** — Deep Research / Event Monitor controls are now treated as compatibility-only metadata in the current branch and are removed from the Settings page to avoid exposing non-functional toggles.

### 文档

- 新增 Ollama 本地模型配置说明，同步更新 `README.md` 与 `docs/README_EN.md`（Fixes #690）
- 完善 Ollama 配置说明：`docs/full-guide.md` / `docs/full-guide_EN.md` 环境变量表与 Note 补充 `OLLAMA_API_BASE`，避免英文用户误以为 Ollama 不能作为独立配置入口；合并重复的 `OLLAMA_API_BASE` 条目为单一条目
- 明确文档同步治理边界：补充 `README.md`、专题文档、双语文档与交付说明之间的默认同步规则，减少后续文档漂移

## [3.8.0] - 2026-03-17

### 发布亮点

- 🎨 **Web 界面完成一轮骨架升级** — 新的 App Shell、侧边导航、主题能力、登录与系统设置流程已经串成统一体验，桌面端加载背景也完成对齐。
- 📈 **分析上下文继续补强** — 美股新增社交舆情情报，A 股补齐财报与分红结构化上下文，Tushare 新接入筹码分布和行业板块涨跌数据。
- 🔒 **运行稳定性与配置兼容性提升** — 退出登录会立即让旧会话失效，定时启动兼容旧配置，运行中的 `MAX_WORKERS` 调整和新闻时效窗口反馈更清晰。
- 💼 **持仓纠错链路更完整** — 超售会被前置拦截，错误交易/资金流水/公司行为可以直接删除回滚，便于修复脏数据。

### 新功能

- 📱 **美股社交舆情情报** — 新增 Reddit / X / Polymarket 社交媒体情绪数据源，为美股分析提供实时社交热度、情绪评分和提及量等补充指标；完全可选，仅在配置 `SOCIAL_SENTIMENT_API_KEY` 后对美股生效。
- 📊 **A 股财报与分红结构化增强**（Issue #710）— `fundamental_context.earnings.data` 新增 `financial_report` 与 `dividend` 字段；分红统一按“仅现金分红、税前口径”计算，并补充 `ttm_cash_dividend_per_share` 与 `ttm_dividend_yield_pct`；分析/历史 API 的 `details` 追加 `financial_report`、`dividend_metrics` 可选字段，保持 fail-open 与向后兼容。
- 🔍 **接入 Tushare 筹码与行业板块接口** — 新增筹码分布、行业板块涨跌数据获取能力，并统一纳入配置化数据源优先级；默认按上海时间区分盘中/盘后交易日取数，优先使用 Tushare 同花顺接口，必要时降级到东财。
- 🧱 **Web UI 基础骨架升级** — 重建共享设计令牌与通用组件，新增 App Shell、Theme Provider、侧边导航，并同步调整 Electron 加载背景，为 Web / Desktop 的统一体验打底。
- 🔐 **登录与系统设置流程重做** — 重构 Login、Settings 与 Auth 管理流程，补上显式的认证 setup-state 处理，并让 Web 端与运行时认证配置 API 行为对齐。
- 🧪 **前端回归与冒烟覆盖补强** — 新增并扩展登录、首页、聊天、移动端 Shell、设置页、回测入口等关键路径的组件测试与 Playwright smoke coverage。

### 变更

- 🧭 **页面接入新 Shell 布局契约** — Home、Chat、Settings、Backtest 已统一接入新的页面容器、抽屉和滚动约定，降低 UI 迁移期间的页面行为不一致。
- 💾 **设置页状态同步更稳** — 优化草稿保留、直接保存同步与冲突处理，减少模块级保存后前后端配置状态不一致的问题。
- 🎭 **登录页视觉基线回归** — 登录页恢复到既有 `006` 分支的视觉基线，同时保留新的认证状态逻辑和统一表单交互模型。
- 🏛️ **AI 协作治理资产加固** — 收敛并加强 `AGENTS.md`、`CLAUDE.md`、Copilot 指令和校验脚本的一致性约束，降低治理资产长期漂移风险。

### Added

- **Web UI foundation refresh** — rebuilt shared design tokens and common primitives, introduced the app shell, theme provider, sidebar navigation, and Electron loading background alignment for the upgraded desktop/web experience
- **Settings and auth workflow overhaul** — rebuilt the Login, Settings, and Auth management flows, added explicit auth setup-state handling, and aligned the Web UI with the runtime auth configuration APIs
- **UI regression coverage and smoke checks** — expanded targeted frontend tests and added Playwright smoke coverage for login, home, chat, mobile shell, settings, and backtest entry flows

### Changed

- **Shell-driven page integration** — aligned Home, Chat, Settings, and Backtest with the new shell layout contract so routing, drawer behavior, and page-level scrolling are consistent during the UI migration
- **Settings state consistency** — refined draft preservation, direct-save synchronization, and conflict handling so module-level saves no longer leave the page out of sync with backend config state
- **Login visual baseline** — restored the login page visual treatment to the established `006` branch baseline while keeping the newer auth-state logic and unified form interaction model

### 修复

- ⏰ **定时启动立即执行兼容旧配置**（Issue #726）— `SCHEDULE_RUN_IMMEDIATELY` 未设置时会回退读取 `RUN_IMMEDIATELY`，修复升级后旧 `.env` 在定时模式下的兼容性问题；同时澄清 `.env.example` / README 中两个配置项的适用范围，并注明 Outlook / Exchange 强制 OAuth2 暂不支持。
- 🧵 **运行期 `MAX_WORKERS` 配置生效与可解释性增强**（#633）— 修复异步分析队列未按 `MAX_WORKERS` 同步的问题；新增任务队列并发 in-place 同步机制（空闲即时生效、繁忙延后），并在设置保存反馈与运行日志中明确输出 `profile/max/effective`，减少“参数未生效”误解。
- 🔐 **退出登录立即失效现有会话** — `POST /api/v1/auth/logout` 现在会轮换 session secret，避免旧 cookie 在退出后仍可继续访问受保护接口；同浏览器标签页和并发页面会被同步登出。认证开启时，该接口也不再属于匿名白名单，未登录请求会返回 `401`，避免匿名请求触发全局 session 失效。
- 🧮 **Tushare 板块/筹码调用限流与跨日缓存修复** — 新增的 `trade_cal`、行业板块排行、筹码分布链路统一接入 `_check_rate_limit()`；交易日历缓存改为按自然日刷新，避免服务跨天运行后继续沿用旧交易日判断取数日期。
- 💼 **持仓超售拦截与错误流水恢复**（#718）— `POST /api/v1/portfolio/trades` 现在会在写入前校验可卖数量，超售返回 `409 portfolio_oversell`；持仓页新增交易 / 资金流水 / 公司行为删除能力，删除后会同步失效仓位缓存与未来快照，便于从错误流水中直接恢复。
- 📧 **邮件中文发件人名编码**（#708）— 邮件通知现在会对包含中文的 `EMAIL_SENDER_NAME` 自动做 RFC 2047 编码，并在异常路径补充 SMTP 连接清理，修复 GitHub Actions / QQ SMTP 下 `'ascii' codec can't encode characters` 导致的发送失败。
- 🐛 **港股 Agent 实时行情去重与快速路由** — 统一 `HK01810` / `1810.HK` / `01810` 等港股代码归一规则；港股实时行情改为直接走单次 `akshare_hk` 路径，避免按 A 股 source priority 重复触发同一失败接口；Agent 运行期对显式 `retriable=false` 的工具失败增加短路缓存，减少同轮分析中的重复失败调用。
- 📰 **新闻时效硬过滤与策略分窗**（#697）— 新增 `NEWS_STRATEGY_PROFILE`（`ultra_short/short/medium/long`）并与 `NEWS_MAX_AGE_DAYS` 统一计算有效窗口；搜索结果在返回后执行发布时间硬过滤（时间未知剔除、超窗剔除、未来仅容忍 1 天），并在历史 fallback 链路追加相同约束，避免旧闻再次进入“最新动态/风险警报”。

### 文档

- ☁️ **新增云服务器 Web 界面部署与访问教程**（Fixes #686）— 补充从云端部署到外部访问的落地说明，降低远程自托管门槛。
- 🌍 **补齐英文文档索引与协作文档** — 新增英文文档索引、贡献指南、Bot 命令文档，并补充中英双语 issue / PR 模板，方便中英文协作与外部贡献者理解项目入口。
- 🏷️ **本地化 README 补充 Trendshift badge** — 在多语言 README 中同步补上新版能力入口标识，减少中英文说明面不一致。

## [3.7.0] - 2026-03-15

### 新功能

- 💼 **持仓管理 P0 全功能上线**（#677，对应 Issue #627）
  - **核心账本与快照闭环**：新增账户、交易、现金流水、企业行为、持仓缓存、每日快照等核心数据模型与 API 端点；支持 FIFO / AVG 双成本法回放；同日事件顺序固定为 `现金 → 企业行为 → 交易`；持仓快照写入采用原子事务。
  - **券商 CSV 导入**：支持华泰 / 中信 / 招商首批适配，含列名别名兼容；两阶段接口（解析预览 + 确认提交）；`trade_uid` 优先、key-field hash 兜底的幂等去重；前导零股票代码完整保留。
  - **组合风险报告**：集中度风险（Top Positions + A 股板块口径）、历史回撤监控（支持回填缺失快照）、止损接近预警；多币种统一换算 CNY 口径；汲取失败时回退最近成功汇率并标记 stale。
  - **Web 持仓页**（`/portfolio`）：组合总览、持仓明细、集中度饼图、风险摘要、全组合 / 单账户切换；手工录入交易 / 资金流水 / 企业行为；内嵌账户创建入口；CSV 解析 + 提交闭环与券商选择器。
  - **Agent 持仓工具**：新增 `get_portfolio_snapshot` 数据工具，默认紧凑摘要，可选持仓明细与风险数据。
  - **事件查询 API**：新增 `GET /portfolio/trades`、`GET /portfolio/cash-ledger`、`GET /portfolio/corporate-actions`，支持日期过滤与分页。
  - **可扩展 Parser Registry**：应用级共享注册，支持运行时注册新券商；新增 `GET /portfolio/imports/csv/brokers` 发现接口。

- 🎨 **前端设计系统与原子组件库**（#662）
  - 引入渐进式双主题架构（HSL 变量化设计令牌），清理历史 Legacy CSS；重构 Button / Card / Badge / Collapsible / Input / Select 等 20+ 核心组件；新增 `clsx` + `tailwind-merge` 类名合并工具；提升历史记录、LLM 配置等页面可读性。

- ⚡ **分析 API 异步契约与启动优化**（#656）
  - 规范 `POST /api/v1/analysis/analyze` 异步请求的返回契约；优化服务启动辅助逻辑；修复前端报告类型联合定义与后端响应对齐问题。

### 修复

- 🔔 **Discord 环境变量向后兼容**（#659）：运行时新增 `DISCORD_CHANNEL_ID` → `DISCORD_MAIN_CHANNEL_ID` 的 fallback 读取；历史配置用户无需修改即可恢复 Discord Bot 通知；全部相关文档与 `.env.example` 对齐。
- 🔧 **GitHub Actions Node 24 升级**（#665）：将所有 GitHub 官方 actions 升级至 Node 24 兼容版本，消除 CI 日志中的 Node.js 20 deprecation warning（影响 2026-06-02 强制升级窗口）。
- 📅 **持仓页默认日期本地化**：手工录入表单默认日期改用本地时间（`getFullYear/Month/Date`），修复 UTC-N 时区用户在当天晚间出现日期偏移的问题。
- 🔁 **CSV 导入去重逻辑加固**：dedup hash 纳入行序号作为区分因子，确保同字段合法分笔成交不被误折叠；同时在 `trade_uid` 存在时也持久化 hash，防止混合来源重复写入。

### 变更

- `POST /api/v1/portfolio/trades` 在同账户内 `trade_uid` 冲突时返回 `409`。
- 持仓风险响应新增 `sector_concentration` 字段（增量扩展），原有 `concentration` 字段保持不变。
- 分析 API `analyze` 接口异步行为契约文档化；前端报告类型联合更新。

### 测试

- 新增持仓核心服务测试（FIFO / AVG 部分卖出、同日事件顺序、重复 `trade_uid` 返回 409、快照 API 契约）。
- 新增 CSV 导入幂等性、合法分笔成交不误去重、去重边界、风险阈值边界、汇率降级行为测试。
- 新增 Agent `get_portfolio_snapshot` 工具调用测试。
- 新增分析 API 异步契约回归测试。

## [3.6.0] - 2026-03-14

### Added
- 📊 **Web UI Design System** — implemented dual-theme architecture and terminal-inspired atomic UI components
- 📊 **UI Components Refactoring** — integrated `clsx` and `tailwind-merge` for robust class composition across Web UI

- 🗑️ **History batch deletion** — Web UI now supports multi-selection and batch deletion of analysis history; added `POST /api/v1/history/batch-delete` endpoint and `ConfirmDialog` component.
- 🔐 **Auth settings API** — new `POST /api/v1/auth/settings` endpoint to enable or disable Web authentication at runtime and set the initial admin password when needed
- openclaw Skill 集成指南 — 新增 [docs/openclaw-skill-integration.md](openclaw-skill-integration.md)，说明如何通过 openclaw Skill 调用 DSA API
- ⚙️ **LLM channel protocol/test UX** — `.env` and Web settings now share the same channel shape (`LLM_CHANNELS` + `LLM_<NAME>_PROTOCOL/BASE_URL/API_KEY/MODELS/ENABLED`); settings page adds per-channel connection testing, primary/fallback/vision model selection, and protocol-aware model prefixing
- 🤖 **Agent architecture Phase 0+1** — shared protocols (`AgentContext`, `AgentOpinion`, `StageResult`), extracted `run_agent_loop()` runner, `AGENT_ARCH` switch (`single`/`multi`), config registry entries
- 🔍 **Bot NL routing** — two-layer natural-language routing: cheap regex pre-filter (stock codes + finance keywords) → lightweight LLM intent parsing; controlled by `AGENT_NL_ROUTING=true`; supports multi-stock and strategy extraction
- 💬 **`/ask` multi-stock analysis** — comma or `vs` separated codes (max 5), parallel thread execution with 150s timeout (preserves partial results), Markdown comparison summary table at top
- 📋 **`/history` command** — per-user session isolation via `{platform}_{user_id}:{scope}` format (colon delimiter prevents prefix collision); lists both `/chat` and `/ask` sessions; view detail or clear
- 📊 **`/strategies` command** — lists available strategy YAML files grouped by category (趋势/形态/反转/框架) with ✅/⬜ activation status
- 🔧 **Backtest summary tools** — `get_strategy_backtest_summary` and `get_stock_backtest_summary` registered as read-only Agent tools
- ⚙️ **Agent auto-detection** — `is_agent_available()` auto-detects from `LITELLM_MODEL`; explicit `AGENT_MODE=true/false` takes full precedence
- 🏗️ **Multi-Agent orchestrator (Phase 2)** — `AgentOrchestrator` with 4 modes (`quick`/`standard`/`full`/`strategy`); drop-in replacement for `AgentExecutor` via `AGENT_ARCH=multi`; `BaseAgent` ABC with tool subset filtering, cached data injection, and structured `AgentOpinion` output
- 🧩 **Specialised agents (Phase 2-4)** — `TechnicalAgent` (8 tools, trend/MA/MACD/volume/pattern analysis), `IntelAgent` (news & sentiment, risk flag propagation), `DecisionAgent` (synthesis into Decision Dashboard JSON), `RiskAgent` (7 risk categories, two-level severity with soft/hard override)
- 📈 **Strategy system (Phase 3)** — `StrategyAgent` (per-strategy evaluation from YAML skills), `StrategyRouter` (rule-based regime detection → strategy selection), `StrategyAggregator` (weighted consensus with backtest performance factor)
- 🔬 **Deep Research agent (Phase 5)** — `ResearchAgent` with 3-phase approach (decompose → research sub-questions → synthesise report); token budget tracking; new `/research` bot command with aliases (`/深研`, `/deepsearch`)
- 🧠 **Memory & calibration (Phase 6)** — `AgentMemory` with prediction accuracy tracking, confidence calibration (activates after minimum sample threshold), strategy auto-weighting based on historical win rate
- 📊 **Portfolio Agent (Phase 7)** — `PortfolioAgent` for multi-stock portfolio analysis (position sizing, sector concentration, correlation risk, cross-market linkage, rebalance suggestions)
- 🔔 **Event-driven alerts (Phase 7)** — `EventMonitor` with `PriceAlert`, `VolumeAlert`, `SentimentAlert` rules; async checking, callback notifications, serializable persistence
- ⚙️ **New config entries** — `AGENT_ORCHESTRATOR_MODE`, `AGENT_RISK_OVERRIDE`, `AGENT_DEEP_RESEARCH_BUDGET`, `AGENT_MEMORY_ENABLED`, `AGENT_STRATEGY_AUTOWEIGHT`, `AGENT_STRATEGY_ROUTING` — all registered in `config.py` + `config_registry.py` (WebUI-configurable)

### Changed
- 🔐 **Auth password state semantics** — stored password existence is now tracked independently from auth enablement; when auth is disabled, `/api/v1/auth/status` returns `passwordSet=false` while preserving the saved password for future re-enable
- 🔐 **Auth settings re-enable hardening** — re-enabling auth with a stored password now requires `currentPassword`, and failed session creation rolls back the auth toggle to avoid lockout
- ♻️ **AgentExecutor refactored** — `_run_loop` delegates to shared `runner.run_agent_loop()`; removed duplicated serialization/parsing/thinking-label code
- ♻️ **Unified agent switch** — Bot, API, and Pipeline all use `config.is_agent_available()` instead of divergent `config.agent_mode` checks
- 📖 **README.md** — expanded Bot commands section (ask/chat/strategies/history), added NL routing note, updated agent mode description
- 📖 **.env.example** — added `AGENT_ARCH` and `AGENT_NL_ROUTING` configuration documentation
- 🔌 **Analysis API async contract** — `POST /api/v1/analysis/analyze` now documents distinct async `202` payloads for single-stock vs batch requests, and `report_type=full` is treated consistently with the existing full-report behavior

### Fixed
- 🐛 **Analysis API blank-code guardrails** — `POST /api/v1/analysis/analyze` now drops whitespace-only entries before batch enqueue and returns `400` when no valid stock code remains
- 🐛 **Bare `/api` SPA fallback** — unknown API paths now return JSON `404` consistently for both `/api/...` and the exact `/api` path
- 🎮 **Discord channel env compatibility** — runtime now accepts legacy `DISCORD_CHANNEL_ID` as a fallback for `DISCORD_MAIN_CHANNEL_ID`, and the docs/examples now use the same variable name as the actual workflow/config implementation
- 🐛 **Session secret rotation on Windows** — use atomic replace so auth toggles invalidate existing sessions even when `.session_secret` already exists
- 🐛 **Auth toggle atomicity** — persist `ADMIN_AUTH_ENABLED` before rotating session secret; on rotation failure, roll back to the previous auth state
- 🔧 **LLM runtime selection guardrails** — YAML 模式下渠道编辑器不再覆盖 `LITELLM_MODEL` / fallback / Vision；系统配置校验补上全部渠道禁用后的运行时来源检查，并修复 `vertexai/...` 这类协议别名模型被重复加前缀的问题
- 🐛 **Multi-stock `/ask` follow-up regressions** — portfolio overlay now shares the same timeout budget as the per-stock phase and is skipped on timeout instead of blocking the bot reply; `/history` now stores the readable per-stock summary instead of raw dashboard JSON; condensed multi-stock output now renders numeric `sniper_points` values
- 🐛 **Decision dashboard enum compatibility** — multi-agent `DecisionAgent` now keeps `decision_type` within the legacy `buy|hold|sell` contract and normalizes stray `strong_*` outputs before risk override, pipeline conversion, and downstream统计/通知汇总
- 🛟 **Multi-Agent partial-result fallback** — `IntelAgent` now caches parsed intel for downstream reuse, shared JSON parsing tolerates lightly malformed model output, and the orchestrator preserves/synthesizes a minimal dashboard on timeout or mid-pipeline parse failure instead of always collapsing to `50/观望/未知`
- 🐛 **Shared LiteLLM routing restored** — bot NL intent parsing and `ResearchAgent` planning/synthesis now reuse the same LiteLLM adapter / Router / fallback / `api_base` injection path as the main Agent flow, so `LLM_CHANNELS` / `LITELLM_CONFIG` / OpenAI-compatible deployments behave consistently
- 🐛 **Bot chat session backward compatibility** — `/chat` now keeps using the legacy `{platform}_{user_id}` session id when old history already exists, and `/history` can still list / view / clear those pre-migration sessions alongside the new `{platform}_{user_id}:chat` format
- 🐛 **EventMonitor unsupported rule rejection** — config validation/runtime loading now reject or skip alert types the monitor cannot actually evaluate yet, so schedule mode no longer silently accepts permanent no-op rules
- 🐛 **P0 基本面聚合稳定性修复** (#614) — 修复 `get_stock_info` 板块语义回归（新增 `belong_boards` 并保留 `boards` 兼容别名）、引入基本面上下文精简返回以控制 token、为基本面缓存增加最大条目淘汰，并补齐 ETF 总体状态聚合与 NaN 板块字段过滤，保证 fail-open 与最小入侵。
- 🔧 **GitHub Actions 搜索引擎环境变量补充** — 工作流新增 `MINIMAX_API_KEYS`、`BRAVE_API_KEYS`、`SEARXNG_BASE_URLS` 环境变量映射，使 GitHub Actions 用户可配置 MiniMax、Brave、SearXNG 搜索服务（此前 v3.5.0 已添加 provider 实现但缺少工作流配置）
- 🤖 **Multi-Agent runtime consistency** — `AGENT_MAX_STEPS` now propagates to each orchestrated sub-agent; added cooperative `AGENT_ORCHESTRATOR_TIMEOUT_S` budget to stop overlong pipelines before they cascade further
- 🔌 **Multi-Agent feature wiring** — `AGENT_RISK_OVERRIDE` now actively downgrades final dashboards on hard risk findings; `AGENT_MEMORY_ENABLED` now injects recent analysis memory + confidence calibration into specialised agents; multi-stock `/ask` now runs `PortfolioAgent` to add portfolio-level allocation and concentration guidance
- 🔔 **EventMonitor runtime wiring** — schedule mode can now load alert rules from `AGENT_EVENT_ALERT_RULES_JSON`, poll them at `AGENT_EVENT_MONITOR_INTERVAL_MINUTES`, and send triggered alerts through the existing notification service
- 🛠️ **Follow-up stability fixes** — multi-stock `/ask` now falls back to usable text output when dashboard JSON parsing fails; EventMonitor skips semantically invalid rules instead of aborting schedule startup; background alert polling now runs independently of the main scheduled analysis loop
- 🧪 **Multi-Agent regression coverage** — added orchestrator execution tests for `run()`, `chat()`, critical-stage failure, graceful degradation, and timeout handling
- 🧹 **PortfolioAgent cleanup** — `post_process()` now reuses shared JSON parsing and removed stale unused imports
- 🚦 **Bot async dispatch** — `CommandDispatcher` now exposes `dispatch_async()`; NL intent parsing and default command execution are offloaded from the event loop, DingTalk stream awaits async handlers directly, and Feishu stream processing is moved off the SDK callback thread
- 🌐 **Async webhook handler** — new `handle_webhook_async()` function in `bot/handler.py` for use from async contexts (e.g. FastAPI); calls `dispatch_async()` directly without thread bridging
- 🧵 **Feishu stream ThreadPoolExecutor** — replaced unbounded per-message `Thread` spawning with a capped `ThreadPoolExecutor(max_workers=8)` to prevent thread explosion under message bursts
- 🔒 **EventMonitor safety** — `_check_volume()` now safely handles `get_daily_data` returning `None` (no tuple-unpacking crash); `on_trigger` callbacks support both sync and async callables via `asyncio.to_thread`/`await`
- 🧹 **ResearchAgent dedup** — `_filtered_registry()` now delegates to `BaseAgent._filtered_registry()` instead of duplicating the filtering logic
- 🧹 **Bot trailing whitespace cleanup** — removed W291/W293 whitespace issues across `bot/handler.py`, `bot/dispatcher.py`, `bot/commands/base.py`, `bot/platforms/feishu_stream.py`, `bot/platforms/dingtalk_stream.py`
- 🐛 **Dispatcher `_parse_intent_via_llm` safety** — replaced fragile `'raw' in dir()` with `'raw' in locals()` for undefined-variable guard in `JSONDecodeError` handler
- 🐛 **筹码结构 LLM 未填写时兜底补全** (#589) — DeepSeek 等模型未正确填写 `chip_structure` 时，自动用数据源已获取的筹码数据补全，保证各模型展示一致；普通分析与 Agent 模式均生效
- 🐛 **历史报告狙击点位显示原始文本** (#452) — 历史详情页现优先展示 `raw_result.dashboard.battle_plan.sniper_points` 中的原始字符串，避免 `analysis_history` 数值列把区间、说明文字或复杂点位压缩成单个数字；保留原有数值列作为回退
- 🐛 **Session prefix collision** — user ID `123` could see sessions of user `1234` via `startswith`; fixed with colon delimiter in session_id format
- 🐛 **NL pre-filter false positives** — `re.IGNORECASE` caused `[A-Z]{2,5}` to match common English words like "hello"; removed global flag, use inline `(?i:...)` only for English finance keywords
- 🐛 **Dotted ticker in strategy args** — `_get_strategy_args()` didn't recognize `BRK.B` as a stock code, leaving it in strategy text; now accepts `TICKER.CLASS` format
- ⏱️ **efinance 长调用挂起修复** (#660) — 为所有 efinance API 调用引入 `_ef_call_with_timeout()` 包装（默认 30 秒，可通过 `EFINANCE_CALL_TIMEOUT` 配置）；使用 `executor.shutdown(wait=False)` 确保超时后不再阻塞主线程，彻底消除 81 分钟挂起问题
- 🛡️ **类型安全内容完整性检查** (#660) — `check_content_integrity()` 现在将非字符串类型的 `operation_advice` / `analysis_summary` 视为缺失字段，避免下游 `get_emoji()` 因 `dict.strip()` 崩溃
- 📄 **报告保存与通知解耦** (#660) — `_save_local_report()` 不再依赖 `send_notification` 标志触发，`--no-notify` 模式下本地报告照常保存
- 🔄 **operation_advice 字典归一化** (#660) — Pipeline 和 BacktestEngine 现在将 LLM 返回的 `dict` 格式 `operation_advice` 通过 `decision_type`（不区分大小写）映射为标准字符串，防止因模型输出格式变化导致崩溃
- 🛡️ **runner.py usage None 防护** (#660) — `response.usage` 为 `None` 时不再抛出 `AttributeError`，回退为 0 token 计数
- 📋 **orchestrator 静默失败改为日志警告** (#660) — `IntelAgent` / `RiskAgent` 阶段失败现在记录 `WARNING` 而非静默跳过，便于诊断

### Notes
- ⚠️ **Multi-worker auth toggles** — runtime auth updates are process-local; multi-worker deployments must restart/roll workers to keep auth state consistent

## [3.5.0] - 2026-03-12

### Added
- 📊 **Web UI full report drawer** (Fixes #214) — history page adds "Full Report" button to display the complete Markdown analysis report in a side drawer; new `GET /api/v1/history/{record_id}/markdown` endpoint
- 📊 **LLM cost tracking** — all LLM calls (analysis, agent, market review) recorded in `llm_usage` table; new `GET /api/v1/usage/summary?period=today|month|all` endpoint returns aggregated token usage by call type and model
- 🔍 **SearXNG search provider** (Fixes #550) — quota-free self-hosted search fallback; priority: Bocha > Tavily > Brave > SerpAPI > MiniMax > SearXNG
- 🔍 **MiniMax web search provider** — `MiniMaxSearchProvider` with circuit breaker (3 failures → 300s cooldown) and dual time-filtering; configured via `MINIMAX_API_KEYS`
- 🤖 **Agent models discovery API** — `GET /api/v1/agent/models` returns available model deployments (primary/fallback/source/api_base) for Web UI model selector
- 🤖 **Agent chat export & send** (#495) — export conversation to .md file; send to configured notification channels; new `POST /api/v1/agent/chat/send`
- 🤖 **Agent background execution** (#495) — analysis continues when switching pages; badge notification on completion; auto-cancel in-progress stream on session switch
- 📝 **Report Engine P0** — Pydantic schema validation for LLM JSON; Jinja2 templates (markdown/wechat/brief) with legacy fallback; content integrity checks with retry; brief mode (`REPORT_TYPE=brief`); history signal comparison
- 📦 **Smart import** — multi-source import from image/CSV/Excel/clipboard; Vision LLM extracts code+name+confidence; name→code resolver (local map + pinyin + AkShare); confidence-tiered confirmation
- ⚙️ **GitHub Actions LiteLLM config** — workflow supports `LITELLM_CONFIG`/`LITELLM_CONFIG_YAML` for flexible AI provider configuration
- ⚙️ **Config engine refactor & system API** (#602) — unified config registry, validation and API exposure
- 📖 **LLM configuration guide** — new `docs/LLM_CONFIG_GUIDE.md` covering 3-tier config, quick start, Vision/Agent/troubleshooting

### Fixed
- 🐛 **analyze_trend always reports No historical data** (#600) — now fetches from DB/DataFetcher instead of broken `get_analysis_context`
- 🐛 **Chip structure fallback when LLM omits it** (#589) — auto-fills from data source chip data for consistent display across models
- 🐛 **History sniper points show raw text** (#452) — prioritizes original strings over compressed numeric values
- 🐛 **GitHub Actions ENABLE_CHIP_DISTRIBUTION configurable** (#617) — no longer hardcoded, supports vars/secrets override
- 🐛 **`.env` save preserves comments and blank lines** — Web settings no longer destroys `.env` formatting
- 🐛 **Agent model discovery fixes** — legacy mode includes LiteLLM-native providers; source detection aligned with runtime; fallback deployments no longer expanded per-key
- 🐛 **Stooq US stock previous close semantics** — no longer misuses open price as previous close
- 🐛 **Stock name prefetch regression** — prioritizes local `STOCK_NAME_MAP` before remote queries
- 🐛 **AkShare limit-up/down calculation** (#555) — fixed market analysis statistics
- 🐛 **AkShare Tencent source field index & ETF quote mapping** (#579)
- 🐛 **Pytdx stock name cache pagination** (#573) — prevents cache overflow
- 🐛 **PushPlus oversized report chunking** (#489) — auto-segments long content
- 🐛 **Agent chat cancel & switch** (#495) — cancel no longer misreports as failure; fast switch no longer overwrites stream state
- 🐛 **MiniMax search status in `/status` command** (#587)
- 🐛 **config_registry duplicate BOCHA_API_KEYS** — removed duplicate dict entry that silently overwrote config

### Changed
- 🔎 **Fetcher failure observability** — logs record start/success/failure with elapsed time, failover transitions; Efinance/Akshare include upstream endpoint and classified failure categories
- ♻️ **Data source resilience & cleanup** (#602) — fallback chain optimization
- ♻️ **Image extract API response extension** — new `items` field (code/name/confidence); `codes` preserved for backward compatibility
- ♻️ **Import parse error messages** — specific failure reasons for Excel/CSV; improved logging with file type and size

### Docs
- 📖 LLM config guide refactored for clarity (#583)
- 📖 `image-extract-prompt.md` with full prompt documentation
- 📖 AkShare fallback cache TTL documentation
## [3.4.10] - 2026-03-07

### Fixed
- 🐛 **EfinanceFetcher ETF OHLCV data** (#541, #527) — switch `_fetch_etf_data` from `ef.fund.get_quote_history` (NAV-only, no OHLCV, no `beg`/`end` params) to `ef.stock.get_quote_history`; ETFs now return proper open/high/low/close/volume/amount instead of zeros; remove obsolete NAV column mappings from `_normalize_data`
- 🐛 **tiktoken 0.12.0 `Unknown encoding cl100k_base`** (#537) — pin `tiktoken>=0.8.0,<0.12.0` in requirements.txt to avoid plugin-registration regression introduced in 0.12.0
- 🐛 **Web UI API error classification** (#540) — frontend no longer treats every HTTP 400 as the same "server/network" failure; now distinguishes Agent disabled / missing params / model-tool incompatibility / upstream LLM errors / local connection failures
- 🐛 **北交所代码识别失败** (#491, #533) — 8/4/92 开头的 6 位代码现正确识别为北交所；Tushare/Akshare/Yfinance 等数据源支持 .BJ 或 bj 前缀；Baostock/Pytdx 对北交所代码显式切换数据源；避免误判上海 B 股 900xxx
- 🐛 **狙击点位解析错误** (#488, #532) — 理想买入/二次买入等字段在无「元」字时误提取括号内技术指标数字；现先截去第一个括号后内容再提取

### Added
- **Markdown-to-image for dashboard report** (#455, #535) — 个股日报汇总支持 markdown 转图片推送（Telegram、WeChat、Custom、Email），与大盘复盘行为一致
- **markdown-to-file engine** (#455) — `MD2IMG_ENGINE=markdown-to-file` 可选，对 emoji 支持更好，需 `npm i -g markdown-to-file`
- **PREFETCH_REALTIME_QUOTES** (#455) — 设为 `false` 可禁用实时行情预取，避免 efinance/akshare_em 全市场拉取
- **Stock name prefetch** (#455) — 分析前预取股票名称，减少报告中「股票xxxxx」占位符
- 📊 **分析报告模型标记** (#528, #534) — 在分析报告 meta、报告末尾、推送内容中展示 `model_used`（完整 LLM 模型名）；Agent 多轮调用时记录并展示每轮实际使用的模型（支持 fallback 切换）

### Changed
- **Enhanced markdown-to-image failure warning** (#455) — 转图失败时提示具体依赖（wkhtmltopdf 或 m2f）
- **WeChat-only image routing optimization** (#455) — 仅配置企业微信图片时，不再对完整报告做冗余转图，避免误导性失败日志
- **Stock name prefetch lightweight mode** (#455) — 名称预取阶段跳过 realtime quote 查询，减少额外网络开销

## [3.4.9] - 2026-03-06

### Added
- 🧠 **Structured config validation** — `ConfigIssue` dataclass and `validate_structured()` with severity-aware logging; `CONFIG_VALIDATE_MODE=strict` aborts startup on errors
- 🖼️ **Vision model config** — `VISION_MODEL` and `VISION_PROVIDER_PRIORITY` for image stock extraction; provider fallback (Gemini → Anthropic → OpenAI → DeepSeek) when primary fails
- 🚀 **CLI init wizard** — `python -m dsa init` 3-step interactive bootstrap (model → data source → notification), 9 provider presets, incremental merge by default
- 🔧 **Multi-channel LLM support** with visual channel editor (#494)

### Changed
- ♻️ **Vision extraction** — migrated from gemini-3 hardcode to `litellm.completion()` with configurable model and provider fallback; `OPENAI_VISION_MODEL` deprecated in favor of `VISION_MODEL`
- ♻️ **Market analyzer** — uses `Analyzer.generate_text()` for LLM calls; fixes bypass and Anthropic `AttributeError` when using non-Router path
- ♻️ **Config validation refinements** — test_env output format syncs with `validate_structured` (severity-aware ✓/✗/⚠/·); Vision key warning when `VISION_MODEL` set but no provider API key; market_analyzer test covers `generate_market_review` fallback when `generate_text` returns None
- ⚙️ **Auto-tag workflow defaults to NO tag** — only tags when commit message explicitly contains `#patch`, `#minor`, or `#major`
- ♻️ **Formatter and notification refactor** (#516)

### Fixed
- 🐛 **STOCK_LIST not refreshed on scheduled runs** — `.env` or WebUI changes to `STOCK_LIST` now hot-reload before each scheduled analysis (#529)
- 🐛 **WebUI fails to load with MIME type error** — SPA fallback route now resolves correct `Content-Type` for JS/CSS files (#520)
- 🐛 **AstrBot sender docstring misplaced** — `import time` placed before docstring in `_send_astrbot`, causing it to become dead code
- 🐛 **Telegram Markdown link escaping** — `_convert_to_telegram_markdown` escaped `[]()` characters, breaking all Markdown links in reports
- 🐛 **Duplicate `discord_bot_status` field** in Config dataclass — second declaration silently shadowed the first
- 🧹 **Unused imports** — removed `shutil`/`subprocess` from `main.py`
- 🔧 **Config validation and Vision key check** (#525)

### Docs
- 📝 Clarified GitHub Actions non-trading-day manual run controls (`TRADING_DAY_CHECK_ENABLED` + `force_run`) for Issue #461 / PR #466

## [3.4.8] - 2026-03-02

### Fixed
- 🐛 **Desktop exe crashes on startup with `FileNotFoundError`** — PyInstaller build was missing litellm's JSON data files (e.g. `model_prices_and_context_window_backup.json`). Added `--collect-data litellm` to both Windows and macOS build scripts so the files are correctly bundled in the executable.

### CI
- 🔧 Cache Electron binaries on macOS CI runners to prevent intermittent EOF download failures when fetching `electron-vX.Y.Z-darwin-*.zip` from GitHub CDN
- 🔧 Fix macOS DMG `hdiutil Resource busy` error during desktop packaging

### Docs
- 📝 Clarify non-trading-day manual run controls for GitHub Actions (`TRADING_DAY_CHECK_ENABLED` + `force_run`) (#474)

## [3.4.7] - 2026-02-28

### Added
- 🧠 **CN/US Market Strategy Blueprint System** (#395) — market review prompt injects region-specific strategy blueprints with position sizing and risk trigger recommendations

### Fixed
- 🐛 **`TRADING_DAY_CHECK_ENABLED` env var and `--force-run` for GitHub Actions** (#466)
- 🐛 **Agent pipeline preserved resolved stock names** (#464) — placeholder names no longer leak into reports
- 🐛 **Code cleanup** (#462, Fixes #422)
- 🐛 **WebUI auto-build on startup** (#460)
- 🐛 **ARCH_ARGS unbound variable** (#458)
- 🐛 **Time zone inconsistency & right panel flash** (#439)

### Docs
- 📝 Clarify potential ambiguities in code (#343)
- 📝 ENABLE_EASTMONEY_PATCH guidance for Issue #453 (#456)

## [3.4.0] - 2026-02-27

### Added
- 📡 **LiteLLM Direct Integration + Multi API Key Support** (#454, Fixes #421 #428)
  - Removed native SDKs (google-generativeai, google-genai, anthropic); unified through `litellm>=1.80.10`
  - New config: `LITELLM_MODEL`, `LITELLM_FALLBACK_MODELS`, `GEMINI_API_KEYS`, `ANTHROPIC_API_KEYS`, `OPENAI_API_KEYS`
  - Multi-key auto-builds LiteLLM Router (simple-shuffle) with 429 cooldown
  - **Breaking**: `.env` `GEMINI_MODEL` (no prefix) only for fallback; explicit config must include provider prefix

### Changed
- ♻️ **Notification Refactoring** (#435) — extracted 10 sender classes into `src/notification_sender/`

### Fixed
- 🐛 LLM NoneType crash, history API 422, sniper points extraction
- 🐛 Auto-build frontend on WebUI startup — `WEBUI_AUTO_BUILD` env var (default `true`)
- 🐛 Docker explicit project name (#448)
- 🐛 Bocha search SSL retry (#445, #446) — transient errors retry up to 3 times
- 🐛 Gemini google-genai SDK migration (Fixes #440, #444)
- 🐛 Mobile home page scrolling (Fixes #419, #433)
- 🐛 History list scroll reset (#431)
- 🐛 Settings save button false positive (fixes #417, #430)

## [3.3.22] - 2026-02-26

### Added
- 💬 **Chat History Persistence** (Fixes #400, #414) — `/chat` page survives refresh, sidebar session list
- 🎨 Project VI Assets — logo icon set, PSD, vector, banner (#425)
- 🚀 Desktop CI Auto-Release (#426) — Windows + macOS parallel builds

### Fixed
- 🐛 Agent Reasoning 400 & LiteLLM Proxy (fixes #409, #427)
- 🐛 Discord chunked sending (#413) — `DISCORD_MAX_WORDS` config
- 🐛 yfinance shared DataFrame (#412)
- 🐛 sniper_points parsing (#408)
- 🐛 Agent framework category missing (#406)
- 🐛 Date inconsistency & query id (fixes #322, #363)

## [3.3.12] - 2026-02-24

### Added
- 📈 **Intraday Realtime Technical Indicators** (Issue #234, #397) — MA calculated from realtime price, config: `ENABLE_REALTIME_TECHNICAL_INDICATORS`
- 🤖 **Agent Strategy Chat** (#367) — full ReAct pipeline, 11 YAML strategies, SSE streaming, multi-turn chat
- 📢 PushPlus Group Push — `PUSHPLUS_TOPIC` (#402)
- 📅 Trading Day Check (Issue #373, #375) — `TRADING_DAY_CHECK_ENABLED`, `--force-run`

### Fixed
- 🐛 DeepSeek reasoning mode (Issue #379, #386)
- 🐛 Agent news intel persistence (Fixes #396, #405)
- 🐛 Bare except clauses replaced with `except Exception` (#398)
- 🐛 UUID fallback for HTTP non-secure context (fixes #377, #381)
- 🐛 Docker DNS resolution (Fixes #372, #374)
- 🐛 Agent session/strategy bugs — multiple follow-up fixes for #367
- 🐛 yfinance parallel download data filtering

### Changed
- Market review strategy consistency — unified cn/us template
- Agent test assertions updated (`6 -> 11`)


## [3.2.11] - 2026-02-23

### 修复（#patch）
- 🐛 **StockTrendAnalyzer 从未执行** (Issue #357)
  - 根因：`get_analysis_context` 仅返回 2 天数据且无 `raw_data`，pipeline 中 `raw_data in context` 始终为 False
  - 修复：Step 3 直接调用 `get_data_range` 获取 90 日历天（约 60 交易日）历史数据用于趋势分析
  - 改善：趋势分析失败时用 `logger.warning(..., exc_info=True)` 记录完整 traceback

## [3.2.10] - 2026-02-22

### 新增
- ⚙️ 支持 `RUN_IMMEDIATELY` 配置项，设为 `true` 时定时任务触发后立即执行一次分析，无需等待首个定时点

### 修复
- 🐛 修复 Web UI 页面居中问题
- 🐛 修复 Settings 返回 500 错误

## [3.2.9] - 2026-02-22

### 修复
- 🐛 **ETF 分析仅关注指数走势**（Issue #274）
  - 美股/港股 ETF（如 VOO、QQQ）与 A 股 ETF 不再纳入基金公司层面风险（诉讼、声誉等）
  - 搜索维度：ETF/指数专用 risk_check、earnings、industry 查询，避免命中基金管理人新闻
  - AI 提示：指数型标的分析约束，`risk_alerts` 不得出现基金管理人公司经营风险

## [3.2.8] - 2026-02-21

### 修复
- 🐛 **BOT 与 WEB UI 股票代码大小写统一**（Issue #355）
  - BOT `/analyze` 与 WEB UI 触发分析的股票代码统一为大写（如 `aapl` → `AAPL`）
  - 新增 `canonical_stock_code()`，在 BOT、API、Config、CLI、task_queue 入口处规范化
  - 历史记录与任务去重逻辑可正确识别同一股票（大小写不再影响）

## [3.2.7] - 2026-02-20

### 新增
- 🔐 **Web 页面密码验证**（Issue #320, #349）
  - 支持 `ADMIN_AUTH_ENABLED=true` 启用 Web 登录保护
  - 首次访问在网页设置初始密码；支持「系统设置 > 修改密码」和 CLI `python -m src.auth reset_password` 重置

## [3.2.6] - 2026-02-20
### ⚠️ 破坏性变更（Breaking Changes）

- **历史记录 API 变更 (Issue #322)**
  - 路由变更：`GET /api/v1/history/{query_id}` → `GET /api/v1/history/{record_id}`
  - 参数变更：`query_id` (字符串) → `record_id` (整数)
  - 新闻接口变更：`GET /api/v1/history/{query_id}/news` → `GET /api/v1/history/{record_id}/news`
  - 原因：`query_id` 在批量分析时可能重复，无法唯一标识单条历史记录。改用数据库主键 `id` 确保唯一性
  - 影响范围：使用旧版历史详情 API 的所有客户端需同步更新

### 修复
- 修复美股（如 ADBE）技术指标矛盾：akshare 美股复权数据异常，统一美股历史数据源为 YFinance（Issue #311）
- 🐛 **历史记录查询和显示问题 (Issue #322)**
  - 修复历史记录列表查询中日期不一致问题：使用明天作为 endDate，确保包含今天全天的数据
  - 修复服务器 UI 报告选择问题：原因是多条记录共享同一 `query_id`，导致总是显示第一条。现改用 `analysis_history.id` 作为唯一标识
  - 历史详情、新闻接口及前端组件已全面适配 `record_id`
  - 新增后台轮询（每 30s）与页面可见性变更时静默刷新历史列表，确保 CLI 发起的分析完成后前端能及时同步，使用 `silent` 模式避免触发 loading 状态
- 🐛 **美股指数实时行情与日线数据** (Issue #273)
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 ^GSPC）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源
  - 消除重复的美股识别逻辑，统一使用 `is_us_stock_code()` 函数

### 优化
- 🎨 **首页输入栏与 Market Sentiment 布局对齐优化**
  - 股票代码输入框左缘与历史记录 glass-card 框左对齐
  - 分析按钮右缘与 Market Sentiment 外框右对齐
  - Market Sentiment 卡片向下拉伸填满格子，消除与 STRATEGY POINTS 之间的空隙
  - 窄屏时输入栏填满宽度，响应式对齐保持一致

## [3.2.5] - 2026-02-19

### 新增
- 🌍 **大盘复盘可选区域**（Issue #299）
  - 支持 `MARKET_REVIEW_REGION` 环境变量：`cn`（A股）、`us`（美股）、`both`（两者）
  - us 模式使用 SPX/纳斯达克/道指/VIX 等指数；both 模式可同时复盘 A 股与美股
  - 默认 `cn`，保持向后兼容

## [3.2.4] - 2026-02-18

### 修复
- 🐛 **统一美股数据源为 YFinance**（Issue #311）
  - akshare 美股复权数据异常，统一美股历史数据源为 YFinance
  - 修复 ADBE 等美股股票技术指标矛盾问题

## [3.2.3] - 2026-02-18

### 修复
- 🐛 **标普500实时数据缺失**（Issue #273）
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 `^GSPC`）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源

## [3.2.2] - 2026-02-16

### 新增
- 📊 **PE 指标支持**（Issue #296）
  - AI System Prompt 增加 PE 估值关注
- 📰 **新闻时效性筛查**（Issue #296）
  - `NEWS_MAX_AGE_DAYS`：新闻最大时效（天），默认 3，避免使用过时信息
- 📈 **强势趋势股乖离率放宽**（Issue #296）
  - `BIAS_THRESHOLD`：乖离率阈值（%），默认 5.0，可配置
  - 强势趋势股（多头排列且趋势强度 ≥70）自动放宽乖离率到 1.5 倍

## [3.2.1] - 2026-02-16

### 新增
- 🔧 **东财接口补丁可配置开关**
  - 支持 `EFINANCE_PATCH_ENABLED` 环境变量开关东财接口补丁（默认 `true`）
  - 补丁不可用时可降级关闭，避免影响主流程

## [3.2.0] - 2026-02-15

### 新增
- 🔒 **CI 门禁统一（P0）**
  - 新增 `scripts/ci_gate.sh` 作为后端门禁单一入口
  - 主 CI 改为 `backend-gate`、`docker-build`、`web-gate` 三段式
  - CI 触发改为所有 PR，避免 Required Checks 因路径过滤缺失而卡住合并
  - `web-gate` 支持前端路径变更按需触发
  - 新增 `network-smoke` 工作流承载非阻断网络场景回归
- 📦 **发布链路收敛（P0）**
  - `docker-publish` 调整为 tag 主触发，并增加发布前门禁校验
  - 手动发布增加 `release_tag` 输入与 semver/changelog 强校验
  - 发布前新增 Docker smoke（关键模块导入）
- 📝 **PR 模板升级（P0）**
  - 增加背景、范围、验证命令与结果、回滚方案、Issue 关联等必填项
- 🤖 **AI 审查覆盖增强（P0）**
  - `pr-review` 纳入 `.github/workflows/**` 范围
  - 新增 `AI_REVIEW_STRICT` 开关，可选将 AI 审查失败升级为阻断

## [3.1.13] - 2026-02-15

### 新增
- 📊 **仅分析结果摘要**（Issue #262）
  - 支持 `REPORT_SUMMARY_ONLY` 环境变量，设为 `true` 时只推送汇总，不含个股详情
  - 默认 `false`，多股时适合快速浏览

## [3.1.12] - 2026-02-15

### 新增
- 📧 **个股与大盘复盘合并推送**（Issue #190）
  - 支持 `MERGE_EMAIL_NOTIFICATION` 环境变量，设为 `true` 时将个股分析与大盘复盘合并为一次推送
  - 默认 `false`，减少邮件数量、降低被识别为垃圾邮件的风险

## [3.1.11] - 2026-02-15

### 新增
- 🤖 **Anthropic Claude API 支持**（Issue #257）
  - 支持 `ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`、`ANTHROPIC_TEMPERATURE`、`ANTHROPIC_MAX_TOKENS`
  - AI 分析优先级：Gemini > Anthropic > OpenAI
- 📷 **从图片识别股票代码**（Issue #257）
  - 上传自选股截图，通过 Vision LLM 自动提取股票代码
  - API: `POST /api/v1/stocks/extract-from-image`；支持 JPEG/PNG/WebP/GIF，最大 5MB
  - 支持 `OPENAI_VISION_MODEL` 单独配置图片识别模型
- ⚙️ **通达信数据源手动配置**（Issue #257）
  - 支持 `PYTDX_HOST`、`PYTDX_PORT` 或 `PYTDX_SERVERS` 配置自建通达信服务器

## [3.1.10] - 2026-02-15

### 新增
- ⚙️ **立即运行配置**（Issue #332）
  - 支持 `RUN_IMMEDIATELY` 环境变量，`true` 时定时任务启动后立即执行一次
- 🐛 修复 Docker 构建问题

## [3.1.9] - 2026-02-14

### 新增
- 🔌 **东财接口补丁机制**
  - 新增 `patch/eastmoney_patch.py` 修复 efinance 上游接口变更
  - 不影响其他数据源的正常运行

## [3.1.8] - 2026-02-14

### 新增
- 🔐 **Webhook 证书校验开关**（Issue #265）
  - 支持 `WEBHOOK_VERIFY_SSL` 环境变量，可关闭 HTTPS 证书校验以支持自签名证书
  - 默认保持校验，关闭存在 MITM 风险，仅建议在可信内网使用

## [3.1.7] - 2026-02-14

### 修复
- 🐛 修复包导入错误（package import error）

## [3.1.6] - 2026-02-13

### 修复
- 🐛 修复 `news_intel` 中 `query_id` 不一致问题

## [3.1.5] - 2026-02-13

### 新增
- 📷 **Markdown 转图片通知**（Issue #289）
  - 支持 `MARKDOWN_TO_IMAGE_CHANNELS` 配置，对 Telegram、企业微信、自定义 Webhook（Discord）、邮件发送图片格式报告
  - 邮件为内联附件，增强对不支持 HTML 客户端的兼容性
  - 需安装 `wkhtmltopdf` 和 `imgkit`

## [3.1.4] - 2026-02-12

### 新增
- 📧 **股票分组发往不同邮箱**（Issue #268）
  - 支持 `STOCK_GROUP_N` + `EMAIL_GROUP_N` 配置，不同股票组报告发送到对应邮箱
  - 大盘复盘发往所有配置的邮箱

## [3.1.3] - 2026-02-12

### 修复
- 🐛 修复 Docker 内运行时通过页面修改配置报错 `[Errno 16] Device or resource busy` 的问题

## [3.1.2] - 2026-02-11

### 修复
- 🐛 修复 Docker 一致性问题，解决关键批次处理与通知 Bug

## [3.1.1] - 2026-02-11

### 变更
- ♻️ `API_HOST` → `WEBUI_HOST`：Docker Compose 配置项统一

## [3.1.0] - 2026-02-11

### 新增
- 📊 **ETF 支持增强与代码规范化**
  - 统一各数据源 ETF 代码处理逻辑
  - 新增 `canonical_stock_code()` 统一代码格式，确保数据源路由正确

## [3.0.5] - 2026-02-08

### 修复
- 🐛 修复信号 emoji 与建议不一致的问题（复合建议如"卖出/观望"未正确映射）
- 🐛 修复 `*ST` 股票名在微信/Dashboard 中 markdown 转义问题
- 🐛 修复 `idx.amount` 为 None 时大盘复盘 TypeError
- 🐛 修复分析 API 返回 `report=None` 及 ReportStrategy 类型不一致问题
- 🐛 修复 Tushare 返回类型错误（dict → UnifiedRealtimeQuote）及 API 端点指向

### 新增
- 📊 大盘复盘报告注入结构化数据（涨跌统计、指数表格、板块排名）
- 🔍 搜索结果 TTL 缓存（500 条上限，FIFO 淘汰）
- 🔧 Tushare Token 存在时自动注入实时行情优先级
- 📰 新闻摘要截断长度 50→200 字

### 优化
- ⚡ 补充行情字段请求限制为最多 1 次，减少无效请求

## [3.0.4] - 2026-02-07

### 新增
- 📈 **回测引擎** (PR #269)
  - 新增基于历史分析记录的回测系统，支持收益率、胜率、最大回撤等指标评估
  - WebUI 集成回测结果展示

## [3.0.3] - 2026-02-07

### 修复
- 🐛 修复狙击点位数据解析错误问题 (PR #271)

## [3.0.2] - 2026-02-06

### 新增
- ✉️ 可配置邮件发送者名称 (PR #272)
- 🌐 外国股票支持英文关键词搜索

## [3.0.1] - 2026-02-06

### 修复
- 🐛 修复 ETF 实时行情获取、市场数据回退、企业微信消息分块问题
- 🔧 CI 流程简化

## [3.0.0] - 2026-02-06

### 移除
- 🗑️ **移除旧版 WebUI**
  - 删除基于 `http.server.ThreadingHTTPServer` 的旧版 WebUI（`web/` 包）
  - 旧版 WebUI 的功能已完全被 FastAPI（`api/`）+ React 前端替代
  - `--webui` / `--webui-only` 命令行参数标记为弃用，自动重定向到 `--serve` / `--serve-only`
  - `WEBUI_ENABLED` / `WEBUI_HOST` / `WEBUI_PORT` 环境变量保持兼容，自动转发到 FastAPI 服务
  - `webui.py` 保留为兼容入口，启动时直接调用 FastAPI 后端
  - Docker Compose 中移除 `webui` 服务定义，统一使用 `server` 服务

### 变更
- ♻️ **服务层重构**
  - 将 `web/services.py` 中的异步任务服务迁移至 `src/services/task_service.py`
  - Bot 分析命令（`bot/commands/analyze.py`）改为使用 `src.services.task_service`
  - Docker 环境变量 `WEBUI_HOST`/`WEBUI_PORT` 更名为 `API_HOST`/`API_PORT`（旧名仍兼容）

## [2.3.0] - 2026-02-01

### 新增
- 🇺🇸 **增强美股支持** (Issue #153)
  - 实现基于 Akshare 的美股历史数据获取 (`ak.stock_us_daily()`)
  - 实现基于 Yfinance 的美股实时行情获取（优先策略）
  - 增加对不支持数据源（Tushare/Baostock/Pytdx/Efinance）的美股代码过滤和快速降级

### 修复
- 🐛 修复 AMD 等美股代码被误识别为 A 股的问题 (Issue #153)

## [2.2.5] - 2026-02-01

### 新增
- 🤖 **AstrBot 消息推送** (PR #217)
  - 新增 AstrBot 通知渠道，支持推送到 QQ 和微信
  - 支持 HMAC SHA256 签名验证，确保通信安全
  - 通过 `ASTRBOT_URL` 和 `ASTRBOT_TOKEN` 配置

## [2.2.4] - 2026-02-01

### 新增
- ⚙️ **可配置数据源优先级** (PR #215)
  - 支持通过环境变量（如 `YFINANCE_PRIORITY=0`）动态调整数据源优先级
  - 无需修改代码即可优先使用特定数据源（如 Yahoo Finance）

## [2.2.3] - 2026-01-31

### 修复
- 📦 更新 requirements.txt，增加 `lxml_html_clean` 依赖以解决兼容性问题

## [2.2.2] - 2026-01-31

### 修复
- 🐛 修复代理配置区分大小写问题 (fixes #211)

## [2.2.1] - 2026-01-31

### 修复
- 🐛 **YFinance 兼容性修复** (PR #210, fixes #209)
  - 修复新版 yfinance 返回 MultiIndex 列名导致的数据解析错误

## [2.2.0] - 2026-01-31

### 新增
- 🔄 **多源回退策略增强**
  - 实现了更健壮的数据获取回退机制 (feat: multi-source fallback strategy)
  - 优化了数据源故障时的自动切换逻辑

### 修复
- 🐛 修复 analyzer 运行后无法通过改 .env 文件的 stock_list 内容调整跟踪的股票

## [2.1.14] - 2026-01-31

### 文档
- 📝 更新 README 和优化 auto-tag 规则

## [2.1.13] - 2026-01-31

### 修复
- 🐛 **Tushare 优先级与实时行情** (Fixed #185)
  - 修复 Tushare 数据源优先级设置问题
  - 修复 Tushare 实时行情获取功能

## [2.1.12] - 2026-01-30

### 修复
- 🌐 修复代理配置在某些情况下的区分大小写问题
- 🌐 修复本地环境禁用代理的逻辑

## [2.1.11] - 2026-01-30

### 优化
- 🚀 **飞书消息流优化** (PR #192)
  - 优化飞书 Stream 模式的消息类型处理
  - 修改 Stream 消息模式默认为关闭，防止配置错误运行时报错

## [2.1.10] - 2026-01-30

### 合并
- 📦 合并 PR #154 贡献

## [2.1.9] - 2026-01-30

### 新增
- 💬 **微信文本消息支持** (PR #137)
  - 新增微信推送的纯文本消息类型支持
  - 添加 `WECHAT_MSG_TYPE` 配置项

## [2.1.8] - 2026-01-30

### 修复
- 🐛 修正日志中 API 提供商显示错误 (PR #197)

## [2.1.7] - 2026-01-30

### 修复
- 🌐 禁用本地环境的代理设置，避免网络连接问题

## [2.1.6] - 2026-01-29

### 新增
- 📡 **Pytdx 数据源 (Priority 2)**
  - 新增通达信数据源，免费无需注册
  - 多服务器自动切换
  - 支持实时行情和历史数据
- 🏷️ **多源股票名称解析**
  - DataFetcherManager 新增 `get_stock_name()` 方法
  - 新增 `batch_get_stock_names()` 批量查询
  - 自动在多数据源间回退
  - Tushare 和 Baostock 新增股票名称/列表方法
- 🔍 **增强搜索回退**
  - 新增 `search_stock_price_fallback()` 用于数据源全部失败时
  - 新增搜索维度：市场分析、行业分析
  - 最大搜索次数从 3 增加到 5
  - 改进搜索结果格式（每维度 4 条结果）

### 改进
- 更新搜索查询模板以提高相关性
- 增强 `format_intel_report()` 输出结构

## [2.1.5] - 2026-01-29

### 新增
- 📡 新增 Pytdx 数据源和多源股票名称解析功能

## [2.1.4] - 2026-01-29

### 文档
- 📝 更新赞助商信息

## [2.1.3] - 2026-01-28

### 文档
- 📝 重构 README 布局
- 🌐 新增繁体中文翻译 (README_CHT.md)

### 修复
- 🐛 修复 WebUI 无法输入美股代码问题
  - 输入框逻辑改成所有字母都转换成大写
  - 支持 `.` 的输入（如 `BRK.B`）

## [2.1.2] - 2026-01-27

### 修复
- 🐛 修复个股分析推送失败和报告路径问题 (fixes #166)
- 🐛 修改 CR 错误，确保微信消息最大字节配置生效

## [2.1.1] - 2026-01-26

### 新增
- 🔧 添加 GitHub Actions auto-tag 工作流
- 📡 添加 yfinance 兜底数据源及数据缺失警告

### 修复
- 🐳 修复 docker-compose 路径和文档命令
- 🐳 Dockerfile 补充 copy src 文件夹 (fixes #145)

## [2.1.0] - 2026-01-25

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 📈 **MACD 和 RSI 技术指标**
  - MACD：趋势确认、金叉死叉信号（零轴上金叉⭐、金叉✅、死叉❌）
  - RSI：超买超卖判断（超卖⭐、强势✅、超买⚠️）
  - 指标信号纳入综合评分系统
- 🎮 **Discord 推送支持** (PR #124, #125, #144)
  - 支持 Discord Webhook 和 Bot API 两种方式
  - 通过 `DISCORD_WEBHOOK_URL` 或 `DISCORD_BOT_TOKEN` + `DISCORD_MAIN_CHANNEL_ID` 配置
- 🤖 **机器人命令交互**
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
- 🌡️ **AI 温度参数可配置** (PR #142)
  - 支持自定义 AI 模型温度参数
- 🐳 **Zeabur 部署支持**
  - 添加 Zeabur 镜像部署工作流
  - 支持 commit hash 和 latest 双标签

### 重构
- 🏗️ **项目结构优化**
  - 核心代码移至 `src/` 目录，根目录更清爽
  - 文档移至 `docs/` 目录
  - Docker 配置移至 `docker/` 目录
  - 修复所有 import 路径，保持向后兼容
- 🔄 **数据源架构升级**
  - 新增数据源熔断机制，单数据源连续失败自动切换
  - 实时行情缓存优化，批量预取减少 API 调用
  - 网络代理智能分流，国内接口自动直连
- 🤖 Discord 机器人重构为平台适配器架构

### 修复
- 🌐 **网络稳定性增强**
  - 自动检测代理配置，对国内行情接口强制直连
  - 修复 EfinanceFetcher 偶发的 `ProtocolError`
  - 增加对底层网络错误的捕获和重试机制
- 📧 **邮件渲染优化**
  - 修复邮件中表格不渲染问题 (#134)
  - 优化邮件排版，更紧凑美观
- 📢 **企业微信推送修复**
  - 修复大盘复盘推送不完整问题
  - 增强消息分割逻辑，支持更多标题格式
  - 增加分批发送间隔，避免限流丢失
- 👷 **CI/CD 修复**
  - 修复 GitHub Actions 中路径引用的错误

## [2.0.0] - 2026-01-24

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 🤖 **机器人命令交互** (PR #113)
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
  - 支持选择精简报告或完整报告
- 🎮 **Discord 推送支持** (PR #124)
  - 支持 Discord Webhook 推送
  - 添加 Discord 环境变量到工作流

### 修复
- 🐳 修复 WebUI 在 Docker 中绑定 0.0.0.0 (fixed #118)
- 🔔 修复飞书长连接通知问题
- 🐛 修复 `analysis_delay` 未定义错误
- 🔧 启动时 config.py 检测通知渠道，修复已配置自定义渠道情况下仍然提示未配置问题

### 改进
- 🔧 优化 Tushare 优先级判断逻辑，提升封装性
- 🔧 修复 Tushare 优先级提升后仍排在 Efinance 之后的问题
- ⚙️ 配置 TUSHARE_TOKEN 时自动提升 Tushare 数据源优先级
- ⚙️ 实现 4 个用户反馈 issue (#112, #128, #38, #119)

## [1.6.0] - 2026-01-19

### 新增
- 🖥️ WebUI 管理界面及 API 支持（PR #72）
  - 全新 Web 架构：分层设计（Server/Router/Handler/Service）
  - 核心 API：支持 `/analysis` (触发分析), `/tasks` (查询进度), `/health` (健康检查)
  - 交互界面：支持页面直接输入代码并触发分析，实时展示进度
  - 运行模式：新增 `--webui-only` 模式，仅启动 Web 服务
  - 解决了 [#70](https://github.com/ZhuLinsen/daily_stock_analysis/issues/70) 的核心需求（提供触发分析的接口）
- ⚙️ GitHub Actions 配置灵活性增强（[#79](https://github.com/ZhuLinsen/daily_stock_analysis/issues/79)）
  - 支持从 Repository Variables 读取非敏感配置（如 STOCK_LIST, GEMINI_MODEL）
  - 保持对 Secrets 的向下兼容

### 修复
- 🐛 修复企业微信/飞书报告截断问题（[#73](https://github.com/ZhuLinsen/daily_stock_analysis/issues/73)）
  - 移除 notification.py 中不必要的长度硬截断逻辑
  - 依赖底层自动分片机制处理长消息
- 🐛 修复 GitHub Workflow 环境变量缺失（[#80](https://github.com/ZhuLinsen/daily_stock_analysis/issues/80)）
  - 修复 `CUSTOM_WEBHOOK_BEARER_TOKEN` 未正确传递到 Runner 的问题

## [1.5.0] - 2026-01-17

### 新增
- 📲 单股推送模式（[#55](https://github.com/ZhuLinsen/daily_stock_analysis/issues/55)）
  - 每分析完一只股票立即推送，不用等全部分析完
  - 命令行参数：`--single-notify`
  - 环境变量：`SINGLE_STOCK_NOTIFY=true`
- 🔐 自定义 Webhook Bearer Token 认证（[#51](https://github.com/ZhuLinsen/daily_stock_analysis/issues/51)）
  - 支持需要 Token 认证的 Webhook 端点
  - 环境变量：`CUSTOM_WEBHOOK_BEARER_TOKEN`

## [1.4.0] - 2026-01-17

### 新增
- 📱 Pushover 推送支持（PR #26）
  - 支持 iOS/Android 跨平台推送
  - 通过 `PUSHOVER_USER_KEY` 和 `PUSHOVER_API_TOKEN` 配置
- 🔍 博查搜索 API 集成（PR #27）
  - 中文搜索优化，支持 AI 摘要
  - 通过 `BOCHA_API_KEYS` 配置
- 📊 Efinance 数据源支持（PR #59）
  - 新增 efinance 作为数据源选项
- 🇭🇰 港股支持（PR #17）
  - 支持 5 位代码或 HK 前缀（如 `hk00700`、`hk1810`）

### 修复
- 🔧 飞书 Markdown 渲染优化（PR #34）
  - 使用交互卡片和格式化器修复渲染问题
- ♻️ 股票列表热重载（PR #42 修复）
  - 分析前自动重载 `STOCK_LIST` 配置
- 🐛 钉钉 Webhook 20KB 限制处理
  - 长消息自动分块发送，避免被截断
- 🔄 AkShare API 重试机制增强
  - 添加失败缓存，避免重复请求失败接口

### 改进
- 📝 README 精简优化
  - 高级配置移至 `docs/full-guide.md`


## [1.3.0] - 2026-01-12

### 新增
- 🔗 自定义 Webhook 支持
  - 支持任意 POST JSON 的 Webhook 端点
  - 自动识别钉钉、Discord、Slack、Bark 等常见服务格式
  - 支持配置多个 Webhook（逗号分隔）
  - 通过 `CUSTOM_WEBHOOK_URLS` 环境变量配置

### 修复
- 📝 企业微信长消息分批发送
  - 解决自选股过多时内容超过 4096 字符限制导致推送失败的问题
  - 智能按股票分析块分割，每批添加分页标记（如 1/3, 2/3）
  - 批次间隔 1 秒，避免触发频率限制

## [1.2.0] - 2026-01-11

### 新增
- 📢 多渠道推送支持
  - 企业微信 Webhook
  - 飞书 Webhook（新增）
  - 邮件 SMTP（新增）
  - 自动识别渠道类型，配置更简单

### 改进
- 统一使用 `NOTIFICATION_URL` 配置，兼容旧的 `WECHAT_WEBHOOK_URL`
- 邮件支持 Markdown 转 HTML 渲染

## [1.1.0] - 2026-01-11

### 新增
- 🤖 OpenAI 兼容 API 支持
  - 支持 DeepSeek、通义千问、Moonshot、智谱 GLM 等
  - Gemini 和 OpenAI 格式二选一
  - 自动降级重试机制

## [1.0.0] - 2026-01-10

### 新增
- 🎯 AI 决策仪表盘分析
  - 一句话核心结论
  - 精确买入/止损/目标点位
  - 检查清单（✅⚠️❌）
  - 分持仓建议（空仓者 vs 持仓者）
- 📊 大盘复盘功能
  - 主要指数行情
  - 涨跌统计
  - 板块涨跌榜
  - AI 生成复盘报告
- 🔍 多数据源支持
  - AkShare（主数据源，免费）
  - Tushare Pro
  - Baostock
  - YFinance
- 📰 新闻搜索服务
  - Tavily API
  - SerpAPI
- 💬 企业微信机器人推送
- ⏰ 定时任务调度
- 🐳 Docker 部署支持
- 🚀 GitHub Actions 零成本部署

### 技术特性
- Gemini AI 模型（gemini-3-flash-preview）
- 429 限流自动重试 + 模型切换
- 请求间延时防封禁
- 多 API Key 负载均衡
- SQLite 本地数据存储

---

[Unreleased]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.31.0...HEAD
[3.31.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.30.0...v3.31.0
[3.30.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.29.0...v3.30.0
[3.29.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.28.0...v3.29.0
[3.28.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.27.0...v3.28.0
[3.27.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.26.1...v3.27.0
[3.26.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.25.0...v3.26.1
[3.25.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.24.1...v3.25.0
[3.24.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.24.0...v3.24.1
[3.24.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.23.0...v3.24.0
[3.23.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.22.0...v3.23.0
[3.22.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.21.1...v3.22.0
[3.21.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.21.0...v3.21.1
[3.21.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.20.0...v3.21.0
[3.20.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.19.0...v3.20.0
[3.19.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.18.0...v3.19.0
[3.18.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.17.1...v3.18.0
[3.17.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.17.0...v3.17.1
[3.17.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.16.0...v3.17.0
[3.16.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.15.0...v3.16.0
[3.15.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.2...v3.15.0
[3.14.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.1...v3.14.2
[3.14.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.0...v3.14.1
[3.14.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.13.0...v3.14.0
[3.13.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.12.0...v3.13.0
[3.12.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.11.0...v3.12.0
[3.11.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.1...v3.11.0
[3.10.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.9.0...v3.10.0
[3.9.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.7.0...v3.8.0
[3.7.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.10...v3.5.0
[3.4.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.9...v3.4.10
[3.4.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.8...v3.4.9
[3.4.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.7...v3.4.8
[3.4.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.0...v3.4.7
[3.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.22...v3.4.0
[3.3.22]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.12...v3.3.22
[3.3.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.11...v3.3.12
[3.2.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.10...v3.2.11
[2.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.5...v2.3.0
[2.2.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.4...v2.2.5
[2.2.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.3...v2.2.4
[2.2.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.2...v2.2.3
[2.2.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.14...v2.2.0
[2.1.14]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.13...v2.1.14
[2.1.13]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.12...v2.1.13
[2.1.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.11...v2.1.12
[2.1.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.10...v2.1.11
[2.1.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.9...v2.1.10
[2.1.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.8...v2.1.9
[2.1.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.7...v2.1.8
[2.1.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.6...v2.1.7
[2.1.6]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.5...v2.1.6
[2.1.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.4...v2.1.5
[2.1.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.3...v2.1.4
[2.1.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.2...v2.1.3
[2.1.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.1...v2.1.2
[2.1.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.6.0...v2.0.0
[1.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v1.0.0

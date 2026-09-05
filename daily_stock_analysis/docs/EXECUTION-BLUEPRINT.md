# 半自动交易助手执行蓝图（GLM-5.3F 执行指导）

> 版本：v1.0（2026-09-05 定稿）
> 适用执行者：GLM-5.3F（或同等能力的长上下文代码 agent）
> 上游依据：全仓盘点（68 服务引用计数 / 巨文件解剖 / 六对重叠判定 / 上游远端核实）
> 效力：本文件是执行顺序与验收标准的**唯一真源**；与 ROADMAP-V2.md（quant 侧完成记录）、HANDOFF.md 冲突时，以本文件为准。
> 纪律继承：quant 侧的预注册纪律、holdout 隔离、诚实负结果原则全量适用于第 ⑤ 阶段；AGENTS.md 的硬规则（不擅自 commit/push、CHANGELOG 同步、最小改动）全量适用。

---

## 〇、终局形态（所有工作的靶心）

**一个住在你聊天软件里的私人交易助手**：

```
09:00  盘前推送【今日操作单】：具体限价数字 + 放弃条件 + 不动的理由
       用户在券商 App 手动下单（5 分钟），回消息回填成交
10:30  /持仓 → 实时持仓 + 浮盈 + 模拟账本 vs 真实成交对比
15:30  盘后推送【日报】：一条汇总（组合表现/信号命中/系统健康）
随时   /操作单 /统计 /状态 查询；Web 看月度对比曲线与健康页
```

四条铁律，所有阶段决策以此裁决：

1. **消息即界面**：聊天管每天的双向交互；Web 只管月度回看（对比/统计/健康三页）。不做新仪表盘。
2. **人是执行引擎**：系统输出"当前持仓→目标持仓的 diff + 具体限价"，绝不自动下单。
3. **具体数字**：操作单的价格 = 一个限价数字 + 两条边界规则（开盘超 X% 放弃 / 14:50 未成交撤单），禁止"价格区间"。
4. **诚实分级**：转债栏 = 系统信号（可回测可后验）；股票栏 = LLM 研究观点（明确标注"观点非信号"，不给操作价位只给观察位）。

---

## 一、现状事实基线（执行前必读，防止重造轮子）

盘点确认的**五块已存在拼图**（接线而非新写）：

| 拼图 | 位置 | 现状 |
| --- | --- | --- |
| 持仓回填链 | `bot/commands/portfolio.py` + `src/services/portfolio_analysis_context.py` | ✅ 已接线（P0-A，commit 64f8c539） |
| 三层仓位管理 | `src/services/position_sizer.py`（430 行） | ⚠️ **完整实现、零接线**（config 有 3 个专属配置项喂空气） |
| 成交假设（费率/滑点/T+1） | `quant_research/quant_research/trade/broker.py` PaperBroker | ✅ 可直接移植；QR 内部 `backtest/broker.py` 另有一份 AShareCommission 重复 |
| 整数手分配 | `quant_research/quant_research/trade/sizer.py`（139 行背包算法） | ✅ 拿来即用 |
| 信号生命周期与后验 | `src/services/decision_signal_*.py` 7 件套（**五态**状态机，entry_low/entry_high/stop_loss/target_price/invalidation 字段齐全） | ✅ 成熟，超过多数开源同类 |
| 监控推送 | event_arbitrage / bond_new_issue / etf_premium 三监控 | ✅ 运行中 |

已确认的**结构病灶**（第 ④ 阶段处理）：

- 产品面过宽而非死代码（全量引用计数，零僵尸服务）。
- 削减目标合计约 3.5 万行：配置三件套 14,755 行 / Agent 例行模式 ~11,000 行 / screening ~7,000 行 / run_diagnostics+run_flow ~3,000 行 / 桌面端+15 渠道+三语（自用各留一条）。
- 孤儿三件：`position_sizer`（待接线）、`bot/platforms/discord.py`（实现+测试齐全但未注册，用户用飞书→冻结）、`src/agent/web_intent_*.py`（1,192 行半孤儿，resolver 从未落地→保留待第 ⑤ 阶段复用）。
- ~~确认废弃~~（✅ 2026-09-06 已执行）：`quant_research/trade/broker.py` 的 TDXJKBroker、`trade/pipeline.py` LiveMWU、`scripts/run_live.py` 已移除（archive/ 无跟踪文件，跳过）；回溯见 tag pre-restructure-v1。

上游关系：ZhuLinsen/daily_stock_analysis 活跃（v3.31.0 后仍每周合 PR），方向为 LLM 分析深化，与本终局重叠度低。**先砍后接会让每次上游合并变成冲突地狱**——这是"布线在前、砍除在后"排序的根本原因。

---

## 二、执行阶段总览（顺序不可调换）

```
① 布线（2-3 周）── 操作单闭环跑起来，每天可用
② 数据地基（3-4 天）── 全市场快照库 + 规则漏斗
③ 账本（1 周）── 模拟 vs 真实两本账 + Web 三页
④ 结构性砍除（1 周）── 在上游版本合并点执行
⑤ 慢线（数周，独立节奏）── 股票系统化策略预注册研究
（并行挂钟：quant C2 节奏——每月 PIT 增量 / 季末三表 / 2027Q2 C1 判定）
```

每个阶段开工前：读本文件对应小节 → 确认上一阶段验收全过 → 用 `.scratch/dsa-<feature>/`（或 `quant-` 前缀）建任务目录。每完成一个子项：CHANGELOG `[Unreleased]` 加一行（扁平格式）→ 全量相关测试 → 按仓库惯例提交（英文 message，无 Co-Authored-By）。

---

## 三、阶段 ①：布线——操作单闭环（最优先）

### ①-A 接线 position_sizer（1 天）

- 目标：`build_position_sizer_from_config()` 被 `src/services/portfolio_analysis_context.py` 或新组件调用，输入 = PortfolioService 快照，输出 = 各标的可配目标权重上限。
- 验收：单测覆盖"风险预算→单票上限→总敞口 95%"三层约束在典型持仓（5 转债 + 3 股票，10k 本金）下的输出；`POSITION_SIZER_*` 三个配置项从"喂空气"变为真实读取。

### ①-B 操作单生成器（2-3 天）——本阶段核心

新建 `src/services/order_sheet_service.py`：

- 输入：当前持仓快照（PortfolioService）+ 目标持仓（quant 转债信号经 ①-A 仓位约束后的权重）+ T-1 收盘价。
- 输出（单条操作，人可照抄执行）：
  ```
  买入 128093.SZ @116.50 限价 · 10张 · 开盘>118.50 放弃 · 14:50未成交撤单
  卖出 113062.SH @128.00 限价 · 10张 · 开盘<126.00 放弃
  持有 118031.SH · 跌出队列但仍在 buffer 内，不换出
  本单无操作（候选不足，沿用持仓）—— 理由一行
  ```
- 价格规则：**限价 = T-1 收盘价**（与回测成交假设一致，保证模拟账本与真实成交可比）；转债 10 张/手、股票 100 股/手（复用 QR sizer 算手数）。
- 每条操作带 `sheet_id` 落库（新表或复用决策信号表），字段含标的/方向/手数/限价/放弃条件/状态（pending→filled/cancelled/expired）。
- 验收：`/操作单` bot 命令输出当日操作单；三档输出齐全（有操作/持有不动/无操作带理由）各有测试；空仓/无信号场景不崩。

### ①-C 转债信号桥（1-2 天）

- 目标：`quant_research/scripts/run_paper_tracking.py` 的选债计划（compute_cb_target_weights）以**子进程或独立脚本产物 JSON** 的方式供 dsa 侧读取——不引入跨项目 import（两项目独立运行的边界见 domain.md）。
- 契约：`output/paper_tracking/current_plan.json`（符号/权重/调仓日/PIT末端）；dsa 侧只读该文件。
- 验收：dsa 侧读取后能生成操作单；quant 侧跑一次 paper tracking 该文件自动更新；文件缺失时操作单降级为"信号源不可用"。

### ①-D 消息总线 + 每日摘要（2-3 天）

- 目标：八个推送源（个股日报/单股推送/大盘复盘/飞书文档链接/事件套利/转债打新/ETF 溢价/AlertWorker）写入当日消息总线（DB 表：时间/来源/级别/持仓相关?/正文摘要），每日 18:00 聚合为**一条**分级摘要（持仓相关 > 重要事件 > 其他）。
- 治轰炸优先级排序 + 降噪默认值就位（dedup TTL / cooldown / quiet-hours 合理默认，落库持久化而非进程内存）。
- `NOTIFICATION_DAILY_DIGEST_ENABLED` 从空壳变为真实实现。
- 验收：模拟 8 个源各发 3 条 → 用户只收 1 条摘要；持仓相关条目置顶；静默时段生效；重启后去重状态不丢。

### ①-E 运行可见性（1-2 天）

- `/状态` 命令：数据新鲜度（PIT/快照到几号）、上次全市场扫描时间、活跃信号数、今日推送计数、各监控服务健康（30 秒可读）。
- Web 健康页（复用 dsa-web 加一页）：各后台服务运行时长/错误计数/冷却状态。
- 日报固定带一行"系统健康摘要"。
- 验收：`/状态` 输出上述五要素；任一监控注入故障后健康页可见。

### ① 阶段验收（全过才进 ②）

- [ ] 每天收到：盘前操作单（具体限价）+ 盘后一条摘要，共 2 条推送
- [ ] `/操作单` `/持仓` `/状态` 可用；回填一条成交后 PortfolioService 与操作单状态联动
- [ ] position_sizer 有真实调用方；三档操作输出有测试
- [ ] 后端全量测试 0 失败（6685+ 基线只增不减）；web lint+build 过

---

## 四、阶段 ②：数据地基——全市场快照与规则漏斗

### ②-A 每日截面快照落库（1 天）

- `stock_zh_a_spot_em()`（data_provider 已有通道）每日收盘后落库（表：date/symbol/close/volume/amount/pe/pb/market_cap/量比），全市场单次调用。
- 验收：连续两日落库；增量追加不重写历史；查询任一日截面 <1s。

### ②-B 规则漏斗（2-3 天）——注意：不用现成 screening

- 现成 `screening_service`（3,893 行 + 26 文件）是 LLM 排序流水线，贵且慢，本阶段**不碰它**（第 ④ 阶段冻结）。
- 新建轻量 `src/services/market_scan_service.py`：规则漏斗（零 LLM）——流动性下限（成交额）/ 估值带（剔除亏损与极端 PE）/ 市值带 → 全市场 → ~300 只候选池。
- 漏斗参数写进 mandate（见 ②-C），**不做网格调参**（quant 纪律：参数一律结构值或社区共识值，预注册）。
- 验收：全市场 → 候选池输出带漏斗各层留存数日志；候选池稳定性测试（相邻两日池变化 <20%）；对候选池批量拉日线有缓存（300 次/日上限可控）。

### ②-C mandate.yaml（0.5 天）

抄 ai-hedge-fund 的 mandate 概念，仓库根 `mandate.yaml`（一份声明，系统照办）：

```yaml
capital: 10000
risk:
  max_single_position: 0.25   # 单票上限
  max_total_exposure: 0.95
  allocation: {convertible_bond: 0.6, stock: 0.4}
cadence:
  rebalance: quarterly        # 转债季频（已验证）；股票观察日频、调仓另定
filters:
  liquidity_min_amount: 30000000   # 漏斗：日成交额下限
  pe_band: [0, 60]
notify:
  channels: [feishu]
  digest_time: "18:00"
```

- 验收：①-A 的 sizer 与 ②-B 的漏斗都从本文件读取参数；改 mandate 不需要改代码。

---

## 五、阶段 ③：账本——模拟 vs 真实两本账

### ③-A 模拟账本（2-3 天）

- 抄 freqtrade dry_run 语义：操作单生成时同步记模拟成交（假设：T-1 收盘价或次日收盘价成交 + QR PaperBroker 费率函数 + 滑点），形成模拟账本。
- **费率假设唯一真源**：把 QR `trade/broker.py` 的佣金/滑点计算抽为共享模块（QR 侧 re-export 保持兼容），dsa 模拟账本与 QR 回测用同一份费率——保证"模拟/回测/真实"三者可比。
- 真实账本 = PortfolioService（唯一真源，已就绪）。
- 验收：同一操作单在模拟账本与回填后的真实账本各有一笔；费用字段一致；模拟账本可出净值曲线。

### ③-B 对比与 Web 三页（2-3 天）

- 对比指标：逐笔偏差（模拟价 vs 真实成交价）、累计净值曲线双线、费用实际 vs 模型。
- Web 加三页（复用 dsa-web，不加新框架）：模拟vs真实对比 / 信号统计（复用 decision_signal outcome 体系）/ 系统健康（①-E 数据）。
- 验收：回填 5 笔真实成交后对比页有内容；净值曲线与费用对比渲染正确。

### ③ 阶段验收

- [ ] 连续运行一周：每天 2 条推送 + 操作单状态闭环（pending→filled/cancelled）
- [ ] 对比页能回答"信不信它"：模拟与真实的偏差量化可见
- [ ] 后端全量测试 0 失败；web lint+build 过

---

## 六、阶段 ④：结构性砍除（①-③ 验收后，上游合并点执行）

> 原则：**砍前必打 annotated tag 备份**（如 `pre-restructure-v1`）；每砍一类跑全量测试；上游合并冲突面按"只加不改"原则收敛。

| 砍除项 | 动作 | 预期行数 | 回归验证 |
| --- | --- | --- | --- |
| TDXJKBroker + run_live.py tdxjk 分支 + LiveMWU | QR 侧 git rm | ~800 | ✅ 已执行，QR 299 测试全过 |
| archive/ | git rm | — | 无 |
| Discord 平台 + 13 个未用 notification sender | 冻结（保留飞书 stream + 用户实际用的 1-2 渠道），先 deprecate 后删 | ~3,000 | notification 路由测试 |
| Agent 多智能体**例行**模式 | 例行分析固定走 legacy 单调用链；Agent 模式仅保留 bot 交互入口 | ~11,000 路径摘除 | 单股分析输出契约不变（对比砍前后报告 diff） |
| screening 冻结 | 配置开关默认 off + 文档标注冻结（不删，等 ②B 漏斗稳定后评估） | 0（路径摘除） | 默认路径不触发 |
| run_diagnostics/run_flow 简化 | 收缩为轻量 run 记录（借 QR experiments.py 模式） | ~2,500 | run 记录可查 |
| 配置三件套收缩 | config.py 374 字段 → 剔除砍除项对应配置；system_config_service 拆薄 | 视 ④ 前序项而定 | 配置读取回归 + Web 设置页可用 |

砍除后规范整理（**必做**，防止"砍完即乱"）：

1. `.env.example` 同步删除死配置（本次盘点已证实：抽查 37 项零死配置——砍除后才会产生死配置，须同步清）。
2. `docs/INDEX.md` / README 导航更新；CHANGELOG 每砍一类一行。
3. bot 命令 help、Web 菜单中移除已砍功能入口。
4. 更新 `AGENTS.md` 的目录职责表（若模块边界变化）。
5. 全仓 grep 已删模块名，确认零残留引用（含测试、文档、workflow）。

---

## 七、阶段 ⑤：慢线——股票系统化策略预注册研究

> 前置依赖：② 的漏斗与候选池稳定运行 ≥2 周。纪律全量继承 quant 侧（预注册/holdout/实验注册 N/诚实负结果）。

- 研究顺序建议（每项独立预注册，fail 即记录并关闭）：
  1. 股票侧双低/低波类基础因子在候选池上的 IC 诊断（借 QR factors.py 的 evaluate/ic_by_year 管道，数据从 ② 截面库取）。
  2. ytm_proxy 思想在股票侧的对应物（红利/质量代理）分年稳定性。
  3. 通过诊断的因子 → 季频调仓回测（真实费率，QR 两段式引擎复用）→ DSR/HLZ 审计 → 才允许进入操作单股票栏从"观点"升级为"信号"。
- 明确不做：自动挖因子、高频、每因子网格调参。
- web_intent 半孤儿资产：若股票栏需要 bot 交互解析，复用其分词管道（800 行已有测试），不再新写解析器。

---

## 八、挂钟线（与上述并行，不占阶段）

- 每月 1 日：`py scripts/update_pit_tail.py` → `py scripts/build_pit_snapshot.py --assemble-only --start 2018`（**`--start 2018` 不可省**，否则丢早期退市债；universe 已修复为 raw 目录树优先）。
- 季末 PIT 覆盖到季末最后工作日后：`py scripts/run_paper_tracking.py --record --quarter YYYYQn`。
- 2027Q2：C1 判定（证据：双低 2025-26 分年 IC 归零 / ytm_proxy 稳健 / 三表实绩）。

---

## 九、GLM-5.3F 执行注意事项（踩坑记录，必读）

1. **`python` 是 Windows 坏桩**，一律用 `py`；工作目录 `daily_stock_analysis/` 或 `quant_research/` 子目录。
2. **全量测试禁止并发**：两轮 pytest 共享 SQLite/端口会互相踩出假失败（曾自残出 229 failures）。全量回归必须独占环境跑完。
3. **目录 rename 是合并黑洞**：本仓库 dsa 在 `daily_stock_analysis/` 子目录，上游是根布局——merge 时上游新增文件会被 rename 检测静默吞掉（曾丢 4 个 desktop 模块）。合并后必跑 `git diff --name-status --diff-filter=A <base> origin/main` 核对新增文件逐个就位。
4. **冲突路径前缀化**：上游测试/CI 的仓库根假设要按本地布局改写（`parents[1]`→`parents[2]`、路径加 `daily_stock_analysis/` 前缀）。
5. **Windows spawn 冷启动 >> 1s**：上游 watchdog 类测试在 win32 走 `skipIf`（已有先例），不要试图放大超时硬修。
6. **测试 DB 隔离**：`PortfolioRepository()` 默认连真实库（`data/stock_analysis.db`）！测试必须 `DATABASE_PATH` 指向 tmp + `DatabaseManager.reset_instance()`（先例：test_portfolio_analysis_context.py）。调试代码也绝不能默认连真库。
7. **CRLF**：本仓库部分文件 CRLF，`py - <<'EOF'` 脚本写 `.gitignore` 等带模式匹配的文件时 `\r` 会破坏匹配，写后必须 `git check-ignore` 验证。
8. **提交规范**：英文 message、无 Co-Authored-By、未经确认不 push；quant 与 dsa 的改动分 commit。
9. **两项目边界**：跨项目集成只走文件契约（如 ①-C 的 JSON），禁止 import 跨界。
10. **每阶段收尾自查**：`git status` 干净 + CHANGELOG 已记 + 文档导航已更新 + 全量测试基线不降。

---

## 十、验收总表（终局完成的定义）

| # | 验收项 | 手段 |
| --- | --- | --- |
| 1 | 每天恰好 2 条推送（盘前操作单 + 盘后摘要），分级有序 | 实收消息 |
| 2 | 操作单价格全部是具体限价数字 + 边界规则 | 抽查 10 张 |
| 3 | 转债栏可回溯（sheet_id→信号→回测假设），股票栏标注"观点" | 操作单渲染 |
| 4 | 回填成交后：真实账本、模拟账本、操作单状态三者联动 | 人工回填 3 笔验证 |
| 5 | 模拟 vs 真实偏差页可回答"信不信它" | Web 对比页 |
| 6 | `/状态` 30 秒讲清系统健康；任一监控故障可见 | 注入故障演练 |
| 7 | mandate.yaml 改参数不改代码即可生效 | 改单票上限观察操作单变化 |
| 8 | 3.5 万行砍除完成、零残留引用、测试基线不降 | ④ 的回归表 |
| 9 | quant 挂钟节奏未受干扰（PIT 增量/三表照常） | 季末三表产出 |
| 10 | 上游合并恢复"低冲突"（只加不改区域外无 diff） | 下次合并实测 |

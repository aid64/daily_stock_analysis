# quant_research 交接文档（2026-09-27 追加现状快照——见第 0 节，以此为准）

> 用途：新会话/新环境先读本文件，再按"快速恢复"验证环境。
> 配套文档：**`docs/CB-RESEARCH-PLAN.md`（下一个研究周期的唯一真源：双低失效诊断 + 三实验接班计划）**、**`docs/ROADMAP-V2.md`（历史执行记录，F1-F8 已闭环）**、
> `docs/ROADMAP-V2.2.md`（历史规格，已被 ROADMAP-V2 取代——见 0.2 节）、`docs/ROADMAP.md`（深度审视参考）、
> 
> `daily_stock_analysis/docs/CHANGELOG.md`、`PRIMARY_SOURCE_RESEARCH.md`。

## 0. 现状快照（2026-09-27 全量阅读对账——与下文旧正文冲突处，以本节为准）

### 0.1 研究周期状态（09-07 之后旧正文缺失的三件事）
1. **09-12 E1 评估器修正**：发现前视/口径 bug（premium 窗口末值映射、调仓日误用、年化÷4、转债误收印花税、现金不守恒），E1 旧结论作废重跑——合成因子**仍 fail**（超额 -1.43pp），但 ytm_true 单臂 +13.11% 成为四臂最强（CB-RESEARCH-PLAN.md:155）。
2. **09-12/13 ytm_true 预注册 → FAIL 关闭**（回撤 43.46% > 允许 36.06%，`docs/ytm-true-preregistration.md:40-48`）。
3. **09-13 起进入 8 周功能冻结 + 影子期**（至约 2026-11-08）：**零新增研究/工程**，唯一动作 = 双低季频 paper tracking 照常 + 影子执行数据积累 + PIT 尾部增量（当前 pit_end 2026-09-23）。期满按 promotion gate 三选一（CB-RESEARCH-PLAN.md:158）。**跨项目影响**：dsa 侧期待的 strategy_intake 裁决报告在冻结期内不能新增生产（打包现有锁定证据也会因 data_as_of 过期被 dsa 新鲜度门正确拒绝）——首批裁决报告最早在冻结期满后产出，除非用户明确豁免"仅数据刷新与证据打包、不做研究决策"。

### 0.2 策略矩阵（真实三态：代码/配置/验证产物）
| 策略 | 代码 | 配置 | 验证产物 | 真实状态 |
| --- | --- | --- | --- | --- |
| ETF 动量（17 池） | ✅ ETFMomentum | ✅ etf_momentum.yaml | ✅ WF 8 折 + audit DSR 0.645 | **已验证；实盘禁令维持，允许进 shadow**（见 0.3） |
| ETF MWU 融合（13 ETF 三专家） | ✅ backtest/mwu.py | ✅ etf_mwu.yaml | ❌ 无独立产物（etf_mwu_cb 的 ETF 腿，R1b 后 delta=0） | 有代码无结论 |
| MWU+转债双低（月频） | ✅ | ✅ etf_mwu_cb_monthly.yaml | ✅ WF OOS 全负 -0.89%~-1.25% | **禁止实盘**（HANDOFF:156） |
| 转债双低两段式 | ✅ two_stage.py | ✅ convertible_double_low.yaml | ✅ 全套（52x 换手成本吞噬；E1 修正版 -30.45%） | **rejected**（crowding_silent） |
| E2 估值分位择时 | ✅ e2_valuation_timing.py | dsa 侧 active(system) | ✅ pass 但降级"待复核假说" | dsa 消费中 |
| ytm_true / 高YTM底仓 | ✅ | dsa cb_ytm_floor(registered) | ✅ 因子 IC 正；**策略 FAIL 关闭** | 线关闭 |
| 双低合成因子 / E3 下修 | ✅ | — | ✅ 均 fail | 线关闭 |
| 行业动量 / 多因子轮动 / 小市值 / 红利网格 | ❌ **quant 零代码** | 仅 dsa 注册表登记（社区来源） | ❌ | **有登记、无实现、无验证**——intake 永不为它们产出裁决 |

### 0.3 ETF 动量部署定性冲突的对账（09-26 dsa 登记引发的必答题）
- **冲突**：本文件:47"WF 8 折 OOS 无稳定参数，禁止实盘"（前 5 折全负）vs dsa `strategies.yaml` 09-26 登记"管道第一候选"。
- **对账结论**：两者兼容但语义必须钉死——**"禁止实盘"维持**（前 5 折 2023-2025H1 连续为负 = 无稳定实盘参数）；"第一候选"仅指**进入 dsa 管道的 shadow 观察态**（影子展示不落单，与禁实盘不冲突）。active/实盘须待：WF 刷新至当前数据后负折段被复证，或冻结期满 promotion gate 判定。
- **walk-forward 实数**（locked，快照 v1 sha 273c8153…）：2023 两折 -9.9%/-8.7% 年化、2024 两折 -2.1%/-4.7%、2025H1 -10.7%、2025H2 +14.1%、2026H1 +37.6%、2026-07~08 +6.3%。**前 5 折全负、后 3 折转正**。
- **参数口径三头不一致（打包裁决前必须钉死）**：yaml 默认 lookback=60/ma_filter=250；WF 网格 ma_filter=200（选中 60→90）；audit selected_config lookback=180。dsa 注册表 params 抄 yaml、evidence 却引用 200 网格口径的 WF 数字——口径不同源，禁止混用。
- **快照世代**：v1（08-15，WF/audit 消费）、v2（08-17）、**v3（09-01，最新至 2026-09-01，无任何消费者——文档此前漏记）**。ETF 产物数据边界 08-08（holdout 冻结线）。

### 0.4 数据层现状
- 转债 PIT：`data/pit/convertible_pit.parquet`（09-24 刷新）631,902 行 / 853 只 / future_rows=0；每日 paper tracking 驱动尾部增量（update_pit_tail.py）。
- ETF：无 PIT 管线，走三份冻结快照 pkl（v1/v2/v3）；research 冻结边界 2026-08-08。
- 遗留待清理：`data/universe_cb.csv`/`universe_cb_pit.csv`（68 只旧口径）、`cb_candidate_union{,_v2}.txt`（已废除机制的产物）、`data/cache/bars/` hfq 子目录。

### 0.5 跨项目契约现状
- `output/paper_tracking/current_plan.json` 已是 **schema cb_current_plan_v2**（旧正文"schema v1"过时）。
- **strategy_intake 桥只有消费端**：dsa `src/services/strategy_intake.py` 期待 `quant_research/output/strategy_intake/`，该目录不存在、quant 侧无任何生成 `strategy_intake_verdict_v1` 的脚本（生产端是冻结期后的第一个工程项，需用户豁免或等 11-08）。
- **E2 分位"矛盾"已解除（09-27 实证）**：quant 88.8%（09-07）与 dsa 68.3%（09-24）是**同一指标随行情移动**——两侧同为"价格中位数 expanding 分位"（构造逐位一致），转债中位数 09-07→09-24 跌 133.77→122.87（-8.2%），分位 92.4%→68.3% 穿越防守线（历史 95.9~144.3 顶部密集，-8% 绝对跌幅 = 24pp 分位落差，敏感性属已知特性）。无需对齐；E2 门当前为"可配置"态。
- 旧正文更正：测试数"252 passed"→现 **321 passed + 2 network deselected**；快速恢复引用的 `universe_cb_pit.csv` 为已删除的旧口径（现用 `data/universe_cb_full.txt` 854 只，见本节下方命令）；`etf_mwu_cb_monthly.yaml:4` 头注释"+0.18%"已被 V5 证伪（真实 -3.45%），**已更正**（原文禁止引用）。

### 0.6 跨项目桥执行记录（2026-09-27，冻结豁免范围内）
1. **快照 v4**（`output/feeds_snapshot_v4.pkl`，sha256 3be84014…）：17 只 ETF，数据至 2026-09-24（9/25 中秋假，边界正确）；akshare 东财源 7 只降级新浪（与 v1/v3 同口径，基线 diff 容差内）。**发现 v3 的 512100.SH 复权因子污染**（close 1.17 vs 自身 amount/volume 隐含 3.12；v1/v4 干净且一致）——v3 从未被消费，作废勿用。
2. **WF 重跑被 holdout 闸正确拦截**：`assert_holdout_clean` 拒绝终点 ≥ 2026-08-09 的研究命令——holdout 保留给 C2 最终评估，提前消费不可豁免。ETF 动量的"新鲜证据"只能出自冻结期满（约 11-08）的 holdout 正式评估。
4. **影子信号跟踪上线（豁免 v3）**：`scripts/track_etf_momentum_signals.py` + dsa `/轮动信号`——每日收盘后计算 etf_momentum 动量得分（直接调用 momentum_scores，与回测同源）追加 `output/etf_momentum_tracking/signals.jsonl`。首信号（as_of 09-24）：仅 2/16 过闸（512890/511880）——偏防御形态。holdout 零触碰；11-08 评估时该日志即"影子期数据积累"的输入。

3. **桥生产端建成**：`scripts/build_intake_verdict.py`（纯证据搬运封装）——以既有锁定证据试产出报告（oos 2023-01~2026-08-08，8 折 +3/-5，复利 +17.20%，含 R2 闸后 +14.7% 口径 caveat），dsa 消费端干跑：schema 校验通过、**新鲜度门正确拒绝**（滞后 34 交易日 > 5）——生产端→消费端→门全链路验证。测试产物已清理，正式报告冻结期满后由脚本再生（2 秒）。

### 0.7 冻结豁免补录（2026-10-09，用户确认口径）

**豁免口径（用户 2026-10-09 拍板）**：修复既有契约的变更 + 纯防护性治理控制 ≠ 新研究/新工程。边界三条：(a) 不触碰 holdout（终点 ≥2026-08-09 的研究命令仍被 `assert_holdout_clean` 拒绝，不可豁免）；(b) 不产出新研究结论、不改变 §0.2 策略矩阵三态；(c) 不新增数据依赖或研究管线。

1. **追溯登记**：dsa 对比盘修复计划（`.scratch/dsa-deployment-repair-plan-20261007`）任务 05 改动 quant 执行链 4 文件 + 2 测试文件（2026-10-07/09，属上述口径的执行链契约修复，holdout 零触碰）：`convertible/snapshot.py`（+`pit_prices_to_feed`，PIT 价 → CB feed）、`core/mwu_core.py`（+`unpriced_terms` 审计属性，缺价显式可见）、`scripts/extract_mwu_targets.py`（CB feeds 生成 + 输出 `unpriced_terms`）、`scripts/run_paper_tracking.py`（导出失败显式 return 1 + `Any` 导入修复）；`tests/test_convertible.py` +6、`tests/test_mwu_core.py` +3。quant 323 测试全绿。
2. **授权登记**：dsa 修复计划任务 09（研究治理）在本口径下执行，含：run_backtest 链补 holdout 闸（防护性）、CI 收编 quant 离线测试（基础设施）、旧研究结论状态头注（文档）、vendor 复现二选一（依赖管理）。
3. **冻结本体不变**：8 周冻结至约 2026-11-08 照旧执行；通知收敛计划 P4 的 quant 工程项（PIT 更新后自动 assemble+export、decision snapshot 等）属新工程，**不在豁免范围**，冻结期满后再议。
4. **任务 09 执行补录（2026-10-13，本口径下落地）**：①`scripts/run_backtest.py` 补 holdout 闸（此前唯一裸露入口：默认终点=今天直达引擎，全链无守卫；现于 `parse_args` 后、配置加载前 fail-fast，并加 3 条回归测试）；②CI 收编 quant 离线测试（`.github/workflows/ci.yml` 新增 `quant-tests` job + 路径过滤 + `.github/requirements-quant-ci.txt` 依赖清单）；③旧结论更正（本节 §0.5 所列三处：测试数 252→321、快速恢复命令的 universe 口径、`etf_mwu_cb_monthly.yaml` 的 +0.18%）；④vendor 复现二选一**取"文档明确不可在干净克隆复现"**（`quant_research/vendor/` 299MB 不入库，补 `.gitignore` 防误提交；N 槽驱动 `vendor/Sequoia-X/n_slot_driver.py` 缺席时 `compare_deck_runner` 的 N 槽按 `sequoia_ready` 条件式跳过）。holdout 零触碰、无新研究结论、无新数据依赖。

## 1. 项目定位

低门槛个人量化交易系统（普通人量化路线）：

- 低手续费账户 + 执行通道（PaperBroker / TDXJK DLL 现状；QMT/miniQMT 为 read-only PoC 候选）+ AI 辅助策略迭代。
- **不以 10 万元 QMT 开户门槛为前提**；策略未过研究门槛前禁止向真实账户发单。
- 研究纪律：预注册门槛、冻结数据快照、实验注册（试验数 N 可追溯）、诚实负结果入档。
- 架构（2026-08-29 重构后六层，commit 4cc0dc8a..c01a9836）：
  1. **数据层** `core/panel.py`：PIT 长表 → (date × symbol) 矩阵，查询式 `get_panel`/`forward_returns`
  2. **因子层** `core/factors.py`：装饰器注册因子 + RankIC/IC 评估
  3. **决策层** `core/mwu_core.py` + `backtest/mwu.py`：MWUCore 唯一权重融合实现（回测/实盘共用）
  4. **规则层** `core/rules.py`：RuleBook（品种 × 生效日期 → 费率/涨跌停/T+1），broker 强制闸
  5. **运行时层** `backtest/adapter.py` + `trade/`：MarketView 协议适配反转；PaperBroker/TDXJKBroker
  6. **实验层** `core/experiments.py` + `runner.py`：run_id 注册、试验数 N 追溯、编排单一来源
- 两段式转债回测 `backtest/two_stage.py`：PIT 全量池矩阵选债 → 仅被选券 feed 执行模拟（**precompute 候选并集已废除**）。

## 2. 当前状态快照（2026-09-03）

- 全量测试：**299 passed + 2 network 冒烟**（`py -m pytest -q -m "not network"`，在 `quant_research/` 下执行）。
- ROADMAP-V2 全链条闭环；**蓝图阶段 ④ 已执行**：TDXJKBroker / LiveMWU / run_live.py 已移除（tag pre-restructure-v1 可回溯），PaperBroker/sizer/executor 保留为操作单引擎组件。
- **跨项目信号契约**：`run_paper_tracking.py --export-plan` 产出 `output/paper_tracking/current_plan.json`（schema v1），dsa 侧 cb_signal_bridge 消费生成操作单——两项目集成只走此文件。
- **CB-RESEARCH-PLAN 六个实验全部执行完毕**（2026-09-06/07）：
  - D0: tushare 数据源接入（1163 只 / 4878 票息行 / 2948 强赎事件 / 109k 转股价）
  - D1: ytm_true 全样本稳定正 IC（含双低死亡年 2021 +0.067）——期限维度有独立信号
  - D2: 共线性递减（0.57→0.35）、残差 IC 全正、AA/A- 最强——越来越不像纯价格因子
  - D3: 判定 crowding_silent（非结构死亡）——离散度在增加、无断点
  - E1: 合成因子 **fail**——四臂全负，截面选债 alpha 消失
  - **E2: 分位择时 pass**——回撤改善 31%，当前分位 95.1% → 减半仓信号
  - E3: 下修博弈 **fail**——328 事件超额 -0.56%，下修是续命不是起死回生
- **转债线最终状态**：截面选债 alpha 消失，但估值分位择时有效。操作 = 持有现有组合 + 估值 >80% 分位减半仓 + 等分位回落 <80% 后重新启用选债（届时合成因子可能重新生效）。
- C2 paper tracking 运行中：每月 PIT 增量，季末 `--record` 三表。
- 环境：Windows，Python 用 `py` 启动器（`python` 是坏桩）；工作目录 `d:\daily_stock_analysis\quant_research`。
- 代码规范入口：仓库根 `AGENTS.md`。

### 研究结论速览（详见第 3 节与 architecture_research 6.2.x）

| 路线 | 状态 | 关键数字 |
| --- | --- | --- |
| ETF 动量 | WF 8 折 OOS 无稳定参数，禁止实盘 | 后 3 折 +20%/+54%/+6%，前 5 折全负 |
| MWU+转债双低（月频 v2 过滤） | WF OOS 全负 → 禁止实盘 | 池规则后 -0.89%~-1.25%；R1b/R2 后 -0.50%~+1.94% |
| 双低因子本身 | **横截面预测力统计显著** | RankIC -0.05~-0.06，\|t\| 12.8-16.6（HLZ 门槛 3） |
| 两段式新基线（无并集预计算） | 独立基线，成本吞噬 | +15.28%/6.6 年，回撤 25.3%，换手 52.12x |
| 温度计门控（静态 170/140） | 预注册验证**不采纳** | OOS -13.66% vs 基线 +1.94%；静态阈值 regime 脆弱 |
| 过拟合审计 | DSR 过门（非纯噪声）、HLZ 全不显著 | ETF DSR 0.6447 / CB DSR 0.7363；t ≤1.06/1.78 |

**核心矛盾已收敛**：真实费率（万0.845+最低5）下季频基线 +14.57%（回撤 14.77%，换手 18.2x），
H1 换手控制 fail、H2 频率结构在 2x 费率 fail / 真实费率 pass、F8 止损带 fail（whipsaw）。
**下一阶段 = C2 paper 挂钟节奏**，2027Q2 凭证据做 C1 判定（双低因子 2025-26 分年 IC 归零失效，
ytm_proxy 2021-26 稳健——分年证据见 `output/factor_diagnostics.json`）。

### 关键数据资产

| 路径 | 说明 |
| --- | --- |
| `data/pit/convertible_pit.parquet` | 转债 PIT 快照（**全量池**）：628,270 行 / 853 只有效 / 8 列 schema（2026-09-02） |
| `data/pit/coverage.json` | 覆盖率审计：请求 854 / 有效 849 / coverage 99.41%，评级覆盖 100%，0 未来行，0 脏行 |
| `data/universe_cb_full.txt` | 全量 universe（854 只，2019+ 上市全部发行券） |
| `data/pit/raw/<代码>/{年份}.csv + meta.json` | 原始缓存（914 目录：iFinD 60 + AKShare 854；**重建成本最高**，支持 `--assemble-only` 离线重组） |
| `output/feeds_snapshot_v2.pkl` | **主用 ETF 快照**（17 只，2012 → 2026-08-17），SHA-256 `7a8cd2da...`；老 13 只与 v1 corr=1.0000 |
| `output/cb_feeds_snapshot.pkl` | 转债候选并集冻结 feed（160 只，SHA `146d7155...`） |
| `output/runs/` | 实验注册（run_id + index.jsonl，试验数 N 机器追溯） |
| `output/overfit_audit_*.json` | 两条路线的 DSR/HLZ/PBO 审计报告 |
| `output/cb_two_stage_weights.csv`、`cb_factor_ic_report.csv` | 两段式选债计划与因子 IC 报告 |
| `data/cache/bars/` | ETF 日线 parquet（仅作 assemble universe 回退源——2026-09-03 修复后 raw 目录树才是主 universe 源） |

## 3. 最新验证结论（务必先读，避免重复劳动）

> **时效声明（2026-09-03）**：2026-09-01 至 09-03 的 F1-F8 全部执行记录见 `docs/ROADMAP-V2.md`（含真实费率敏感性、滑点解析口径、分年因子证据、止损带预注册失败），本节以下为 08-31 前的历史批次。> 原 2026-08-31 声明：本节按时间顺序记录研究演进，**最下方（4.1-4.5）的批次更新**。当前有效基线以 2 节"研究结论速览"表和 4.5/4.6 节为准——特别是：候选并集 precompute 已被两段式回测废除（R5）、月频基线已从 -3.45% 演进为 -0.50%~+1.94%（R1b/R2 后）、双低因子 IC 显著是新增强结论。旧批次中的"当前基线"字样均指当时时点。

### 3.1 可转债 PIT（2026-08-18 重大更新：AKShare 补采完成）

- **覆盖率 39.71% → 100%**：41 只缺失券（原全部卡 iFinD missing_meta）已通过 `scripts/backfill_cb_from_akshare.py` 补采（bond_zh_cov_value_analysis 日频历史 + bond_zh_cov 评级/发行规模），PIT 重组为 59,609 行 / 68 只 / 0 脏行。**幸存者偏差已修复**，双低回测从此基于完整 universe。
- AKShare 补采的 meta 为近似口径：`list_date`=数据首日、评级=当前评级静态映射（iFinD meta 同为稀疏事件结构，兼容）；meta 带 `source: "akshare"` 与 `issue_size`（发行规模，A1-3 扩展 remain_size 时可消费）。
- 128062.SZ 评级 CCC（违约风险券）已正确入库，评级过滤规则上线后将被排除。
- `coverage.json` 字段：`missing_meta_symbols` / `missing_daily_symbols` / `no_usable_pit_symbols`（现全空）/ `observed_lifecycle`（每券 `first_observed` / `last_observed`）。
- `last_observed` 只是最后可观察行情日，**不代表最后交易日**；引擎不得把数据末行当退市强平。
- 2007-2013 老券在 iFinD 无有效转股溢价率（universe 定为 2019-2021 现代券，其中含大量已退市券，双低回测无幸存者偏差）。
- 历史结论（27 只子集时代）：月频基线 2020-2026 长期约打平、测试段小负，**不构成盈利结论**；100% 覆盖后需重跑复核。

### 3.1.1 完整 universe 复核（2026-08-18，V1-V5 验证批）

- **V1 评级静态映射一致性**：bond_zh_cov 当前评级 vs iFinD 发行评级，73 只对照 68 一致（93.2%），5 只不一致均为 ±1 档评级迁移（AA↔AA-、AA+↔AAA），**无一跨过 A- 过滤线**——静态映射对评级过滤可用。
- **V2 list_date 修正**：bond_zh_cov 有 `上市时间` 列（真值），41 只 akshare meta 的 list_date 已由"数据首日"修正为真实上市时间（`--enrich-only` 完成）。
- **V3 东财行情接口持续断连**（本网络环境对 push2his.eastmoney.com 的 RemoteDisconnected，与 adjust 参数无关，跨日复测仍在）——快照维持新浪+分拆修正口径；511880 计息 / 512890 分红低估的近似保留，待东财恢复后可用 `build_feeds_snapshot.py --source akshare` 重建升级。
- **V4 链序修复**：`loader._etf_chain` 已改为 **akshare > tushare > baostock**（原 tushare 优先会被假断点污染）。同时清除 `data/cache/bars/*/hfq/` 全部 ETF 缓存（8/15 tushare 时代产物，5 只分拆断点实证污染）；CB 的 none 缓存无分拆问题，保留。
- **V5 完整 universe 重跑（etf_mwu_cb 月频，2020-01-01~2026-08-08，配置 `config/strategy/etf_mwu_cb_monthly.yaml`）**：总收益 **-3.45%**（旧 27 只口径 +0.18%）、回撤 27.76%（旧 25.89%）、换手 19.06x（旧 12.13x）、成交订单 243。**幸存者偏差修复使月频基线由"打平"转为小负**——旧打平是退市券缺失造成的幻觉；B 阶段参数优化必须基于 -3.45% 的真实基线，禁止引用旧 +0.18%。
- **A1-3 PIT schema 扩展落地**：PIT 8 列（+`maturity_date`/`issue_size`，广播静态列，100% 覆盖）；maturity 来源：集思录真值（30 只子集命中）或 申购日+6 年近似（`maturity_note` 标记）。剩余规模历史免费源不可得，issue_size 为静态近似。

### 3.1.2 全量池采集与 B1 规则落地（2026-08-18 下午批）

- **universe 68 → 854 只**（`data/universe_cb_full.txt`，2019+ 上市全量发行券，来自 bond_zh_cov；年份分布 2019:106 ~ 2026:50）。AKShare 补采 786 只全部成功（两轮：614+172，断点续传设计生效）；PIT 重组为 **624,637 行 / 849 只有效（99.41% 覆盖）/ 每日中位 362 只候选**（原 32 只）。
- **B1 v2 过滤规则上线**（`PITConvertibleDoubleLowExpert`，默认 None 关闭、配置启用）：`max_conv_value`（转股价值=价格/(1+溢价率/100) 数学推导，无需额外数据，防强赎风险区）、`min_maturity_days`（临期排除，maturity 缺失宽容保留）、`max_price`。5 个新测试覆盖，全套 176 passed。
- **关键实验：池规模 × v2 规则交互**（etf_mwu_cb 月频 2020-2026）：
  | 配置 | 总收益 | 回撤 | 换手 |
  | --- | --- | --- | --- |
  | 68 只池 无 v2 | -3.45% | 27.76% | 19.06x |
  | 68 只池 + v2 | **-29.21%** | 45.82% | 14.09x |
  | 849 只池 无 v2 | -5.09% | 28.05% | 19.79x |
  | **849 只池 + v2（当前基线）** | **-1.44%** | 27.91% | 19.12x |
- **教训**：v2 规则在 68 只小池上把候选掏空（42/92 个月 <5 只、13 个月归零）→ 空仓/集中致 -29%；在 849 只大池上保护作用充分体现（净改善 3.65pp）。**过滤规则必须在足够大的池上运作**——这是对社区标准隐含前提的实证确认。
- **性能优化**：全量池 849 只全建 backtrader feed 不可行；`scripts/precompute_cb_union.py` 预计算月频调仓日双低 top15 候选并集（276 只，确定性推导无前视），`run_backtest.py --cb-symbols-file` 消费。选债结果不受影响（并集 ⊇ 过滤后候选）。
- **R1/R2/R3 风险闭环**：issue_size 静态近似无前视（发行时已知），剩余/发行比 0.83-1.00（转股稀释有限）定案可用；到期日"申购+6年"近似在 19 只到期退市券上误差 ≤1 天（精度惊人）；东财断连为环境级持续状态，维持文档化。
- 附带发现：404002/404003 评级 C 券已入库（评级过滤正确处理）；2022-2024 上市新券经历转债信用危机，是"大池无 v2 恶化 1.6pp"的主要来源。

### 3.1.3 B2 前置风险闭环与 WF 扩展（2026-08-18 晚间批）

- **R1 候选并集参数化**：`precompute_cb_union.py` 全参数化（--freq/--top-n/--max-premium/--min-rating/v2 三参数/--out），与专家过滤链严格一致（rating→premium→median→新债→v2→top_n）。**网格超集原则**：扫参时并集必须用网格最宽参数生成（top_n 取最大、上限阈值取最宽），否则各组合 feed 池不一致。v2 宽参数并集 = **160 只**（`data/cb_candidate_union_v2.txt`，无 v2 版 276 只保留）。
- **R2 数据安全与评级验证**：`.gitignore` 补 `quant_research/data/pit/raw/`（原规则只覆盖 data/raw/，914 目录会被误提交）；raw 缓存实测 23.9MB；PIT 中 223 只 A+ 及以下评级券**零出现在 v2 并集**（含 C 级 113575/404002），评级过滤数据级验证通过。
- **B2-1 CB feeds 冻结快照**：`scripts/build_cb_feeds_snapshot.py` → `output/cb_feeds_snapshot.pkl`（160/160 零失败，SHA-256 `146d7155c3525c1d...`，tushare cb_daily 链，缓存续传）。
- **B2-2 WF 扩展**：`run_walk_forward.py` 支持 `--strategy etf_mwu_cb`（--pit/--cb-feeds 必填）。网格键自动路由（lookback/top_k/ma_filter→momentum 专家；top_n/max_premium/max_conv_value 等→CB 专家；eta/loss_scale→MWU 顶层）；warmup 取全专家窗口最大值（425 天）；**测试指标裁剪落地**（equity_curve 日收益序列只统计 [test_start, test_end]，修复复核发现 1 的 warmup 污染——冒烟实证：整跑 +1.92% → 测试窗 +0.07%）；锁定 CSV 附 strategy + 三输入 SHA（snapshot/pit/cb_feeds）。冒烟通过（1 折，ETFMWU + 17 ETF + 143 CB）。
- **B2-3 全量网格运行中**：4 组合（top_n=3,5 × max_premium=15,20）× 8 折（2020-2026，3 年训练/6 月测试）并行 4 进程；输出 `output/wf_mwu_cb_t{n}p{m}.csv`。MWU/动量/均值回归参数固定为 monthly yaml 默认（单变量原则：只扫 CB 选债参数）。

### 3.1.4 B2 结果：MWU+转债 WF（2026-08-18 完成，诚实负结果）

8 折 × 4 组合（锁定参数流程同 ETF WF：训练窗选参→锁定→测试窗，测试指标已裁剪 warmup 污染）：

| 组合 | OOS 累计 | 正折数 | 测试回撤(max) | 换手(均值/折) |
| --- | --- | --- | --- | --- |
| top_n=3, premium=15 | -12.97% | 5/8 | 21.15% | 3.3x |
| top_n=3, premium=20 | -3.26% | 5/8 | 10.45% | 3.2x |
| top_n=5, premium=15 | -13.16% | 5/8 | 21.15% | 3.2x |
| **top_n=5, premium=20（最优）** | **-1.65%** | 5/8 | 10.45% | 3.2x |

逐折（t5p20）：2023 两折 -6.1%/-3.2% → 2024 +3.0%/+0.7% → 2025H1 +0.07%/H2 +7.3% → 2026H1 +9.2% → **2026-07/08 折 -11.0%**。

**结论（按预注册纪律）**：
1. **无组合 OOS 为正 → 不接入 LiveMWU/TDXJKBroker**（与 ETF 动量 WF 结论一致；两条路线均未达锁定门槛）。
2. 参数敏感度：top_n 近乎无影响（3≈5）；max_premium 20 显著优于 15，但差异几乎全部来自 fold 8——不可作为"20 更优"的证据。
3. 训练/测试年化同号折数仅 2-3/8：**训练窗选参信号对测试窗无预测力**，锁参准则（annual-0.25×dd）不可迁移——这是比收益为负更根本的负结果。
4. **fold 8 根因（已诊断，未修复）**：非市场崩盘（2026-07 双低前 20 中位 +3.4%）；union 160 只中 2026-06-30 仅 28 只存活（存量缩水+强赎潮），池萎缩 → 集中度暴露，一只 -23% 券进入持仓域即重创。候选缓解：**池规模下限规则**（候选 <N 只时 CB 专家让位防御）——留待下轮验证，不在本轮加（对失败折打补丁=过拟合最后一折）。
5. fold 8 窗口仅 5.5 周，年化口径（-88.8%）严重失真，报告以总收益为准。

结果文件：`output/wf_mwu_cb_t{3,5}p{15,20}.csv` + `_locked.csv`（含三输入 SHA，可复现）。

### 3.1.5 池规模下限规则验证（2026-08-18 P 批，规则采纳 / 实盘结论不变）

- **实现**：`PITConvertibleDoubleLowExpert` 新增 `min_candidates`（默认 None 关闭）；检查基于单一来源 `_filter_double_low_pool`（从 `compute_double_low_scores` 抽出的完整过滤链 helper），语义 = "评级→溢价→中位→新债过滤后的候选数 < 阈值 → 专家让位现金"。阈值取**结构值 top_n×3=15，未做网格扫描**（防对失败折拟合）；规则系事后设计（看到 fold 8 失败后），此事后性已在文档声明。178 tests passed（+2）。
- **全局验证（4 组合 × 8 折重跑，`output/wf_mwu_cb_mc_*.csv`）**：
  | 组合 | 旧 OOS | 新 OOS | 旧回撤max | 新回撤max |
  | --- | --- | --- | --- | --- |
  | t3p15 | -12.97% | **-1.25%** | 21.15% | 9.89% |
  | t5p15 | -13.16% | **-1.25%** | 21.15% | 9.89% |
  | t3p20 | -3.26% | **-0.99%** | 10.45% | 9.89% |
  | t5p20 | -1.65% | **-0.89%** | 10.45% | 9.89% |
- **验证标准逐条核对**：① 4 组合 OOS 全部改善（非只救 fold 8）；② 正常期（前 7 折）干扰微小（Δ -0.8pp ~ +1.8pp，机制性规则特征）；③ 回撤上限 21.15%→9.89% 全线收敛；④ fold 8 从 -21.6%（p15）收敛至 -10.4%，剩余损失为 ETF 端 + 池规则触发前段。**规则采纳**（尾部风险管理属性：正常期零干扰、萎缩期防集中爆仓）。
- **附带发现**：池规则使 top_n 差异被抹平（t3p15≡t5p15、t3p20≈t5p20——萎缩期都让位）；**前 7 折（2023-01~2026-06）OOS 全部为正（+10.3%~+10.7%）**，唯一深坑是 2026-07/08 折。
- **PBO/CSCV 决定（不做的理由）**：标准 PBO 需要几十+配置 × 逐期收益矩阵；当前 N=4 且池规则下配置行为趋同，排名统计无效力。同号折数复核（1-3/8）确认"选参无预测力"结论在池规则下不变。未来若扩配置矩阵（≥20 配置）再上 CSCV。
- **实盘结论不变**：新 OOS 全负（-0.89%~-1.25%），不接入 LiveMWU/TDXJKBroker。`etf_mwu_cb_monthly.yaml` 已启用 min_candidates: 15 作为后续实验的新基线配置。

### 3.2 ETF 动量 Walk-Forward（2026-08-17 完成）

- 命令：`py scripts/run_walk_forward.py --snapshot output/feeds_snapshot.pkl --start 2020-01-01 --end 2026-08-08 --train-years 3 --test-months 6 --grid lookback=60,90 --grid top_k=2,3 --grid ma_filter=200 --output output/walk_forward_etf_2020_2026.csv`
- 机制：每折只用训练窗（3 年）按 `annual_return - 0.25 * max_drawdown` 选参（优先回撤 ≤40% 候选），**锁定后**才跑下一测试窗（6 个月）；测试指标不参与选参。
- 8 折测试收益：-13.27% / -11.92% / -2.81% / -6.49% / -14.18% / **+19.99% / +54.23% / +6.30%**。
- 前 5 折（约 2023-2025 熊市段）全负，后 3 折（2025-07 之后）转正；后 3 折均选中 `lookback=90, top_k=2, ma_filter=200`。
- **结论：无跨 regime 稳定参数，禁止接入 `LiveMWU` / `TDXJKBroker`。** 后期 3 折的表现不可外推。

## 4. 最终战略方案（2026-08-18 广域调研定稿，数据源已验证）

> 背景：ETF 动量与 MWU+转债两条路线 WF 验证均未达标（OOS 全负）后，进行全品类广域调研（事件套利/打新/被动/小市值/ML/期权/期货/日历效应/国际社区），按用户硬约束（小资金 1-10 万、TDXJK 半自动执行、已有通知系统与回测基建、业余时间）评分定稿。**所有成功实盘者均为多策略组合，无一单策略。**

### 三层小资金多策略体系

**第 1 层 收益垫（立即建，主项目能力，零回测需求——价差由合约确定）**
- **事件套利监控**：要约收购/现金选择权/私有化/主动退市公告扫描 → 价差>3% 且全面要约优先 → 巴菲特四问核查表 → 推送
  - 证据：2025 年 A 股 9 起此类事件，实跑者单笔 2.5-18%（玉龙/中航产融/ST新潮/ST天茂/德邦等）；巴菲特套利框架背书；小资金专属容量
  - **数据源已验证**（2026-08-18 实测）：`ak.stock_notice_report(symbol="全部", date=YYYYMMDD)` 全市场日公告流（约 500-1300 条/日）→ 标题关键词过滤（要约收购/现金选择权/私有化/主动退市/终止上市）——实测在 2026-08-12 真实命中 ST龙元(600491) 要约收购 3 条公告；`ak.stock_zh_a_disclosure_report_cninfo(symbol, keyword)` 用于个券深度跟踪（实测德邦股份现金选择权全链路 10 条）
- **转债打新提醒**：申购日 9:25 提醒顶格申购 / 中签 T+2 缴款 / 上市日 14:50 提醒卖出
  - 证据：2026 年首日涨幅均值 52.95%（45 只中 32 只顶格 57.3%）；单账户年期望 600-2000 元（户均中 2-4 签）
  - **数据源已验证**：`ak.bond_zh_cov()` 申购日期列（实测 21 只 7-12 月窗口，含未来 3 只待申购）
- 预期贡献：年化 +5~15%（视机会密度）

**第 2 层 底仓（与第 1 层并行，零维护）**
- 转债 ETF（511380，成立以来年化 ~6.5%、2025 +17.9%、回撤 16%）或 被动配置组合（300/红利低波/黄金/国债，研报级验证年化 7-11%）
- 预期贡献：年化 6~10%

**第 3 层 进攻（二期，quant_research，必须先过 WF 验证）**
- 首选：转债三因子周频折扣版（社区实盘者年化 45% 含费为日频原版；TDXJK 约束下重验证周频）
- 备选：小市值国九条（用本仓库独有退市股 universe 做无幸存者偏差验证——社区回测均无此能力）
- 门槛：沿用既有纪律（OOS 为正 + 折间一致性），不达标不上

### 旧任务清单状态
- ~~转债 universe 补齐~~（已完成：854 只全量，coverage 99.41%）
- ~~生命周期数据~~（已完成：maturity_date/issue_size 8 列 schema）
- ~~WF 扩展~~（已完成：etf_mwu_cb 支持 + 8 折验证，诚实负结果）
- 版本保护（已决策：暂不提交，单 main 分支偏好保留）

### 下一步工程（第 1 层实施）— 已完成（2026-08-18）

第 1 层两个监控模块已落地主项目（schedule 模式后台任务，配置默认关闭）：

- **事件套利监控** `daily_stock_analysis/src/services/event_arbitrage_monitor.py`：
  `stock_notice_report` 全市场公告扫描（回看昨天+今天）→ 关键词过滤（要约收购/现金选择权/私有化/主动退市/终止上市）→ 噪声排除（风险提示类）→ 批内+持久化双重去重（data/cache/event_arbitrage_state.json）→ 推送（附巴菲特四问核查表 + 风险提示）。通知失败不标记已读（下轮重试）。
- **转债打新日历** `daily_stock_analysis/src/services/bond_new_issue_calendar.py`：
  `bond_zh_cov` 发行表 → 三段式判定（subscribe：申购日∈[今,明]；payment：申购日∈[昨-1,昨] T+2 缴款窗；listing：上市日∈[今,明]）→ 去重 → 分段推送。
- **接入**：`main.py` schedule 分支注册两个后台任务（沿用 agent_event_monitor 范式）；配置项 `EVENT_ARBITRAGE_MONITOR_ENABLED` / `BOND_NEW_ISSUE_MONITOR_ENABLED` / `MARKET_MONITOR_INTERVAL_MINUTES`（默认关，60 分钟间隔，已在 config.py 与 .env.example 同步）。
- **验证**：17 项单测全过（tests/test_market_monitors.py：关键词/噪声/三段窗口/去重/持久化跨实例/网络失败兜底/通知失败不标记）；config 回归 120 项通过；实数据 smoke 通过（打新日历命中 7 条真实提醒：丰茂今日申购/强达明日申购/震裕缴款/4 只上市；事件监控当日无命中与预期一致）。
- 用户启用方式：`.env` 中设置 `EVENT_ARBITRAGE_MONITOR_ENABLED=true` 与 `BOND_NEW_ISSUE_MONITOR_ENABLED=true`，以 `--schedule` 模式运行。

### 4.1 2026-08-28 广域调研轮（知乎 API + 论文 + GitHub + 社区实盘）

- 产物：`.scratch/quant-research-report-20260828.md`（调研报告，四路证据汇总）与 `.scratch/quant-improvement-plan-20260828.md`（改进计划 P0-P3，含预注册门槛）。知乎原始数据 `.scratch/zhihu_research_20260828.json`（12 组查询）。
- 核心结论（影响后续所有决策）：
  1. **MWU regret 界是相对保证，不创造 alpha**；专家无 OOS 边际时融合层调参无意义——η/share_alpha 网格冻结。当前 `clip(-r,0,1)` 单边 loss clipping 违反理论假设（P0-1 修正项，非救命项）。
  2. **fold 8 定性升级**：2025-2026 转债存量坍缩至 5578 亿（回到 2018 水平）、双低中位数 >170 进入历史高估区，头部实盘者（yyb凌波）同期降仓撤离——fold 8 是"高估周期尾段"regime 现象，全市场一致，非代码 bug。
  3. **方法论升级可近零成本**：PBO/CSCV/DSR 可直接复用现有 WF 逐期收益矩阵（S=16 块枚举 ~50 行 numpy），先于任何新策略实验执行（P0-2），把"OOS 全负"拆解为噪声/真失效/小样本三成分。
  4. **第 3 层进攻策略不在估值高位上实盘**：双低中位数 <140 前只做研究；温度计门控（170 清仓/140 启用）作为 P1 首项实验。
  5. 确定性收益继续加厚第 1/2 层：事件套利假信号排除 + 要约比例 + 格雷厄姆年化；511380 溢价率买点规则（当前溢价偏高、近一年仅 +1.15%，只定投不追高）。
- 知乎调研新档案：本轮起的知乎搜索结果不再存 `daily_stock_analysis/docs/`（避免与早期 6 份战略档案混淆），统一存 `.scratch/`。

### 4.2 2026-08-29 架构调研与彻底重构方案

- 产物：`.scratch/quant-refactor-plan-20260829.md`（重构方案）与 `.scratch/zhihu_research_20260829_arch.json`（架构专题 10 组知乎搜索）。
- 六个已核实痛点：MWU 双实现漂移（mwu.py vs pipeline.py）、A 股规则硬编码（印花税无历史变更/T+1 靠策略自觉）、849 转债装不进 feed（precompute 并集绕路）、无因子抽象、实验散落无注册、脚本承载编排。
- 方案要点：**逐层渐进重构，不推倒重来、不整体迁移任何框架**。六层蓝图（数据/因子/决策/规则/运行时/实验），每层借一个成熟框架的一块（qlib 查询式数据与因子矩阵、LEAN Insight 管线、rqalpha 规则即数据、vnpy 引擎协议、聚宽目标式下单语义）；五步迁移 R1-R5（R1=MWUCore 抽取并吸收 P0-1，R2=RuleBook 并吸收 P0-3，R5=panel+因子层改动面最大放最后），每步独立验证回滚。
- 执行通道结论：XTBroker（xtquant/miniQMT）设计与坑清单已固化于方案文档；官方 vnpy_xt 仅行情不下单；月频用"进程退出式"（调仓日拉起跑完即退）而非常驻执行引擎。
- 反过度工程护栏：每步重构必须对应一个已核实痛点；重构期间不改策略行为，P 线（研究结论）与 R 线（工程可信度）分线推进。

### 4.3 重构执行记录（2026-08-29，R1-R5 六层蓝图全部完成）

- 工作树先以 4 个逻辑提交锚定基线（治理/主项目监控/quant_research 研究状态/调研产物），用户已授权提交。
- **R1a（4cc0dc8a，行为保持）**：`core/` 包（MarketView 协议 + MWUCore 唯一融合实现），适配方向反转，专家不再消费 backtrader line；修复 `run_live.py` dict/tuple 解包崩溃 bug。等价性对拍：合成回测净值与 40 步实盘权重轨迹逐位一致（`.scratch/r1_equiv_*.json`）。
- **R1b（ec9d9199，吸收 P0-1）**：loss 映射改为线性 ℓ=(1-ρ)/2，空选=现金 r=0（ℓ=0.5）；MWU+CB t5p20 8 折 OOS -0.89%→-0.50%（定性结论不变，不接实盘）；ETF 动量路线逐折 delta=0（不经 MWU 层）。
- **R2（e3140b5d）**：`core/rules.py` RuleBook（品种×日期→费率/涨跌停/T+1，含印花税减半/创业板注册制/创业板科创板 ETF ±20%/转债 ±20% 历史口径）；TPlus1Broker 升级为提交路径强制闸（此前涨跌停/T+1 规则从未实际生效）；过户费修正 0.001%。闸影响：ETF WF 3/8 折不变、OOS +17.2%→+14.7%（不可能成交被剔除）；CB WF 7/8 折不变、OOS -0.50%→+1.94%。
- **R3（05534b30）**：`quant_research/runner.py` 吸收两脚本的参数表/网格解析/warmup/切片/折切分/锁参准则/键路由（单一来源）；WF 重跑输出与重构前逐字节一致。
- **R4（082cf2b8）**：`core/experiments.py` 实验注册（run_id 目录 + index.jsonl，试验数 N 机器可追溯，供 P0-2 DSR）；已接入 run_backtest/run_grid/run_walk_forward/run_cb_backtest。
- **R5（c01a9836）**：`core/panel.py`（PIT→date×symbol 矩阵 + 静态字段广播 + forward_returns）、`core/factors.py`（装饰器注册因子 + RankIC/IC 评估）、`backtest/two_stage.py`（第一段 PIT 全量池矩阵选债 `compute_cb_target_weights` 与 PIT 专家共用 `pit_snapshot_picks` 单一过滤链；第二段 `WeightTargetStrategy` 只为被选券建 feed 执行模拟；**precompute_cb_union 废除**）；执行 mixin 自 ETFMWU 抽取（TargetWeightExecutionMixin）。过程中修复两个真 bug：①目标外 1 手持仓因超额取整永远无法卖出（改整仓退出）；②退市券 NaN 估值塌方（PIT 合成 feed 前向填充）。
- **两段式新基线**（转债双低月频 v2，2020-2026，1 万本金，收盘价近似口径）：+15.28%/6.6 年（年化 +1.85%）、回撤 25.30%、190 笔、换手 52.12x——不可与旧 MWU 组合基线逐位对比，独立基线。
- **因子 IC 报告**（`output/cb_factor_ic_report.csv`）：双低因子 RankIC -0.05~-0.06、|t| 12.8-16.6（远超 HLZ t>3）且周期越长越强——横截面预测力统计显著；单独 premium_rate 不显著。IC 显著 ≠ 成本后可交易（52x 换手已证明费用吞掉毛 alpha）。
- 测试 178→**237 项全过**。**下一步**：P0-2 PBO/CSCV/DSR（R4 试验数 N 已就绪；对现有 WF 收益矩阵跑 CSCV S=16 枚举 + DSR + HLZ 门槛）；P1-1 温度计门控（不依赖重构，随时可做，PIT 面板可直接算双低中位数）。

### 4.4 P0-2 过拟合审计与 P1-1 温度计门控（2026-08-29 完成）

- **P0-2**：`core/overfit_audit.py`（CSCV/PBO + DSR + HLZ，无 scipy）；`scripts/run_overfit_audit.py` 宽矩阵审计入口。**预注册修订（有记录）**：固定矩阵互补恒等式诅咒实测推翻两次预期（iid 矩阵 PBO 0.95），PBO 降级为诊断参考，**主门槛 = DSR<0.5 + HLZ |t|>3**。
- **审计结论**：ETF 动量路线 DSR 0.6447 过门（非纯噪声）但 HLZ 日频 t 全 18 配置不显著（≤1.06）；CB 两段式路线 DSR 0.7363 过门、HLZ 全不显著（≤1.78）。两路线共同结论：无统计显著日频预测力，WF 纪律（OOS 正+折间一致）仍不满足，不实盘。
- **P1-1 温度计门控：不采纳**（预注册验证）。门控实现保留在 `pit_snapshot_picks`（dd_median_upper/lower，默认关闭）。8 折 WF：门控 OOS -13.66% vs 基线 +1.94%。归因：2023-2025 双低中位数几乎全程在 140-171 中间带（三年砍半仓位）；2026 全程 ≥170 清仓但 fold 8 仅改善 0.4pp（亏损源于 ETF 腿）。教训：静态绝对阈值在漂移统计量上 regime 脆弱；相对阈值留待独立预注册。
- 测试 237→**250 项全过**。产物：`output/overfit_audit_etf_momentum.json`、`output/overfit_audit_cb_two_stage.json`、`output/wf_p11_thermo_t5p20.csv`。
- **下一步**：按 ROADMAP §3.3 执行 A1（成本 1x/2x/4x 敏感性，全盘最关键曲线）→ A2（有效暴露口径）→ B1（H1 换手控制预注册）。

### 4.5 全仓未来计划定稿（2026-08-31）

- 产物：`docs/ROADMAP.md`（quant_research 深度路线图，主计划）+ `.scratch/project-future-plan-20260831.md`（全仓总览，DSA 侧主计划）。
- 核心判断：quant_research 已从"回测项目"变为"研究基础设施"——双低因子横截面预测力统计显著（RankIC |t| 12.8-16.6）但净收益被 52x 换手的成本吞噬，下一阶段第一变量是**成本而非新因子**（预注册假设 H1：rank-buffer + 最小换手约束，目标换手降 ≥30% 且 2x 成本下不劣于基线）；2026-08-09 之后数据冻结为最终 holdout，现有 2020-2026 窗口已被多轮研究触碰不再作最终 OOS。
- DSA 主项目方向：投资工作台闭环（统一 InvestmentEvent 事件模型、结构化套利核查卡、双信息源验证）；P2 监控层已落地（事件套利加厚/配债登记/ETF 溢价监控，commit c9aa7972）。
- 测试可靠性：主项目全量离线失败 57-81（混沌）→ 稳定 ≤3；跨测试环境污染六层根治（conftest 双层 env 隔离 + session 默认 ENV_FILE + ConfigManager WinError 5 重试回退，commit 9f26838c/15cd0eff）。唯一残余为 intelligence fail_open 负载敏感单测（隔离必过）。
- 执行通道：QMT 只做 read-only PoC；策略过研究门槛前 PaperBroker 跑完整年报季/强赎周期。
- 分支治理：本地领先 origin/main 39 个提交未推送；GitHub 直连被本机代理阻断（127.0.0.1 连接失败），恢复后先做上游差异审计（上游最新已知提交 5159bd72）再决定推送/合并策略。

## 5. 快速恢复（新环境/新会话）

```bash
# 1. 环境自检（工作目录必须是 quant_research）
cd d:\daily_stock_analysis\quant_research
py -m pytest -q          # 预期 321 passed + 2 network deselected

# 2. PIT 覆盖率复核（不发网络请求）
py scripts/build_pit_snapshot.py --validate-only --pit data/pit/convertible_pit.parquet --universe-file data/universe_cb_full.txt --start 2018-01-01 --end 2026-08-08

# 3. 从 raw 缓存离线重组 PIT（iFinD 不稳定时的重建路径）
py scripts/build_pit_snapshot.py --assemble-only --universe-file data/universe_cb_full.txt --pit data/pit/convertible_pit.parquet

# 4. 复跑 Walk-Forward（结果应可复现，快照 SHA-256 见上表）
py scripts/run_walk_forward.py --snapshot output/feeds_snapshot.pkl --start 2020-01-01 --end 2026-08-08 --train-years 3 --test-months 6 --grid lookback=60,90 --grid top_k=2,3 --grid ma_filter=200 --output output/walk_forward_etf_2020_2026.csv
```

### 新会话开场提示（可直接粘贴）

```text
继续 d:\daily_stock_analysis\quant_research 的低门槛量化项目。
先读 quant_research/HANDOFF.md **第 0 节（现状快照，以此为准）**、docs/CB-RESEARCH-PLAN.md（下一周期唯一真源）
与 daily_stock_analysis/docs/quant_research_architecture_research.md。
当前状态：321 tests passed；六层架构重构完成（core 包 / Runner / 实验注册 / 两段式转债回测）；
8 周功能冻结中（至约 2026-11-08），唯一动作 = 双低季频 paper tracking 照常 + 影子执行数据积累 + PIT 尾部增量；
冻结豁免口径见 HANDOFF §0.7（"修复既有契约的变更 + 纯防护性治理" ≠ 新研究/新工程）。
禁止实盘：WF 纪律未通过。遵守根目录 AGENTS.md。
```

## 6. 已知坑位（踩过的坑，勿重蹈）

- iFinD 批量查询返回截断时给全量 CSV URL，客户端已处理；连接重置/DNS 失败常见，采集必须断点续跑。
- 已退市券 CSV 列宽不一致 → `parse_daily_csv` 已用 `on_bad_lines="skip"` + 缺列返回空表，单券异常不拖垮批次。
- 转债代码带/不带交易所后缀混用 → 统计前必须 `normalize_cb_symbol()`。
- 1 万元本金下周频轮动手续费显著侵蚀收益 → 月频是小资金基线。
- 转债交易单位 10 张、普通 ETF 100 份、国债 ETF 10 张，下单量计算不可混用。
- **tushare 的 ETF 日线不处理份额分拆/折算**（2026-08-18 实证）：512480（2021-03 1拆4、2026-07 再拆）、510500（2015-04）、512010、512100、512890 等在 tushare hfq/qfq 下分拆日产生 ±50%~300% 假跳变，与东财源日收益 corr 仅 0.33~0.82。DataLoader 的 `_etf_chain` 把 tushare 排第一是**隐患**——构建 ETF 快照必须用 `build_feeds_snapshot.py --source akshare`（东财失败自动降级新浪 + `adjust_etf_share_split` 分拆修正，产出与 v1 快照 corr=1.0）。
- 东财 `fund_etf_hist_em` 偶发整体断连（RemoteDisconnected，与 adjust 参数无关），`build_feeds_snapshot.py` 已内置 3 次重试 + 新浪降级路径。
- 511880 银华日利为每日计息货币 ETF，新浪不复权源丢失计息收益（实测年化 0.08% vs 真实约 2%）；作为"空仓停靠资产"语义可用（低波动近零收益），但**不得用于收益测算**。512890 红利低波月度分红同样被低估（约年化少 1-2%），轮动排序影响轻微。
- 快照验证三件套（已固化在 `build_feeds_snapshot.py`）：基线 diff、收益率断点扫描（|日收益|>15% 预期仅剩创业板/科创 2024-09/10 真实 20cm）、必要列校验。
- **转债补采首选 AKShare 而非 iFinD**（2026-08-18 实证）：`bond_zh_cov_value_analysis` 含已退市券完整日频历史（收盘价/转股溢价率，2019 起，百分数口径与 PIT 一致），41 只 iFinD 缺失券 100% 覆盖；`bond_zh_cov` 是全量发行表（1050 只，含已退市），提供评级/申购日期/发行规模。路径：`scripts/backfill_cb_from_akshare.py` → `build_pit_snapshot.py --assemble-only`。已知近似：该源收盘价每只有 12-26 行 NaN（上市初期），组装时自动丢行。
- 转债 raw 缓存 meta.json 现有两种来源，`source` 字段区分（`akshare` / 无标记=iFinD）；`backfill_cb_from_akshare.py` 不会覆盖 iFinD 缓存。

# quant_research 交接文档（2026-08-17）

> 用途：作者更换账号/环境前固化项目状态。新会话应先读本文件，再按"快速恢复"验证环境。
> 配套文档：`daily_stock_analysis/docs/quant_research_architecture_research.md`（6.2.6 节为最新研究结论）、`daily_stock_analysis/docs/CHANGELOG.md`。

## 1. 项目定位

低门槛个人量化交易系统（普通人量化路线）：

- 低手续费账户 + TDXJK 通达信接口（http://tdxjk.bbroot.com）+ AI 辅助策略迭代。
- **不以 10 万元 QMT 开户门槛为前提**：保留 `PaperBroker` / `TDXJKBroker` 低门槛执行路径。
- 策略栈：ETF 动量 / RSI(2) 均值回归 / 国债 ETF 防御 / 可转债双低（`price + premium_rate`），四专家 MWU 融合（Fixed Share 回拉）。
- 回测框架：Backtrader 封装（`quant_research/backtest/engine.py`）。

## 2. 当前状态快照（2026-08-17）

- 全量测试：**171 passed**（`py -m pytest -q`，在 `quant_research/` 下执行）。
- 最新本地提交：`3689eb51`（docs: changelog）。**quant_research 全部工作均未提交**：
  - 已修改 21 个文件（backtest/engine、broker、strategies、config schema/loader、data loader/cache、clients、scripts、tests、docs）。
  - 新增未跟踪 17 项：`backtest/convertible.py`、`backtest/mwu.py`、`data/clients/ifind_client.py`、`trade/`、`scripts/build_pit_snapshot.py`、`scripts/run_grid.py`、`scripts/run_live.py`、`scripts/run_walk_forward.py`、5 个测试文件、2 个 MWU yaml、`data/`、`output/`。
- 环境：Windows，Python 用 `py` 启动器；工作目录必须是 `d:\daily_stock_analysis\quant_research`。
- 代码规范入口：仓库根 `AGENTS.md`（不擅自 commit/push、CHANGELOG 扁平格式、新配置同步 `.env.example`）。

### 关键数据资产（均未入库，切换环境前需备份）

| 路径 | 说明 |
| --- | --- |
| `data/pit/convertible_pit.parquet` | 转债 PIT 快照（**全量池**）：**624,637 行 / 849 只有效 / 8 列 schema / 每日中位 362 只候选**（2026-08-18 全量重建；68 只时代为 59,609 行） |
| `data/pit/coverage.json` | 覆盖率审计：请求 854 / 有效 849 / coverage 99.41%，评级覆盖 100%，0 未来行，0 脏行 |
| `data/universe_cb_full.txt` | 全量 universe（854 只，2019+ 上市全部发行券） |
| `data/cb_candidate_union.txt` | 月频双低候选并集（276 只，`precompute_cb_union.py` 产物，回测 `--cb-symbols-file` 用） |
| `data/pit/raw/<代码>/{年份}.csv + meta.json` | 原始缓存（**914 目录**：iFinD 60 + AKShare 854，meta 带 `source` 区分；**重建成本最高的资产**，支持 `--assemble-only` 离线重组） |
| `output/feeds_snapshot.pkl` | 冻结 ETF 行情快照（13 只 ETF，约 2012/2013 → 2026-08-14），SHA-256 `273c8153ec8c89228b65bf8068cd9faa4e5e2dfb4811202f6fe2311556f65161` |
| `output/feeds_snapshot_v2.pkl` | **当前主用快照**（17 只 ETF = 原 13 + 159531 中证2000 / 512890 红利低波 / 518880 黄金 / 511880 银华日利，2012 → 2026-08-17），SHA-256 `7a8cd2da9145a9902186986914c81920cae75e16e9f9c3fa147dd6a257e2ee11`；由 `scripts/build_feeds_snapshot.py --source akshare` 构建；老 13 只与 v1 日收益 corr=1.0000（历史结果可复现） |
| `output/walk_forward_etf_2020_2026.csv` + `_locked.csv` | 8 折滚动 Walk-Forward 逐折明细与锁定参数序列 |
| `data/cache/bars/` | 13 只 ETF 本地行情 parquet |
| `data/universe_cb_pit.csv` | 转债请求池（68 只） |

## 3. 最新验证结论（务必先读，避免重复劳动）

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
- **下一步候选**：P2 层确定性收益增强（主项目事件套利加厚/511380 溢价买点监控，见改进计划）；第 3 层新策略假设需全新预注册。

### 4.5 全仓未来计划定稿（2026-08-31）

- 产物：`.scratch/project-future-plan-20260831.md`（全仓两子项目 90 天路线图）。
- 核心判断：quant_research 已从"回测项目"变为"研究基础设施"——双低因子横截面预测力统计显著（RankIC |t| 12.8-16.6）但净收益被 52x 换手的成本吞噬，下一阶段第一变量是**成本而非新因子**（预注册假设 H1：rank-buffer + 最小换手约束，目标换手降 ≥30% 且 2x 成本下不劣于基线）；2026-08-09 之后数据冻结为最终 holdout，现有 2020-2026 窗口已被多轮研究触碰不再作最终 OOS。
- DSA 主项目方向：投资工作台闭环（统一 InvestmentEvent 事件模型、结构化套利核查卡、双信息源验证）；P2 监控层已落地（事件套利加厚/配债登记/ETF 溢价监控，commit c9aa7972）。
- 测试可靠性：主项目全量离线失败 57-81（混沌）→ 稳定 ≤3；跨测试环境污染六层根治（conftest 双层 env 隔离 + session 默认 ENV_FILE + ConfigManager WinError 5 重试回退，commit 9f26838c/15cd0eff）。唯一残余为 intelligence fail_open 负载敏感单测（隔离必过）。
- 执行通道：QMT 只做 read-only PoC；策略过研究门槛前 PaperBroker 跑完整年报季/强赎周期。
- 分支治理：本地领先 origin/main 39 个提交未推送；GitHub 直连被本机代理阻断（127.0.0.1 连接失败），恢复后先做上游差异审计（上游最新已知提交 5159bd72）再决定推送/合并策略。

## 5. 快速恢复（新环境/新会话）

```bash
# 1. 环境自检（工作目录必须是 quant_research）
cd d:\daily_stock_analysis\quant_research
py -m pytest -q          # 预期 252 passed

# 2. PIT 覆盖率复核（不发网络请求）
py scripts/build_pit_snapshot.py --validate-only --pit data/pit/convertible_pit.parquet --universe-file data/universe_cb_pit.csv --start 2018-01-01 --end 2026-08-08

# 3. 从 raw 缓存离线重组 PIT（iFinD 不稳定时的重建路径）
py scripts/build_pit_snapshot.py --assemble-only --universe-file data/universe_cb_pit.csv --pit data/pit/convertible_pit.parquet

# 4. 复跑 Walk-Forward（结果应可复现，快照 SHA-256 见上表）
py scripts/run_walk_forward.py --snapshot output/feeds_snapshot.pkl --start 2020-01-01 --end 2026-08-08 --train-years 3 --test-months 6 --grid lookback=60,90 --grid top_k=2,3 --grid ma_filter=200 --output output/walk_forward_etf_2020_2026.csv
```

### 新会话开场提示（可直接粘贴）

```text
继续 d:\daily_stock_analysis\quant_research 的低门槛量化项目。
先读 quant_research/HANDOFF.md（4.5 节为最新方向）与 daily_stock_analysis/docs/quant_research_architecture_research.md。
当前状态：252 tests passed；六层架构重构完成（core 包 / Runner / 实验注册 / 两段式转债回测）；
双低因子 RankIC 显著但换手 52x 成本吞噬，下一步按 .scratch/project-future-plan-20260831.md
执行预注册假设 H1（rank-buffer 降换手），2026-08-09 后数据为冻结 holdout。
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

# 数据源矩阵（Data Source Matrix）
> 关联文档：`docs/strategy-compare-deck.md`（运维真源）

> 2026-10-03 建立并实测。对比盘与主流程的数据源全景——每个源：能力/状态/限制/用途。
> 原则：任何单源故障不得拖垮流程（数据链自动降级）；阈值信号**当前为单源+保守回落**（多源交叉验证尚未实施，见 §六 纪律第 1 条）。

## 一、实时行情（优先级链：`REALTIME_SOURCE_PRIORITY`）

| 源 | 优先级 | 状态 | 限制 | 实测 |
|---|---|---|---|---|
| **TickFlow** | **第1（用户配置最高）** | ✅ 主力 | 官方口径（Free/A 股）：日线 **1标的/次** 10/min（周期 1d/1w/1M/1Q/1Y）；实时 **5标的/次** 10/min。上列限额为官方公告值，本仓仅实测"实时/K线可取"与接口可用性，**未逐项实测限频**；fetcher 本地 BATCH_SIZE=100 为并发分批非 API 批量 | 实时/K线实测成功；`get_main_indices` ✅ |
| tushare | P-1（日频/实时均高优先） | ✅ 主力 | 官方 15000 积分档 **500 次/分钟**——本仓仅在**已验证端点子集**（§二 15 个接口）下使用，不推广到全部端点；daily 单次 6000 行需分页/分日 | 多接口实测 |
| 腾讯直连 | 链内+审计直连 | ✅ | 无 | 全市场 |
| 东财 efinance/akshare | 链内 | ⚠ 不稳定 | 盘后/晚间频繁 RemoteDisconnected | 降级链正常 |
| 新浪/PyTDX/Baostock/Yfinance | 链内兜底 | ✅ | 各自限制 | 降级链正常 |

**TickFlow 独有接口**（2026-10-03 实测）：`get_main_indices`（主要指数完整行情）✅；`get_market_stats`/`get_sector_rankings` 返回 None（fail-open，待查）。

## 二、tushare 15000 积分接口（15/16 实测可用）

| 接口 | 用途 | 已落地 |
|---|---|---|
| daily_basic | 市值/估值/换手（K/M 槽选股、快照补列） | ✅ |
| stock_basic / daily / index_daily / index_dailybasic | 基础数据、PE 分位总闸（10年PE） | ✅ |
| moneyflow_hsgt | 北向净买入（L 槽信号） | ✅ |
| cb_daily / forecast / stk_holdernumber / fund_div | E2/I/M 槽核心 | ✅ |
| **limit_list_d** | 全市场跌停家数（熔断第三信号） | ✅ |
| **cyq_perf / cyq_chips** | 真·筹码获利比例（M 槽二次验证） | ✅ |
| moneyflow | 个股资金流（J 崩刀过滤） | 清单 |
| income / sw_daily / margin / top_list / block_trade / hk_hold / repurchase / dividend | 排雷/行业/情绪/事件 | 备用 |
| report_rc | 研报评级 | ⚠ 额度 10 次/天 |
| tracker | 一致预期 | ❌ 接口名无效 |

## 三、akshare（独立补充源）

| 接口 | 状态 | 用途 |
|---|---|---|
| stock_zh_a_spot_em | ⚠ 不稳定（晚间频繁断连） | 全市场快照（降级→新浪，缺列由 tushare 补） |
| fund_etf_spot_em | ⚠ 不稳定 | ETF 快照 |
| stock_notice_report | ✅ | 全市场公告流（X 事件扫描核心） |
| stock_zt_pool_dtgc_em | ✅ | 跌停池（9 行/日，与 limit_list_d 交叉验证） |
| stock_margin_sse | ✅ | 两融明细 |
| stock_index_pe_lg | ⚠ 仅 258 行 | PE 分位兜底（主源 tushare） |
| stock_news_em | ❌ 参数/解析异常 | 个股新闻（观察，待换接口） |

## 四、baostock（交叉验证源）

- ✅ 日线（复权）稳定；Sequoia-X 主数据源
- **audit 多源一致性检查**：baostock vs 腾讯 同标的收盘偏差 ≤0.5%（实测 4.432=4.432 PASS）

## 五、限频对策略的影响（官方口径 2026-10-03）

- **tushare 官方 500 次/分**（已验证端点子集）：月度窗的全市场循环（I=40次/J=21次/M=2次）无压力；daily 6000 行上限靠分日调用绕开
- **TickFlow 官方 10/min**（本仓未逐项实测限频）：实时批量≤5 → 18 槽全量预热需 4 批；**周/月/季/年线**接口可用于 G/H 引擎降拉取量（月线 450 天≈15 根，待预注册后启用）
- 两者限频独立，互不挤占——实时走 TickFlow、历史走 tushare 是最优分工

## 六、纪律

1. 阈值信号（熔断/PE分位/E2）**双源交叉验证未实施**——当前为单源+保守回落：数据失败时不动仓位（E2 缺数据保持仓位、熔断信号缺失不触发、PE分位走乐咕兜底），禁止把缺数据当作有效触发
2. 单源限速（tickflow 10/min、report_rc 10/天、tushare 积分）——调用前查缓存，禁止无谓重复拉取
3. 新数据源接入必须先进本矩阵登记+实测，再写代码

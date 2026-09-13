# 优化修改方案 v2（2026-09-12）

> 本文件是对"被忽略处与最不放心处"的深调研结论与修改方案，取代并整合 FUTURE-PLAN-REV1 与 SAFETY-AND-EVIDENCE-REVISION 两文档中已被本方验证/修正的部分。
> 方法：四条深研线全部**本地可复现实证**（不是推断）+ 行业一手标准（FIX 5.0 / QuantConnect / SQLite / Alpaca / Backtrader / Fowler）；取证失败的外部源一律不引用。
> 本轮已顺手修复的 R0（观察模式等）归入方案第一批，此处不再重复展开。

---

## 第一部分：实证发现汇总（每条都带可复现证据）

### 发现 1：同一笔成交重复回填 = 真实账本双记账【已实证】

**实验**：隔离标的 600519，复刻 `fill.py` 现行调用方式（不传 trade_uid）连续回填两次"100 股 @ 10 元"。
**结果**：账本出现 **2 行**（id 7、8）。实验数据已清理。
**根因**：`fill.py:99` 调 `record_trade` 时没传 `trade_uid/dedup_hash`；底层唯一约束形同虚设。
**后果**：持仓虚增、现金虚减、对账永不准。飞书消息重发/用户重复输入都会触发。

### 发现 2：通知失败被记为成功【已实证】

**实验**：mock 通知服务返回 `success=False`（不抛异常）跑 `run_daily_digest`。
**结果**：`digest_sent` 标记**照写**。同日不再重试，当日摘要静默丢失。
**同病**：`daily_pipeline_service.py` 的 `send()` 返回值同样被忽略——管线 ok=true 不代表你收到了操作单。
**根因**：通知层用返回值表达失败（no_channel/all_failed），上层只捕获异常。

### 发现 3：E1 实验的 premium 因子被前视污染到不可用【已实证】

**代码**：`e1_composite_backtest.py:54` 把每只券**整个窗口最后一天**的 premium_rate 映射到所有历史调仓日。
**量化**：中段（2021-06-30）真实 premium vs 窗口末值——中位绝对偏差 **17.49 个百分点**、68% 的券偏差 >10pp、排序 Spearman 相关仅 **0.146**（即"低溢价臂"的排序基本是未来信息）。
**结论**：E1 的 low_premium 和 composite 双臂结果是前视污染产物；double_low 臂的 premium 部分同污。**E1 "fail" 判定不能作为"因子已证伪"的依据**——它只证明"被污染的实现失败了"。

### 发现 4：E2 静态 JSON 与动态重算偏差 6.3 个百分点【已实证】

- 静态 `e2_valuation_timing.json` 的 `current_state.percentile=0.951` 无 as_of 标注（实为 2026-08-08 窗口末值）；
- 按 pit_end=2026-09-10 动态重算 = **0.888**；
- 0.951 vs 0.888 跨在 0.80 门槛同侧（本次未误判），但 6.3pp 偏差意味着**某个时点会跨线误判仓位**。健康视图读静态 JSON、操作单读动态重算——两处口径不一。

### 发现 5：负 IC 结论方向性错误【代码复核】

`double_low_verdict.py:76` 用未反号的 `price + premium_rate` 算 IC；双低值越低越好 → **负 IC 正是因子有效的方向**。此前文档"深负→因子反转失效"的解释方向反了。2025 后 IC 归零仍值得研究，但"双低死亡"叙事的证据链需要重建。

### 发现 6：账本仍有可疑残留

portfolio_trades 中存在 `002458`（500 股买入 8.57 + 500 股卖出 8.75，2026-07/08）——非当前 STOCK_LIST、非用户申报持仓。来源不明（疑似更早测试残留），**本轮不删**（无确凿证据），列入待用户确认清单。

### 发现 7：外部一手标准已取证（此前部分缺失）

- **FIX 5.0 SP2 OrdStatus**（onixs.biz/fix-dictionary）：行业订单状态全集 = New / Partially filled / Filled / Done for day / Canceled / Pending Cancel / Stopped / Rejected / Suspended / Pending New / Expired / Calculated / Pending Replace…——**"Done for day"（当日结束≠终态）和 Pending Cancel（撤单待确认）是我们状态机缺的两个关键语义**。
- **QuantConnect 文档**：部分成交后取消剩余是标准实践（"avoid trade on non-updated information"）。
- **SQLite transactional**：事务保证只覆盖库内——跨 JSON 文件的双写（操作单+账本）**没有任何原子性**，这是发现 1 之外的第二层风险。
- **Backtrader 成交规则**：限价单成交需后续 bar 价格匹配——模拟账本"生成即全量成交"违反基本时序。

---

## 第二部分：优化修改方案（按优先级，每项带验收反例）

### 批次 A：账本与回填的完整性（1 周，最高优先）

| 改动 | 验收反例 |
|---|---|
| A1 `_do_fill` 传 `trade_uid = f"{sheet_id}:{symbol}:{side}:{trade_date}"`；无单回填用内容哈希 | 同一笔回填两次 → 第二次收到"已回填过"且账本仍 1 行 |
| A2 真实账本写失败时回滚操作单标记（先写账本成功再标单；或记 outbox 待补偿） | mock 账本抛错 → 操作单无 filled 标记 |
| A3 部分成交：fills 按 `(symbol, side)` 累计数量而非覆盖；累计 < 计划量时单据态 = partially_filled（FIX: "1"） | 100 张回填 30 张 → 单据显示"已成交 30/100，部分成交"；再回填 70 → filled |
| A4 `/撤单 代码 [原因]` 命令：pending → cancelled；已成交量保留 | 撤单后再回填剩余量 → 拒绝并提示已撤 |
| A5 量价合理性：回填价偏离限价 ±5% 或量超计划剩余 → 要求"确认"参数 | 超限回填首次被拦 |
| A6 日终过期：pending 单跨日自动标 expired（FIX: "C"），留档可查 | 昨日 pending 今日查看 = expired，不可回填 |

### 批次 B：通知成功语义（2-3 天）

| 改动 | 验收反例 |
|---|---|
| B1 digest/pipeline 检查 `send_with_results` 的 `success` 与真实渠道回执；失败不写 sent 标记、不置 ok=true | mock success=False → 无 sent 标记、pipeline detail="通知未送达" |
| B2 pipeline_status 拆字段：`data_ok / decision_ok / notify_ok` | 操作单生成但通知失败 → ok=true 但 notify_ok=false，Web 横幅黄 |
| B3 通知重试：失败进次日首轮重试队列（同 logical_id 去重） | 人工修复渠道后次日补发且不重复 |

### 批次 C：研究证据链重建（2-3 周，与 A/B 并行）

| 改动 | 验收 |
|---|---|
| C1 E1 重写：premium 按 as_of 逐日对齐（PIT 有每日列，纯实现缺陷）；年化按真实采样频率；现金守恒；费用口径统一 | 全臂无未来信息（决策日 t 只见 ≤t 数据）；四臂结果可复现 |
| C2 E2 修复：current_state 加 as_of；健康视图弃用静态 JSON 改动态重算（与操作单同源）；补"中位数代理 vs 可投资组合"差异说明 | /健康 与 /操作单 显示同一分位值；分位线跨线时两者同时变 |
| C3 双低 verdict 修正：IC 反号或改用"低值组收益-高值组收益"多空价差口径；重写结论 | 新报告不再出现"负 IC=失效"表述 |
| C4 旧结论文档全部加 superseded 标注（不删除，保留审计轨迹） | CB-RESEARCH-PLAN/E1/E2 相关段落标注"实现缺陷，结论待重验" |
| C5 模拟账本污染处置：无主 sheet 条目标 unknown 隔离区（不删）；123089 历史条目保留但对比口径排除已退市标的；执行率分母 = 已审批可执行单 | /对比 明示"隔离 N 条不可追溯记录" |

### 批次 D：状态机对齐 FIX 标准（批次 A 后，1 周）

- 订单状态集对齐 FIX OrdStatus 子集：`new → partially_filled → filled / canceled / expired`，加 `pending_cancel`（撤单已发未确认）；
- "Done for day"语义：日终不终态化当日订单（次日可能继续），只标 `done_for_day`；
- 每次状态转移写审计行（who/when/why），进决策快照。

### 批次 E：暂停服务 fail-closed 收尾（1 天）

- `trading_halt_service` 读文件失败时 `is_trading_halted()` 返回 True（unknown=阻断）——当前 order_sheet 已兜底，服务层语义统一；
- `/恢复交易` 只解除人工暂停；数据/现金闸门独立生效（已实现，补测试锁定）。

---

## 第三部分：执行顺序与停止条件

```
本周      批次 A（账本完整性）+ B（通知语义）——先让"记录"可信
下周      批次 C1/C2（E1/E2 修复）——让"研究"可信
第 3 周   批次 C3-C5 + D（状态机）+ E
第 4 周起 影子期重新计时（此前 0% 执行率基线作废）
```

**影子期重新计时的理由**：账本双记账 + 通知假成功 + 模拟账本污染三重失真下，任何执行率/滑点统计都无意义。

**停止条件（新增）**：
- 重复回填再次入账两行（幂等失效）；
- 通知失败再次被标成功；
- E1 重跑后因子结论与旧结论方向相反且无法解释（说明还有未发现的口径问题）；
- 002458 残留经用户确认非本人操作（说明账本有未知写入路径）。

**需要你确认的一件事**：portfolio_trades 里 2026 年 7-8 月的 002458（买 500 股 @8.57、卖 500 股 @8.75）是不是你的真实交易？不是的话我来清理并追查写入路径。

## 第四部分：取证来源

- FIX 5.0 SP2 OrdStatus 枚举：https://www.onixs.biz/fix-dictionary/5.0.SP2/tagNum_39.html （本轮成功取证）
- QuantConnect 订单事件/部分成交实践：https://www.quantconnect.com/docs/v2/writing-algorithms/trading-and-orders/order-events
- SQLite 事务边界：https://www.sqlite.org/transactional.html
- Alpaca 订单生命周期：https://docs.alpaca.markets/docs/trading/orders/
- Backtrader 限价成交规则：https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/
- Fowler 熔断器：https://martinfowler.com/bliki/CircuitBreaker.html
- 本地实证：600519 双记账实验（已清理）、digest mock 失败实验、E1 premium 偏差量化（17.49pp/0.146）、E2 分位偏差量化（6.3pp）
- 取证失败不引用：Knight Capital SEC 原文 PDF、巴林银行页、GitHub API（限流）

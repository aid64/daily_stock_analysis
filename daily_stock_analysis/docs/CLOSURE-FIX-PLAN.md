# 闭环修复计划（GLM-5.3F 执行指导）

> 版本：v1.0（2026-09-07 定稿）
> 上游依据：全仓端到端诚实审计（2026-09-07，12 条断裂链路）
> 效力：本文件是本轮修复的唯一真源；与 EXECUTION-BLUEPRINT.md（工程蓝图，①-⑤ 已闭环）和 CB-RESEARCH-PLAN.md（研究计划，D0-D3/E1-E3 已闭环）并列。
> 纪律：每条修复完成后必须**端到端验证**（跑通 ≠ 代码存在）；未验证的项禁止标记完成。

---

## 〇、审计结论与修复目标

审计发现：半自动交易助手核心闭环四环断三环——

| 环节 | 断裂状态 | 本计划修复后 |
| --- | --- | --- |
| 每日自动分析 | 只分析 000899（漏 002475） | F1 修复后覆盖全部持仓 |
| 信号自动产出 | 纯手动，09-09 过期 | F4 修复后每日自动刷新 |
| 操作单自动生成 | 纯手动，挂在无人能调的命令上 | F4 修复后每日推送 |
| 手机发命令 | 完全无接收平台 | F2 修复后飞书双向 |
| 下单后回填 | mark_order_filled 零调用点 | F3 修复后 bot 命令 + API |
| E2 择时 | 硬编码冻结在 2026-08-08 | F5 修复后用 pit_end 动态计算 |

**修复完成的标准**：用户手机上收到飞书推送的操作单 → 用户在飞书发 `/持仓登记` 回填 → `/对比` 看到真实 vs 模拟对照 → 每日 18:00 企微收到一条摘要。全程不需要碰电脑。

---

## 一、平台决定：飞书（Stream 模式）

理由：feishu_stream.py 763 行最完整；个人版免费建应用即可；企微继续做推送兜底（双通道冗余）；dsa-web 文档主推。

**用户需提供**（在飞书开放平台 https://open.feishu.cn 创建企业自建应用后获取）：
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

应用需开通：机器人能力 + 消息接收（Stream 模式）。

---

## 二、修复任务清单（按依赖顺序执行）

### F1 STOCK_LIST 同步持仓（5 分钟，一行配置 + 根治代码）

**立即修复**：`.env` 的 `STOCK_LIST` 改为 `000899,002475`。

**根治（可选，本轮不做）**：在 runtime_scheduler 注册一个持仓→STOCK_LIST 同步任务（读 PortfolioService 非零持仓 → 刷新 STOCK_LIST env），消除两套体系的静默脱节。本轮先手动同步，根治留给后续。

**验收**：`py main.py --dry-run --stocks 000899,002475` 确认两只都进入分析队列。

### F2 飞书 Stream 双向命令激活（30 分钟）

前置：用户已提供 FEISHU_APP_ID + FEISHU_APP_SECRET。

- `.env` 追加：`FEISHU_STREAM_ENABLED=true` + 凭据两行
- 验证 `bot/platforms/feishu_stream.py` 的启动入口（`main.py:1335-1365` 附近）在 `--serve` 模式下被调用
- 确认全部 16 个 bot 命令在飞书可用：help/status/analyze/market/batch/ask/chat/research/strategies/history/portfolio/portfolio_record/portfolio_clear/ordersheet/compare/health

**验收**：飞书发 `/健康` 收到五要素回复；发 `/持仓` 看到当前两只持仓。

### F3 回填成交入口（1 小时，本轮核心新代码）

**bot 命令**：新增 `/成交回填` 命令（bot/commands/fill.py）：

```
/成交回填 000899 卖出 100 11.50    → 标记操作单中 000899 卖出腿已成交
/成交回填 002475 买入 100 56.50    → 标记买入腿已成交
```

逻辑：读当日操作单（`load_latest_sheet`）→ 找到匹配 symbol+direction 的 OrderLine → 调用 `mark_order_filled(sheet, symbol, actual_price, actual_quantity)` → 同时调 `PortfolioService.record_trade` 写真实账本 → 回复确认。

**API 端点**：POST `/api/v1/assistant/order-sheets/fills`（body: `{symbol, direction, actual_price, actual_quantity?}`）→ 同上逻辑。供 Web 端后续使用。

**验收**：飞书发 `/成交回填` 后：① 操作单 JSON 状态从 pending→filled（或部分 filled）② `PortfolioService.list_trade_events` 出现该笔 ③ `/对比` 显示新回填的条目。

### F4 每日自动化任务链注册（1 小时）

在 `runtime_scheduler.py` 的 `_current_background_tasks` 新增一个任务 `daily_pipeline`：

- **触发**：每日 09:00（开盘后）执行一次；实现方式参照 digest 的轮询模式（每 5 分钟检查，到点执行一次，当日标记防重跑）
- **串行执行**（子进程调 quant_research 侧脚本，遵守 domain.md 跨项目契约——不 import，走子进程）：
  1. `py quant_research/scripts/update_pit_tail.py --limit 50`（增量更新，限制数量避免超时）
  2. `py quant_research/scripts/build_pit_snapshot.py --assemble-only --start 2018`
  3. `py quant_research/scripts/run_paper_tracking.py --export-plan`（刷新 current_plan.json）
  4. dsa 侧调 `bot/commands/order_sheet.py` 的 `_build_sheet_text()` 生成操作单 → 落盘 + 模拟账本记录
  5. 推送操作单文本到通知渠道（`NotificationService.send`，route_type=report）
- **失败处理**：任一步骤失败 → 推送一条 system_error 告警（"自动化管线某步骤失败：XXX"），不中断其他后台任务

同时注册 `market_snapshot` 任务：
- **触发**：每日 15:30（收盘后）
- **执行**：`market_scan_service.fetch_and_store_snapshot()`（幂等，当日已存在跳过）
- **失败**：静默降级（新浪 fallback 已内置），不推送告警

**验收**：启动 `--serve` 后等到 09:00（或临时改 digest 时间模式做 5 分钟内验证）→ 飞书/企微收到操作单推送 → `output/order_sheets/` 出现当日新 JSON → `current_plan.json` 的 `generated_at` 更新。

### F5 E2 择时信号解冻（15 分钟）

`bot/commands/order_sheet.py:103` 的硬编码 `pd.Timestamp("2026-08-08")` 改为读 `current_plan.json` 的 `pit_end` 字段（动态跟随 PIT 增量）：

```python
# 改前
_pit = _pit[_pit["as_of"] <= pd.Timestamp("2026-08-08")]
# 改后
pit_end = plan.get("pit_end")  # 从已加载的 plan 取
_pit = _pit[_pit["as_of"] <= pd.Timestamp(pit_end)]
```

同时 `/健康` 的 `_timing_line()` 保持读静态 JSON（F4 的每日管线会刷新它——在 F4 步骤 3 后追加调用 `e2_valuation_timing.py` 的核心计算函数刷新 JSON）。

**验收**：`/操作单` 输出的分位数随 PIT 增量自动变化（不再是写死的 95.1%）。

### F6 幽灵配置与残留清理（15 分钟）

- `.env` 删除 `STRATEGY_SIGNAL_ENABLED=true` 段（代码无消费者）
- `.env` 删除 `ALPHASIFT_ENABLED=true`（同上，测试已断言移除）
- `src/config.py:3738-3746` 的"NOTIFICATION_DAILY_DIGEST_ENABLED 当前为预留配置"过时告警段删除（实现已落地）
- `bot/__init__.py:20-24` 文档中不存在的 webhook 路由描述删除
- `.env` 打开三个监控：`EVENT_ARBITRAGE_MONITOR_ENABLED=true`、`BOND_NEW_ISSUE_MONITOR_ENABLED=true`、`ETF_PREMIUM_MONITOR_ENABLED=true`
- `simulation_ledger.py:27-29` 的 `parents[1]` 改 `parents[2]` + 迁移 `src/output/simulation_ledger/ledger.json` 合并到 `output/simulation_ledger/ledger.json`
- `cb_signal_bridge.py` / `health.py` / `assistant.py` / `order_sheet.py` 四处的兄弟目录路径收敛为一个共享常量（放 `cb_signal_bridge.py`，其余 import），并支持 `CB_PLAN_PATH` env 覆盖

**验收**：`py -m pytest -m "not network" -q` 全过；`grep -rn "STRATEGY_SIGNAL_ENABLED\|ALPHASIFT_ENABLED" src/` 零命中。

### F7 通知路由配置（5 分钟）

`.env` 追加：
```
NOTIFICATION_REPORT_CHANNELS=wechat,feishu
NOTIFICATION_ALERT_CHANNELS=wechat,feishu
```

（具体渠道名以 notification_routing.py 的合法值为准，执行时先核对再写入。）

---

## 三、执行顺序与依赖

```
F6 清理（先做，避免脏状态干扰后续验证）
 → F1 STOCK_LIST（立即见效）
 → F5 E2 解冻（F4 依赖它）
 → F3 回填入口（独立，可与 F4 并行）
 → F4 每日管线（依赖 F5；最复杂）
 → F2 飞书激活（依赖用户提供凭据——执行者先做 F1/F5/F3/F4/F6/F7，
    凭据到位后最后接 F2）
 → F7 通知路由（随时，最后收尾）
```

---

## 四、GLM-5.3F 执行注意事项（本轮特有）

1. **每条修复的验收不是"代码编译过"而是"端到端跑通"**——F3 要在飞书或本地 dispatcher 真正调一次 `/成交回填` 看到 JSON 状态变化；F4 要真的等到/触发一次管线执行。
2. **跨项目契约**：F4 步骤 1-3 必须走子进程（`subprocess.run`），不能 import quant_research——两个项目独立运行（domain.md）。
3. **tushare 频率**：F4 步骤 1 的 update_pit_tail 用东财接口不走 tushare（tushare 只用于条款快照 D0），无频率风险。
4. **测试隔离**：F3 的测试必须用 `isolated_db` fixture（tests/conftest.py），绝不能碰 `data/stock_analysis.db` 真库。
5. **操作单幂等**：F4 步骤 4 生成操作单前先 `load_latest_sheet(today)`——当日已有 pending 单则跳过重新生成，只做推送。
6. **模拟账本路径迁移**（F6）：迁移时保留两份历史（合并 entries 而不是覆盖），以 `output/` 版本为准。
7. **通用注意事项**：见 EXECUTION-BLUEPRINT.md 第九节（py 启动器/测试禁并发/rename 黑洞/真库隔离/CRLF/commit 后 stat 核对）。
8. **CHANGELOG**：每条 F 项完成后加一行（扁平格式，`[修复]`/`[新功能]`/`[chore]`）。
9. **提交粒度**：每个 F 项独立 commit（F6 清理可以和 F1 合并），英文 message，无 Co-Authored-By。
10. **验收后回填本文件状态表**（下方），未验证的项禁止标 ✅。

---

## 五、状态表（执行后回填）

| 任务 | 状态 | 验收证据 |
| --- | --- | --- |
| F1 STOCK_LIST 同步 | ⏳ | |
| F2 飞书 Stream 激活 | ⏳ 等凭据 | |
| F3 成交回填入口 | ⏳ | |
| F4 每日自动化管线 | ⏳ | |
| F5 E2 择时解冻 | ⏳ | |
| F6 幽灵配置清理 | ⏳ | |
| F7 通知路由配置 | ⏳ | |

---

## 六、用户侧待办（需要你做的）

| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | **提供飞书凭据**（F2 的前置） | 到 https://open.feishu.cn → 创建企业自建应用 → 添加机器人能力 → 开启 Stream 模式 → 把 App ID 和 App Secret 发给我 |
| 2 | 确认每日管线时间 | 默认 09:00 跑管线 / 15:30 拍快照 / 18:00 发摘要——要改时间告诉我 |
| 3 | F1 完成后核对 .env | 确认 STOCK_LIST=000899,002475 与你的实际持仓一致 |

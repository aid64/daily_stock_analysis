# 闭环修复计划 v2（GLM-5.3F 执行指导）

> 版本：v2.0（2026-09-07 重写——基于用户核心反馈："我对电脑端完全看不到项目到底运行了还是没运行感到焦虑"）
> 替代 v1.0（F1-F7 已部分完成：F2 飞书双向已验证 ✓ / F1 STOCK_LIST 已修 ✓）
> 效力：本轮修复的唯一真源。纪律不变：**每条完成必须端到端验证**，未验证禁止标 ✅。

---

## 〇、用户核心痛点（本轮设计的出发点）

1. **看不到运行状态**——终端窗口一关服务就停，不知道系统是活着还是死了
2. **不知道下一步该干嘛**——启动方式对非开发者不友好（要 cd 到目录、敲命令）
3. **不确定消息会不会来**——推送和 bot 是"黑盒"，没有"系统活着"的可确认信号

设计目标：**双击一个图标 → 浏览器打开一个页面 → 页面上明明白白写着"系统运行中"** → 飞书能收发消息。零终端、零命令行。

---

## 一、总体架构：服务常驻 + 单页可视化

```
双击 启动助手.vbs（桌面快捷方式）
  → 隐藏窗口启动 py main.py --serve（后台常驻，无黑框）
  → 等待服务就绪（轮询 /api/v1/health 直到 200）
  → 自动打开浏览器 http://127.0.0.1:8000/
  → Web 首页顶部显示【系统运行中 ✓ 已运行 X 小时】
  → 飞书机器人同时在线（同一服务进程内）
```

配套：
- 桌面第二个图标 `停止助手.vbs`：优雅停止服务
- Web 首页加"运行状态"横幅（服务在跑 = 绿灯 + 运行时长；用户打开页面就能确认）
- 每日 08:55 系统自动给飞书发一条"早安报"——用户在飞书上看到它 = 系统活着（心跳确认）

---

## 二、任务清单（G 系列，按执行顺序）

### G1 一键启动/停止脚本（30 分钟）★★★ 本轮核心

**新建 `scripts/start-assistant.ps1`**（PowerShell，被 VBS 调用）：
1. 检查端口 8000 是否已被占用（`Test-NetConnection` 或 socket 探测）——已占用则直接打开浏览器（服务已在跑，幂等）
2. 未占用 → `Start-Process py -ArgumentList "main.py","--serve" -WorkingDirectory <项目目录> -WindowStyle Hidden`（无窗口后台启动）
3. 轮询 `http://127.0.0.1:8000/api/v1/health` 最多 90 秒直到 200（LLM 初始化慢，留足时间）
4. 成功 → `Start-Process "http://127.0.0.1:8000/"`（打开浏览器）
5. 失败 → 弹 Windows 消息框（`[System.Windows.Forms.MessageBox]`）提示"启动失败，查看 logs/assistant.log"

**新建 `scripts/stop-assistant.ps1`**：
- 找到监听 8000 端口的进程 PID（`Get-NetTCPConnection -LocalPort 8000`）→ `Stop-Process -Id <pid> -Force`
- 弹确认框"助手已停止"

**新建桌面入口 `启动助手.vbs` / `停止助手.vbs`**（放在项目根，用户可发送到桌面快捷方式）：
- VBS 隐藏 PowerShell 窗口执行对应 ps1（避免闪黑框）

**日志落盘**：服务 stdout/stderr 重定向到 `logs/assistant.log`（启动脚本里 `-RedirectStandardOutput`）。

**验收**：
- 双击 `启动助手.vbs` → 无黑框 → ~30 秒后浏览器自动打开 Web 首页 → 页面能访问
- 再双击一次 → 不重复启动，直接打开浏览器（幂等）
- 双击 `停止助手.vbs` → 端口释放，飞书机器人离线
- `logs/assistant.log` 有内容

### G2 Web 首页运行状态横幅（30 分钟）

**后端**：`api/v1/endpoints/health.py` 或新建轻量端点 `GET /api/v1/runtime/status` 返回：
- `running: true` + `started_at`（服务进程启动时间，写进 app.state）+ `uptime_seconds`
- `feishu_stream_alive`（`get_feishu_stream_client()` 非 None 且线程存活）
- `last_pipeline_run`（G4 的 daily_pipeline 最近一次执行时间与结果）
- `last_digest_sent`（读 notification bus 的 digest_sent 标记）

**前端**：`HomePage.tsx` 顶部加横幅组件（复用现有 Card/Badge）：
- 绿灯 `系统运行中 · 已运行 2h 15m · 飞书在线 ✓`
- 红灯 `服务异常`（API 不通时前端自然显示错误态）
- 附"上次管线 08:57 ✓ / 上次摘要 18:00 ✓"两行小字

**验收**：启动服务 → 打开首页 → 看到绿灯和运行时长；`stop-assistant` 后刷新页面 → 无法访问（红灯态）。

### G3 飞书早安报心跳（20 分钟）

在 `runtime_scheduler` 注册后台任务 `morning_heartbeat`：
- 触发：每日 08:55（轮询模式，同 digest——每 5 分钟检查，到点发一次，当日标记防重发）
- 内容（一条消息，直接走 NotificationService 静态渠道 = 企微 + 飞书若配了静态推送）：
  ```
  早安 · 系统运行中 ✓
  运行时长 X 小时 · 飞书在线 ✓
  今日操作单已生成（或：今日无操作）
  估值分位 95.1% → 建议减半仓
  ```
- **实现注意**：推送走 NotificationService（企微 webhook），确保用户至少在一个渠道收到心跳。同时可调用飞书 stream 客户端直接发消息给创建者（如果 feishu stream 有主动发消息能力——查 feishu_stream.py 有无 send 方法，没有就只走企微）。

**验收**：临时把触发时间改为 2 分钟后 → 等待 → 企微/飞书收到心跳消息 → 恢复 08:55。

### G4 每日自动化管线（从 v1 的 F4 移植，1 小时）

在 `runtime_scheduler` 注册 `daily_pipeline` 任务（09:00 触发，轮询模式）：
1. 子进程：`py quant_research/scripts/update_pit_tail.py --limit 50`
2. 子进程：`py quant_research/scripts/build_pit_snapshot.py --assemble-only --start 2018`
3. 子进程：`py quant_research/scripts/run_paper_tracking.py --export-plan`
4. 内联：生成操作单（复用 `bot/commands/order_sheet.py` 的 `_build_sheet_text()` 逻辑——抽成服务层函数供 bot 和管线共用）+ 记模拟账本
5. 推送操作单全文（NotificationService，route_type=report）
6. 刷新 E2 择时 JSON（内联调 `e2_valuation_timing.py` 的核心计算，或子进程重跑脚本）
7. 任一步失败 → 推 system_error 告警（"管线步骤 N 失败：XXX"），不中断其他任务
8. 每步结果写 `output/pipeline_status.json`（G2 的 last_pipeline_run 数据源）

**执行时间与 G3 心跳配合**：心跳 08:55（说"今日操作单生成中"）→ 管线 09:00 → 操作单推送 ~09:05。

**验收**：临时改触发时间为 2 分钟后 → 等待 → 收到操作单推送 + `output/order_sheets/` 出现当日 JSON + `pipeline_status.json` 记录成功 → 恢复 09:00。

### G5 成交回填入口（从 v1 的 F3 移植，1 小时）

**bot 命令** `/成交回填 代码 方向 数量 价格`：
- 读当日操作单 → 匹配 symbol+direction → `mark_order_filled()` → 同时 `PortfolioService.record_trade()` 写真实账本 → 回复确认
- 无匹配腿时给出明确错误（"操作单中没有 000899 的买入腿"）

**API 端点** `POST /api/v1/assistant/order-sheets/fills`（同逻辑，供 Web 用）。

**验收**：飞书发 `/成交回填 002475 买入 100 56.50` → 操作单 JSON 状态变化 + PortfolioService 出现该笔 + `/对比` 显示新条目。

### G6 E2 择时解冻 + 幽灵配置清理（从 v1 的 F5/F6 移植，30 分钟）

- `order_sheet.py:103` 硬编码 `2026-08-08` → 改读 `current_plan.json` 的 `pit_end`
- `.env` 删 `STRATEGY_SIGNAL_ENABLED` / `ALPHASIFT_ENABLED` 幽灵段
- `config.py:3738-3746` 过时的"digest 预留配置"告警删除
- `bot/__init__.py:20-24` 不存在的 webhook 路由文档删除
- `.env` 打开三监控（EVENT_ARBITRAGE / BOND_NEW_ISSUE / ETF_PREMIUM = true）
- `simulation_ledger.py` `parents[1]`→`parents[2]` + 合并迁移 `src/output/.../ledger.json` 到 `output/.../ledger.json`
- `cb_signal_bridge` 路径常量收敛 + 支持 `CB_PLAN_PATH` env 覆盖（4 处重复定义合一）

**验收**：`py -m pytest -m "not network" -q` 全过；`/操作单` 的分位数随 PIT 动态变化。

### G7 通知路由 + 收尾（15 分钟）

- `.env` 配 `NOTIFICATION_REPORT_CHANNELS` / `NOTIFICATION_ALERT_CHANNELS`（先核对 notification_routing.py 合法渠道名）
- CHANGELOG 记录全部 G 项
- HANDOFF.md / CLOSURE-FIX-PLAN 状态表回填
- `.gitignore` 加 `logs/`

---

## 三、执行顺序与依赖

```
G6 清理（先做，避免脏状态）
 → G1 一键启动（核心体验，立即解决焦虑）
 → G2 运行状态横幅（依赖服务可访问）
 → G4 每日管线（G2 显示其状态）
 → G3 早安心跳（依赖 G4 的管线状态输出）
 → G5 成交回填（独立）
 → G7 收尾
```

G1 完成后用户即可双击图标使用——这是最快消除焦虑的一步，优先做。

---

## 四、GLM-5.3F 执行注意事项（v2 特有）

1. **PowerShell/VBS 在 Git Bash 里写文件注意编码**——ps1 用 UTF-8 with BOM（含中文注释时必须），VBS 用 GBK/ANSI（wscript 不认 UTF-8 无 BOM）。写完用 `powershell -File` 实测执行。
2. **端口探测用 `Get-NetTCPConnection -LocalPort 8000 -State Listen`**（Windows 专用，比 Test-NetConnection 快且无输出噪声）。
3. **`--serve` 的 host/port**：确认 main.py --serve 默认绑定（127.0.0.1:8000 还是 0.0.0.0）——G1/G2 用同一地址，别猜，读代码确认。如果默认端口不是 8000，全部文档和脚本跟着改。
4. **后台启动进程的生命周期**：`Start-Process` 启动的 py 进程独立于 PowerShell 窗口存活（正确）；但**不要用 `&` 后台 job**（会随 PowerShell 退出而死）。
5. **G4 跨项目子进程**：`subprocess.run([sys.executable, ...])` 用绝对路径的脚本路径；cwd 设 quant_research/（脚本内部有相对路径假设）；timeout=600 秒（update_pit_tail 拉 50 只可能慢）。
6. **G3 心跳的飞书直发**：先查 feishu_stream.py 有无主动发消息方法（grep send/reply）——只有收到消息后的 reply 的话，心跳只走企微（宁简单勿过度工程）。
7. **日志轮转**：assistant.log 简单按日切（启动脚本启动前 rename 昨日文件），不引 logging 框架轮转——服务内部日志已由自身管理。
8. **G2 的 started_at**：在 FastAPI lifespan 里 `app.state.started_at = time.time()`，端点从 app.state 读——不要依赖进程 PID 探测。
9. 通用纪律见 EXECUTION-BLUEPRINT.md 第九节；**commit 后 `git show --stat` 核对**（v1 犯过漏 add 错）。

---

## 五、状态表（执行后回填）

| 任务 | 状态 | 验收证据 |
| --- | --- | --- |
| G1 一键启动/停止 | ⏳ | |
| G2 运行状态横幅 | ⏳ | |
| G3 早安心跳 | ⏳ | |
| G4 每日管线 | ⏳ | |
| G5 成交回填 | ⏳ | |
| G6 解冻+清理 | ⏳ | |
| G7 路由+收尾 | ⏳ | |
| （v1 遗产）F1 STOCK_LIST | ✅ 已完成 | .env=000899,002475 |
| （v1 遗产）F2 飞书双向 | ✅ 已验证 | 用户实测 /健康 收到回复 |

---

## 六、用户侧零待办

v1 需要用户提供飞书凭据——已提供并验证通过。**本轮 G1-G7 全部由执行者完成，用户不需要做任何配置。** 唯一可选：把 `启动助手.vbs` 发送到桌面创建快捷方式（G1 验收时会演示）。

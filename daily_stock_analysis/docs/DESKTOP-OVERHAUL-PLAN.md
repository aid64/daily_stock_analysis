# 桌面端大修计划（GLM-5.3F 执行指导）

> 版本：v1.0（2026-09-07 定稿）
> 上游依据：桌面端代码勘察（main.js 2411 行函数结构 / 进程管理现状）+ CLOSURE-FIX-PLAN v2 的 G0 bug 发现 + 用户决策："桌面端彻底大修，通知渠道与桌面端配合，两个项目是一个整体"
> 效力：本文件是桌面端大修的唯一真源。与 CLOSURE-FIX-PLAN v2 的关系：**本计划吸收并替代 v2 的 G1**（一键启动）；G2-G7 的后端部分仍按 v2 执行，前端部分并入本计划。
> 纪律：每个任务端到端验证后才算完成；UI 变化需附截图（AGENTS.md 要求）。

---

## 〇、终局形态：桌面端 = 半自动交易助手的主界面

```
双击桌面图标
  → 应用窗口打开（单一主窗口，加载 Web 前端）
  → 后端自动拉起（现有 spawn 逻辑，修 G0 bug 后飞书同时在线）
  → 关闭窗口 → 缩到系统托盘继续运行（后端 + 飞书 + 定时任务全部活着）
  → 托盘图标 = 运行状态灯（绿=健康 / 黄=后端启动中 / 红=异常）
  → 双击托盘图标恢复窗口；右键菜单：打开主界面 / 立即跑管线 / 退出
  → 后端事件（操作单生成/告警）→ Windows 系统通知弹出 + 手机渠道（企微/飞书）同步推
  → 单实例锁：双击两次图标不会拉起两个后端
  → 开机自启（可选开关，默认关）
```

用户判断"系统活着"的三个信号：**任务栏/托盘有图标、窗口里绿灯横幅、Windows 通知弹出**。

---

## 一、现状盘点（勘察结论）

| 组件 | 现状 | 问题 |
| --- | --- | --- |
| 进程管理 | `spawn` + `waitForHealth` 轮询（main.js:1315,1134）✅ 基础好 | 后端崩溃无自动重启；退出时子进程树清理待验证 |
| 窗口 | 单主窗口 + loading/error 页（2090,2129）✅ | 关窗 = 默认退出（无托盘常驻）；无 `window-all-closed` 处理 |
| 托盘 | **无** Tray 代码 | 全部缺失 |
| 系统通知 | **无** Notification 调用 | 全部缺失 |
| 单实例锁 | **无** `requestSingleInstanceLock` | 双击两次 = 两个后端抢端口 |
| 自动更新 | electron-updater 完整（79-402 行）✅ | 保留不动 |
| 菜单 | 上游"分析产品"形态 | 助手三页（/assistant/*）无入口（但 Web 路由已有，桌面端加载同一前端=天然可见，只需导航里有——**Web 侧已有 SidebarNav，无需桌面端改**） |
| 构建残留 | dist/ 2.4GB + dist-v3.29/ 737MB | 纯垃圾，清理 |
| 后端 serve | 飞书 bot 不启动（G0 bug） | main.py:1609 分支未执行，需先修（前置依赖） |

**关键认知**：桌面端加载的就是 Web 前端（同一份 static/ 产物）——所以"助手页面无入口"实际是 Web 导航问题（已解决，SidebarNav 有三项），桌面端不需要单独改菜单。桌面端真正缺的是：**托盘常驻、系统通知、状态灯、进程守护、单实例**。

---

## 二、架构蓝图（main.js 模块化拆分）

现状 2411 行单文件混在一起。大修按模块拆到 `apps/dsa-desktop/src/` 下（Electron 标准做法）：

```
apps/dsa-desktop/
├── main.js                    # 入口：只做 app 生命周期 + 模块组装（目标 <300 行）
├── src/
│   ├── backend-manager.js     # 后端进程管理器（spawn/健康检查/崩溃重启/优雅退出）
│   ├── tray-controller.js     # 托盘控制器（图标状态灯/右键菜单/双击恢复）
│   ├── window-manager.js      # 主窗口管理（创建/loading/error/关闭到托盘）
│   ├── notification-bridge.js # 通知桥（SSE 订阅后端事件 → Windows 系统通知）
│   └── updater.js             # 自动更新（从 main.js 平移，逻辑不变）
├── assets/
│   ├── tray-green.png         # 托盘图标（16/32px，绿=健康）
│   ├── tray-yellow.png        # 黄=启动中
│   └── tray-red.png           # 红=异常
└── tests/                     # 现有 node --test 测试保留
```

各模块职责（边界清晰，单一职责）：

- **backend-manager**：`start()` / `stop()` / `getStatus()` / `onEvent('health'|'exit'|'error')`。内部：spawn（现有逻辑平移）、waitForHealth 轮询（现有）、**新增**：崩溃后自动重启（最多 3 次/10 分钟，指数退避）、退出时 Windows `taskkill /T /F` 清理进程树
- **tray-controller**：`setStatus('green'|'yellow'|'red')`、托盘 tooltip 显示"运行中 · X小时"（每分钟刷新）、右键菜单（显示主界面/立即跑管线/退出）、双击恢复窗口。**通知与托盘是两个模块——托盘管状态显示，通知桥管消息弹出**
- **notification-bridge**：订阅后端 SSE（`/api/v1/stream/notifications`，新增端点，见 D4）→ Windows `Notification` 弹出 → 点击通知聚焦主窗口。AppUserModelID 设置（Windows 通知必要）
- **window-manager**：主窗口创建/loading→ready 切换（现有逻辑平移）+ **关闭行为改为隐藏到托盘**（`close` 事件 preventDefault + hide，只有托盘菜单"退出"才真正退出）

---

## 三、任务清单（D 系列，按依赖顺序）

### 前置：G0 修 serve 飞书 bug（CLOSURE-FIX-PLAN 已列，先修）

**这是桌面端大修的前置**——桌面端 spawn 的是 `--serve-only`，同一个 bug。修法：定位 main.py:1609 `bot_clients_started` 分支未执行的原因（日志显示 FastAPI 启动后进程异常终止？还是 start_api_server 抛了被吞的异常？）。修复标准：`py main.py --serve-only` 启动后日志出现 `[Main] Feishu Stream client started`，飞书发消息有回复。

### D1 构建残留清理（15 分钟）

- `git rm -r --cached` 不适用（dist 未跟踪）——直接删 `dist/`、`dist-v3.29/`、`old-installers/`（3.1GB 磁盘回收）
- `.gitignore` 确认 `apps/dsa-desktop/dist*` 已忽略（防再次堆积）
- **验收**：`du -sh apps/dsa-desktop/` 从 3.2GB 降到 <1GB（node_modules 保留）

### D2 main.js 模块化拆分（1.5 小时，纯平移不改行为）

- 按"二、架构蓝图"拆 5 个模块；main.js 保留 app 生命周期 + 模块组装
- **平移纪律**：这一步不改任何行为（除模块边界必要的接口化），现有测试全过是唯一验收——行为变化留给 D3-D6
- **验收**：`npm test`（node --test）全过 + `npm run dev` 手动冒烟（窗口能开、后端能起）

### D3 单实例锁 + 托盘常驻（1 小时）

- `app.requestSingleInstanceLock()`：拿不到锁 → `app.quit()`（第二个实例静默退出，或聚焦已有窗口）
- Tray：图标三态（用 assets/ 下 16px PNG；先用纯色占位图标——绿#22c55e/黄#eab308/红#ef4444，Electron 可用 nativeImage.createFromDataURL 生成，不依赖美术资源）
- `window-all-closed` 不退出 + 窗口 `close` 事件 → `hide()`（关闭=隐藏到托盘）
- 托盘右键菜单：显示主界面 / 退出（退出才走真正的 `before-quit` → backend-manager.stop()）
- **验收**：双击图标开应用 → 关窗 → 托盘图标还在 → 后端仍活着（飞书发 /健康 有回复）→ 双击托盘恢复窗口 → 托盘"退出"后进程树干净（`tasklist` 查无残留 python）

### D4 后端 SSE 通知流 + 通知桥（1.5 小时，前后端配合的核心）

**后端**（`api/v1/endpoints/` 新增 `stream.py`）：
- `GET /api/v1/stream/notifications`（SSE）：连接建立后，把 notification bus 的当日 JSONL 尾部推送（简单实现：每 5 秒读文件比对 mtime，新行即推 `event: notification`）。**不引入 WebSocket 依赖**——SSE 用 FastAPI `StreamingResponse` 原生支持
- 事件格式：`{"title": "今日操作单", "body": "买入 002475 @56.5…", "severity": "info"}`

**桌面端**（notification-bridge.js）：
- EventSource 订阅 SSE → `new Notification({title, body})` 弹出 → `click` 事件聚焦主窗口
- `app.setAppUserModelId('com.dsa.assistant')`（Windows 通知必设，否则不弹）
- **与手机渠道的配合逻辑**：后端 NotificationService 的推送（企微/飞书 webhook）**不变**——手机和桌面**同时**收到。桌面端 SSE 是旁路订阅（只读 bus，不干扰发送链路）。bus 里 system_error 和 bot 会话回复不入 SSE（与 digest 总线同一过滤口径）

**验收**：启动桌面端 → 手动向 bus 写一条测试通知（或跑 digest force）→ Windows 右下角弹出系统通知 → 点击通知主窗口置前 → 手机企微同步收到同一条

### D5 状态灯 + 健康联动（45 分钟）

- backend-manager 健康检查结果 → tray-controller.setStatus（绿/黄/红）
- 后端新增轻量端点 `GET /api/v1/runtime/status`（CLOSURE-FIX-PLAN G2 的后端部分）：`{running, started_at, uptime_seconds, feishu_stream_alive, last_pipeline_run, last_digest_sent}`——托盘 tooltip 和 Web 横幅共用这个端点
- 托盘 tooltip：`半自动助手 · 运行中 2h15m · 飞书✓`（每分钟刷新）
- **验收**：杀掉后端进程 → 托盘图标 30 秒内变红 → 自动重启后回绿（D6 的守护生效）

### D6 后端崩溃自动重启（45 分钟）

- backend-manager 监听子进程 `exit`：非正常退出（code≠0 且非用户主动 stop）→ 记录 → 指数退避重启（1s/4s/16s，10 分钟窗口最多 3 次，超限转红状态+系统通知"后端反复崩溃，请查看日志"）
- **验收**：`taskkill /F` 杀后端 → 16 秒内托盘回绿 → 飞书 /健康 恢复响应

### D7 开机自启（可选，30 分钟）

- 托盘右键菜单加"开机自启 ✓/✗"开关（electron `app.setLoginItemSettings`）
- 默认关；状态持久化到 electron-store 或 localStorage（不引新依赖的话用 app.getPath('userData')/settings.json）
- **验收**：开启后重启电脑 → 应用自动拉起并缩到托盘

### D8 大修收尾（30 分钟）

- README_desktop 更新（桌面端使用说明：托盘含义/通知行为/退出方式）
- CHANGELOG 记录（UI 变化附截图——托盘三态 + 系统通知弹出 + 关窗常驻）
- CLOSURE-FIX-PLAN v2 状态表回填：G1 标记"由桌面端大修替代完成"
- 全量验证：后端 pytest + 前端 lint/build + 桌面端 npm test + 手动冒烟全流程（双击→关窗→通知→恢复→退出）

---

## 四、执行顺序与依赖

```
G0 修 serve 飞书 bug（前置，后端）
 → D1 清理（3.1GB 磁盘回收，先做心情好）
 → D2 模块化拆分（纯平移，给后续改动干净的底座）
 → D3 单实例 + 托盘常驻（桌面端核心体验）
 → D4 SSE 通知桥（前后端配合核心）
 → D5 状态灯（依赖 D3 托盘 + D4 的 runtime/status 端点）
 → D6 崩溃重启（依赖 D5 状态机制）
 → D7 开机自启（独立可选）
 → D8 收尾
（并行：CLOSURE-FIX-PLAN 的 G4 每日管线 / G5 回填命令 / G6 清理——纯后端，与桌面端无冲突，可在 D2 之后穿插执行）
```

---

## 五、GLM-5.3F 执行注意事项（本项目特有）

1. **Electron 主进程改动禁用 ESM**——项目是 CommonJS（require），保持一致。
2. **托盘图标不用美术资源**：`nativeImage.createFromDataURL` + 纯色 16x16 PNG base64 内嵌——三态图标各一个常量，零文件依赖。
3. **AppUserModelID 必须在 app.ready 前设置**（Windows 通知硬性要求），放在 main.js 最顶部。
4. **SSE 实现**：FastAPI `StreamingResponse(media_type="text/event-stream")` + async generator；注意客户端断开时正确退出 generator（`await request.is_disconnected()`）。
5. **进程树清理**：Windows 下 `backendProcess.kill()` 只杀直接子进程——py 可能再 spawn 孙进程（调度器 watchdog）。用 `spawn('taskkill', ['/pid', pid, '/T', '/F'])`（仓库已有先例：scripts/smoke_bot_index_entry.py 同款写法）。
6. **单实例锁的第二个实例**：聚焦已有窗口后退出（`second-instance` 事件里 `win.show()` + `app.quit()`）。
7. **D2 平移纪律**：拆分过程中发现的原有 bug（如 G0 类）**只记录不顺手修**——行为变更全部放 D3-D6，每步可回滚。
8. **测试命令**：后端 `py -m pytest -m "not network" -q`（禁并发）；前端 `npm run lint && npm run build`（dsa-web）；桌面端 `npm test`（dsa-desktop）；UI 验证附截图。
9. **通用纪律**：EXECUTION-BLUEPRINT.md 第九节全部适用；commit 后 `git show --stat` 核对；UI 变化 PR/交付说明附截图（AGENTS.md 硬规则）。
10. **通知渠道配合的边界**：手机推送链路（NotificationService→企微/飞书 webhook）一行不改；桌面端只做**旁路订阅**（读 bus 推系统通知）。两路并存、互不干扰——这是"一个项目"的正确集成方式，不是把通知迁移到桌面端。

---

## 六、状态表（执行后回填）

| 任务 | 状态 | 验收证据 |
| --- | --- | --- |
| G0 serve 飞书 bug | ⏳ | |
| D1 构建残留清理 | ⏳ | |
| D2 模块化拆分 | ⏳ | |
| D3 单实例+托盘 | ⏳ | |
| D4 SSE 通知桥 | ⏳ | |
| D5 状态灯 | ⏳ | |
| D6 崩溃重启 | ⏳ | |
| D7 开机自启 | ⏳ | |
| D8 收尾 | ⏳ | |

---

## 七、用户侧零待办

全部由执行者完成。唯一可选项：D7 开机自启默认关——用户想开在托盘菜单里自己点。

# 桌面端大修计划 v2（GLM-5.3F 执行指导）

> **[历史计划·桌面端已冻结]** 桌面端在重构计划 v4.0 中已定为"冻结不删"（用户主入口是飞书 + Web），本大修计划不再执行。仅作追溯，当前方向以 `docs/REFACTOR-PLAN-STRATEGY-CENTRIC.md` 为准。

> 版本：v2.0（2026-09-07 定稿——基于外部调研修订，替代 v1.0）
> 调研依据：Electron 官方文档（app/Tray/Notification 三份逐条核对）+ 参考项目（fyears/electron-python-example 2089★ 已停维护 / every-pdf 1242★ 活跃，进程管理最佳现实参考）+ 本仓库代码核实（所有引用行号已逐条验证）
> 效力：桌面端大修的唯一真源。吸收并替代 CLOSURE-FIX-PLAN v2 的 G1；G2/G4/G5/G6 后端部分仍按 v2 执行。
> 纪律：端到端验证后才算完成；UI 变化附截图（AGENTS.md 硬规则）。

---

## 〇、调研带来的计划修正（v1 → v2 变化）

| # | v1 的假设 | 调研结论（v2 修正） |
| --- | --- | --- |
| 1 | 托盘图标用 base64 PNG 内嵌生成 | **Windows 官方建议 ICO 格式**；三态各一个 ICO 文件放 assets/（PNG 会退化，ICO 支持 Windows 托盘色彩管理） |
| 2 | SSE 通知桥订阅现有任务流 | 现有 `/tasks/stream` 需要会话 Cookie（`useTaskStream.ts:197` 用 `withCredentials:true`），主进程 http.get 要手动带 Cookie——**更干净的做法：后端新增免鉴权的通知专用 SSE 端点**（本机 127.0.0.1 绑定，无安全风险，AGENTS 要求同步 .env.example/文档） |
| 3 | 崩溃重启"3次/10分钟"自定标准 | **every-pdf 模式**：`exit 且 code!==0 且非主动退出` 才重启；指数退避 `min(1000*2^n, 30s)`；上限 5 次；**uptime>60s 后计数归零**（稳定运行即重置）。另发现 v1 遗漏：**稳态健康监控**（进程活着但 uvicorn 假死时 exit 不触发——需 30s 周期探测 + 连续 N 次失败主动重启） |
| 4 | 单实例锁简单包裹 | 官方关键细节：**主启动逻辑必须移入 else 分支**（`app.quit()` 不同步终止后续代码）；second-instance 需补 `if (!win.isVisible()) win.show()`（托盘隐藏态） |
| 5 | 开机自启用 openAsHidden | **openAsHidden 在 Electron 31 是 macOS-only 且 Deprecated**（main 分支已删除）——Windows 正确做法是 `args:['--hidden']` + 启动时检查 `process.argv.includes('--hidden')`；**读写都要传相同 args** |
| 6 | Tray 用 guid 固定位置 | 官方明确：**未签名应用不用 guid**（绑定 exe 路径，路径变即失效）——本项目未签名，省略 |
| 7 | — | **好消息**：本项目现有进程管理（taskkill /T /F + waitForHealth + 端口探测）**已超过最高星模板**（fyears 2089★ 只有裸 spawn/kill 且停维护）——大修是补缺失件，不是重写 |
| 8 | — | **AppUserModelID 取值必须与 package.json 的 appId 一致**（`com.daily-stock-analysis.desktop`，已核实 package.json:19）且 setLoginItemSettings 注册表键 name 默认取它——生命周期一致性 |

---

## 一、终局形态（不变）

双击图标 → 窗口开 + 后端拉起（含飞书）→ **关窗缩托盘继续跑** → 托盘三态图标（绿=健康/黄=重启中/红=失败）→ 双击托盘恢复 → 系统通知（后端事件弹出，点击聚焦窗口）→ 手机渠道（企微/飞书）同步推 → 单实例锁 → 可选开机自启（--hidden 启动直接进托盘）。

---

## 二、main.js 改造清单（调研版——含真实行号，逐条可执行）

> 改造原则：**不拆文件**（v1 的"模块化拆分"取消——现有 tests/main.test.js 1694 行按单文件 main.js 的 fake module 模式写，拆文件会连带重写全部测试，风险大于收益。改为单文件内分区块注释 + 顶部模块级常量）。 CommonJS 保持。

### P1 启动序列重构（头部 L1-26 + L2341）

1. **L1 require 之后立即**：
   - `const APP_USER_MODEL_ID = 'com.daily-stock-analysis.desktop'`（与 package.json:19 appId 同值）
   - `app.setAppUserModelId(APP_USER_MODEL_ID)`（Windows 通知硬前提；NSIS 打包不会自动设）
   - `const startHidden = process.argv.includes('--hidden')`（开机自启的隐藏启动）
2. **单实例锁**（官方结构，L2341 `whenReady().then(createWindow)` 整体移入 else）：
   ```js
   const gotTheLock = app.requestSingleInstanceLock();
   if (!gotTheLock) {
     app.quit();
   } else {
     app.on('second-instance', () => {
       if (mainWindow) {
         if (mainWindow.isMinimized()) mainWindow.restore();
         if (!mainWindow.isVisible()) mainWindow.show();
         mainWindow.focus();
       }
     });
     app.whenReady().then(createWindow);  // 原 L2341
     // activate / window-all-closed 等注册全部随迁入 else
   }
   ```
3. **module 级新增状态**（靠前声明）：`let isQuittingApp = false; let intentionalStop = false; let restartAttempts = 0; let lastBackendStartAt = 0; let tray = null;`（tray 全局引用防 GC——官方 FAQ 明确：局部变量托盘图标几分钟内消失）

### P2 崩溃自动重启 + 稳态健康监控（L1363/L1417）

4. **L1363 `backendProcess.on('exit')` 升级**：
   ```js
   backendProcess.on('exit', (code, signal) => {
     logLine(`[backend] exited code=${code} signal=${signal || 'none'}`);
     if (intentionalStop || isQuittingApp) return;      // 主动退出不重启
     scheduleBackendRestart();                           // 崩溃 → 退避重启
   });
   ```
5. **新增 `scheduleBackendRestart()`**（every-pdf 模式 + 生产级加固）：
   - `delay = Math.min(1000 * 2 ** restartAttempts, 30000)`
   - `restartAttempts >= 5` → 放弃：`setTrayStatus('error')` + 系统通知"后端反复崩溃，请查看日志" + 广播 BACKEND_ERROR_EVENT
   - 未超限 → setTimeout(delay) 后走完整链路：`findAvailablePort → startBackend → waitForHealth → 广播 ready → setTrayStatus('normal')`
   - 启动成功且 uptime > 60s → `restartAttempts = 0`（稳定即重置）
6. **L1417 `stopBackend()` 开头**置 `intentionalStop = true`（防退出清理被误判崩溃）；`before-quit`（L2356）先置 `isQuittingApp = true` 再 stopBackend——**与 installDownloadedUpdate() 的 stopBackend 共享此标志隔离**（更新流程不触发重启，调研风险提示）
7. **新增稳态健康监控**（v1 遗漏项）：backend ready 后启动 30s 周期 `http.get /api/v1/health`（复用 waitForHealth 的探测函数）；**连续 3 次失败**（uvicorn 假死，exit 不触发）→ 主动 taskkill + scheduleBackendRestart()；成功一次即重置失败计数

### P3 托盘常驻（L2090 窗口 + 新增 createTray）

8. **L2090 `new BrowserWindow`**：`startHidden` 时 `show: false`；注册 close 事件：
   ```js
   mainWindow.on('close', (event) => {
     if (!isQuittingApp) { event.preventDefault(); mainWindow.hide(); }
   });
   ```
   （hide 不销毁 → window-all-closed 不触发 → app 驻留；L2349 的 window-all-closed handler 保留作安全网不改）
9. **新增 `createTray()`**（whenReady 后调用）：
   - `assets/tray-normal.ico` / `tray-warning.ico` / `tray-error.ico`（16/32px ICO；**生成方式**：用 Python PIL 从纯色 32x32 画布转 ICO——`py -c "from PIL import Image; ..."` 一次生成三色 #22c55e/#eab308/#ef4444，不用美术资源）
   - `setTrayStatus('normal'|'warning'|'error')`：`tray.setImage()` + `setToolTip(\`半自动助手 · ${状态文案} · ${uptime}\`)`（tooltip 60s 定时刷新）
   - 右键菜单：显示主窗口 / 检查更新（复用现有 updater 调用）/ 开机自启(checkbox，见 P5) / 退出（置 isQuittingApp → app.quit()）
   - `tray.on('click', showMainWindow)`；**不用 guid**（未签名，官方建议省略）

### P4 系统通知桥（后端免鉴权 SSE + 主进程订阅）

10. **后端新增端点**（`api/v1/endpoints/` 加进现有 assistant.py，免鉴权——本机绑定无风险）：
    - `GET /api/v1/assistant/stream/notifications`：SSE，**照抄仓库内先例 analysis.py:797-865 的模式**（StreamingResponse + async generator + 30s 心跳 + CancelledError 正确传播 + finally 清理——调研确认该实现已符合最佳实践，勿自创写法）
    - 数据源：notification bus 的当日 JSONL（每 2s 检查 mtime，新行解析为 `{title, body, severity}` 推送；跳过 bot 会话回复与 system_error——与 digest 过滤口径一致）
    - **AGENTS 同步**：新端点记 CHANGELOG；无需 .env.example（无新配置）
11. **主进程新增 `subscribeBackendNotifications()`**（backend ready 后启动）：
    - Node `http.get` 增量解析 `text/event-stream`（**必须主进程**——Notification 只在主进程可用，且隐藏窗口的渲染进程计时器被 backgroundThrottling 节流，调研明确）
    - 收到事件 → `showDesktopNotification(title, body)`
    - 断线退避重连（复用 P2 的退避函数）；渲染进程现有 useTaskStream 的 EventSource **不动**（UI 层任务进度与系统通知各司其职）
12. **新增 `showDesktopNotification(title, body)`**：`Notification.isSupported()` 守卫 → `new Notification({title, body}).on('click', showMainWindow).show()`
    - **验收注意**：dev 模式（electron.exe）通知可能不显示——官方 tip：dev 时 `app.setAppUserModelId(process.execPath)` 并把 electron.exe 固定到开始菜单；**打包版才用正式 APP_USER_MODEL_ID 验证**（计划里注明，防执行者误判失败）

### P5 开机自启（IPC + 正确的 args 处理）

13. **IPC**：`desktop:get-launch-at-login` / `desktop:set-launch-at-login`
    - 守卫：`app.isPackaged`（dev 下注册的是 electron.exe，必须禁用开关）
    - 写：`app.setLoginItemSettings({ openAtLogin, args: ['--hidden'] })`
    - 读：`app.getLoginItemSettings({ args: ['--hidden'] }).openAtLogin`（**读传同样 args 否则 openAtLogin 永远 false**——官方文档原话）
    - **不用 openAsHidden**（Electron 31 macOS-only Deprecated）
14. Web 设置页加开关（preload 暴露 IPC → 渲染进程调用；dsa-web 检测 Electron 环境才显示，复用现有 desktop 环境检测）

### P6 清理与收尾

15. **构建残留清理**：删 `dist/`（2.4GB）+ `dist-v3.29/`（737MB）+ `old-installers/`；确认 .gitignore 覆盖 `apps/dsa-desktop/dist*`
16. **测试**（tests/main.test.js 按现有 fake module 模式补）：单实例锁 else 分支 / close-to-tray preventDefault / 崩溃退避序列（fakeBackend.emit('exit') 模拟，仓库已有先例）/ loginItemSettings args 读写一致性
17. **文档与截图**：desktop-package.md 更新（托盘三态含义/通知行为/--hidden）；CHANGELOG；交付说明附截图（托盘三态 + 通知弹出 + 关窗常驻——AGENTS 硬规则）
18. **全量验证**：后端 pytest（禁并发）+ dsa-web lint/build + dsa-desktop npm test + 打包版手动冒烟（双击→关窗→杀后端看托盘变黄重启回绿→通知弹出→托盘退出进程树干净）

---

## 三、执行顺序

```
P6-15 清理（先做，3.1GB 回收）
 → P1 启动序列（AppUserModelID+单实例锁+状态变量——一切的地基）
 → P2 崩溃重启+稳态监控（后端进程可靠性）
 → P3 托盘常驻（桌面端核心体验）
 → P4 SSE 通知桥（前后端配合，含后端新端点）
 → P5 开机自启（依赖 P1 的 --hidden 处理）
 → P6-16/17/18 测试+文档+验证
（并行：G0 serve 飞书 bug 修复是全部前置；CLOSURE-FIX-PLAN 的 G4 管线/G5 回填/G6 清理为纯后端，可穿插）
```

---

## 四、GLM-5.3F 执行注意事项（v2，调研修订版）

1. **本计划所有行号已逐条验证**（1363/1417/2341/2090/797-865/package.json:19）——但执行时以实际文件为准，行号可能随编辑漂移，先 grep 锚文本再改。
2. **不拆 main.js 单文件**（v1 计划的模块化拆分取消）——tests/main.test.js 1694 行测试绑定单文件结构，拆分收益低于测试重写成本。分区块用注释分隔即可。
3. CommonJS 保持（require/module.exports）。
4. **ICO 生成**：Python PIL 三色 32x32 → `.save('tray-normal.ico', sizes=[(16,16),(32,32)])`；一次性脚本放 .scratch/ 不入库，ICO 入 assets/。
5. **dev 模式通知验证陷阱**（P4-12）——打包前用 process.execPath 作 AppUserModelId 验证，正式验收以打包版为准。
6. **SSE 端点照抄 analysis.py:797 先例**——心跳/CancelledError/finally/X-Accel-Buffering 四件套一个不少；FastAPI async generator 必须有 await 点才能正确取消（官方文档）。
7. **intentionalStop 标志贯穿三条路径**：用户退出 / 自动更新 stopBackend / 崩溃重启判断——三处共享，漏一处就会"退出时被误判崩溃又拉起"。
8. **后端新增端点记 CHANGELOG**（AGENTS 硬规则）；桌面端交付说明需注明 Web 构建产物与 Electron 构建验证情况。
9. 通用纪律：EXECUTION-BLUEPRINT.md 第九节；commit 后 git show --stat 核对；UI 附截图。
10. **参考项目**：进程生命周期抄 every-pdf（github.com/DDULDDUCK/every-pdf main/background.ts 的 isQuitting 模式）；SSE 抄仓库内 analysis.py；Tray/Notification/单实例 全部以 Electron 官方文档为准（调研已核对 verbatim）。

---

## 五、状态表（执行后回填）

| 任务 | 状态 | 验收证据 |
| --- | --- | --- |
| G0 serve 飞书 bug（前置） | ✅ 2026-09-08 | 验证无代码 bug，加 debug 日志（8d3cbc87） |
| P6-15 构建残留清理 | ✅ 2026-09-08 | 3.1GB 回收（3.2GB→466MB） |
| P1 启动序列（锁+AUMID+状态） | ✅ 2026-09-08 | 67dee532 |
| P2 崩溃重启+稳态监控 | ✅ 2026-09-08 | 指数退避+30s 轮询+unref |
| P3 托盘常驻 | ✅ 2026-09-08 | 三态图标+关窗缩托盘 |
| P4 SSE 通知桥 | ✅ 2026-09-08 | ecc6dd18，双路并存 |
| P5 开机自启 | ✅ 2026-09-08 | 托盘 checkbox + IPC，49+5 测试全过 |
| P6-16/17/18 测试+文档+验证 | ⏳ | |

---

## 六、用户侧零待办

全部由执行者完成。开机自启默认关，用户想在托盘菜单或设置页自行开启。

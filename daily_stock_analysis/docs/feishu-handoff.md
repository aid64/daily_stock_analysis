# 飞书通知与交互配置交接说明

> 目的：把本仓库**当前实际生效**的飞书配置一次讲清，供接手者在不问原作者的前提下独立排查与迁移。
> 编写时间：2026-09-15。范围：`daily_stock_analysis/` 的通知发送、事件交互（Stream Bot）、渠道路由、每日摘要。
> 事实来源：`.env`（本机实际值）、`src/notification_sender/feishu_sender.py`、`bot/platforms/feishu_stream.py`、`src/notification_routing.py`、`src/services/notification_digest_service.py`，以及 `python main.py --check-notify` 实测输出。

---

## 一、先理解：飞书有三种互相独立的接入方式

本项目同时存在三条飞书路径，**它们互不替代**，混淆是最常见的排障起点。

| 方式 | 用途 | 关键配置 | 本项目是否在用 |
| --- | --- | --- | --- |
| A. 群机器人 Webhook | 单向把消息推进群 | `FEISHU_WEBHOOK_URL` (+可选 secret/keyword) | **否**（未配置 URL） |
| B. 应用机器人 App Bot | 用应用身份主动发消息给群或人 | `FEISHU_APP_ID` + `FEISHU_APP_SECRET` + `FEISHU_CHAT_ID` + `FEISHU_RECEIVE_ID_TYPE` | **是**（主动推送主力） |
| C. Stream 长连接 Bot | 接收用户消息、执行 `/命令` 并回复 | 同 B 的凭据 + `FEISHU_STREAM_ENABLED=true` | **是**（交互主力） |

要点：

- 只配 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 不会开启任何推送——它们只是应用凭据。
- B 是"系统→你"，C 是"你→系统"。本项目**两者都开**，才是完整的双向闭环。
- A 与 B 不共存：`send_to_feishu()` 在 `FEISHU_WEBHOOK_URL` 存在时优先走 Webhook，否则才走 App Bot。

---

## 二、当前实际生效的配置（本机 `.env`）

以下为真值，`FEISHU_APP_SECRET` 已脱敏。**这些值写在 `daily_stock_analysis/.env`，不入库**。

```env
# —— 应用凭据（方式 B / C 共用）——
FEISHU_APP_ID=cli_aa1484d606211cd5
FEISHU_APP_SECRET=<已脱敏，见本机 .env>
FEISHU_DOMAIN=feishu                      # 飞书国内版

# —— 主动推送目标：直发用户私聊 ——
FEISHU_RECEIVE_ID_TYPE=open_id            # 关键：私聊用 open_id
FEISHU_CHAT_ID=ou_7ff5e38bf6a9bacf7f64c02645076ac5   # 这里存的其实是用户 open_id
FEISHU_STREAM_ENABLED=true                # 开启双向交互

# —— 渠道路由：三类通知都只走飞书（避免多平台重复）——
NOTIFICATION_REPORT_CHANNELS=feishu
NOTIFICATION_ALERT_CHANNELS=feishu
NOTIFICATION_SYSTEM_ERROR_CHANNELS=feishu

# —— 每日摘要总线 ——
NOTIFICATION_DAILY_DIGEST_ENABLED=true
NOTIFICATION_DIGEST_TIME=18:00
```

### 两个必须知道的命名陷阱

1. **`FEISHU_CHAT_ID` 名不副实**：私聊模式下它装的不是 `oc_` 开头的群 ID，而是 `ou_` 开头的**用户 open_id**。变量名沿用群聊语义，容易误判为"配错了"。判断依据是 `FEISHU_RECEIVE_ID_TYPE`：
   - `chat_id`（默认）→ `FEISHU_CHAT_ID` 填群 ID（`oc_...`）
   - `open_id` → `FEISHU_CHAT_ID` 填用户 open_id（`ou_...`）
2. **`.env` 里不要写行内注释**。历史上曾出现 `FEISHU_RECEIVE_ID_TYPE=open_id  # 直发用户私聊`。本机 dotenv 会剥离行内注释，实测加载值正确；但这依赖解析器行为，且若注释含特殊字符会污染取值——变量注释请单独成行。已实测确认当前加载值为 `open_id`，无隐患残留。

### 实测已加载值（排障时可用来比对）

```
receive_id_type = 'open_id'
chat_id         = 'ou_7ff5e38bf6a9bacf7f64c02645076ac5'
app_id          = 'cli_aa1484d606211cd5'
domain          = 'feishu'
stream          = True
```

加载链路提醒：`Config()` **直接实例化不会读 `.env`**，必须用 `Config.get_instance()`（内部调用 `setup_env()` 加载）。写脚本排查时若发现配置全空，先怀疑这一点。

---

## 三、消息发送路径（方式 B）

实现：`src/notification_sender/feishu_sender.py`

```
NotificationService.send_with_results(content, route_type=...)
  └─ 路由过滤（NOTIFICATION_*_CHANNELS，见第五节）
      └─ FeishuSender.send_to_feishu(content)
          ├─ 若 FEISHU_WEBHOOK_URL 有值 → Webhook 路径（本项目未用）
          └─ 否则若 APP_ID + APP_SECRET + CHAT_ID 齐全 → App Bot 路径
              └─ lark-oapi SDK：im.v1.message.create
                   receive_id_type = FEISHU_RECEIVE_ID_TYPE
                   receive_id      = FEISHU_CHAT_ID
                   msg_type        = interactive（卡片，Markdown 渲染）
```

行为细节：

- 走 `im.v1.message.create`，每条消息带 UUID 幂等键，失败按次数重试。
- 超过 `FEISHU_MAX_BYTES`（约 20KB）自动分批发送。
- `FEISHU_SEND_AS_FILE=true` 时报告以 `.md` 文件形式发送（需 `im:message` + `im:file` 权限）；仅对 `route_type="report"` 生效。本项目未开启。
- 消息体是 interactive 卡片；若在飞书"自动化"里消费卡片，需按 `docs/bot/feishu-bot-config.md` 的卡片 JSON 结构映射 `card.elements[0].text.content`。

---

## 四、交互路径（方式 C，Stream Bot）

实现：`bot/platforms/feishu_stream.py`；启动：`main.py:start_bot_stream_clients()`，当 `FEISHU_STREAM_ENABLED=true` 时后台启动。

```
飞书用户发消息 → lark-oapi WebSocket 长连接（handle_message）
  → _parse_event_message（提取文本、mentions）
  → _extract_command（去掉 @机器人 前缀，识别 /命令）
  → BotCommand 分发执行
  → reply_text / _send_interactive_card 回复原会话
```

关键点：

- 长连接模式下 **`encrypt_key` / `verification_token` 不是必需的**——这是 Stream 相比 Webhook 回调的优势。
- 消息在同一会话内串行处理（`_conversation_key` 队列），避免同一用户并发命令互相干扰。
- 长连接状态可通过 API 查询：`GET /api/v1/assistant/runtime/status` 返回 `feishu_stream_alive`。注意该字段只表示"SDK 可用 + 凭据非空、客户端可构造"，**不等于长连接当前已连接**（详见第七节现象 4）。
- 该路径与方式 B 共用同一套 `FEISHU_APP_ID` / `SECRET`；`FEISHU_RECEIVE_ID_TYPE` 也影响主动回复时的 `receive_id_type`。

---

## 五、渠道路由（避免"为什么发了多份"）

实现：`src/notification_routing.py`

三类通知各自独立路由，**留空 = 发往所有已配置渠道**（这是重复消息的常见根因）：

| route_type | 配置键 | 用途 |
| --- | --- | --- |
| `report` | `NOTIFICATION_REPORT_CHANNELS` | 个股/日报/大盘分析报告、账户决策包 |
| `alert` | `NOTIFICATION_ALERT_CHANNELS` | 事件告警（价格/涨跌幅/成交量触发） |
| `system_error` | `NOTIFICATION_SYSTEM_ERROR_CHANNELS` | 系统错误与管线护栏告警 |

本项目三者都显式设为 `feishu`，因此**只会有一个渠道**。若接手后新增了微信/邮件等渠道，必须同步这三个键，否则同一条消息会重复投递到各渠道。

补充规则：

- 交互式命令（你在飞书里发 `/健康`）的回复走会话上下文，**不受**路由限制，也不会因路由为空而发到别处。
- 路由过滤发生在 Markdown 转图片之前。

---

## 六、每日摘要总线

实现：`src/services/notification_digest_service.py`；开关 `NOTIFICATION_DAILY_DIGEST_ENABLED`，到点 `NOTIFICATION_DIGEST_TIME`（当前 18:00）由后台任务聚合发送。

- report 类通知**先写入总线**而非即时发送：总线文件为 `daily_stock_analysis/data/cache/notification_bus/<date>.jsonl`（代码中 `notification_digest_service.BUS_ROOT`），到点合成一条【每日摘要】。
- 按 `logical_id` 折叠：同一逻辑事件刷新内容时替换旧条目，不追加第二条。示例：
  - `daily_operation_sheet:<date>` —— 账户决策包
  - `event_alert:<symbol>:<type>:<direction>` —— 事件告警
- 摘要分级顺序：**与你持仓相关** → **重要**（alert / critical / error）→ **其他**。
- **两类不入总线的通知，保持即时发送**：
  1. `system_error` —— 系统错误必须立刻可见；
  2. `alert` —— 盯盘告警（价格/涨跌幅/成交量触发）。盘中止损信号压到 18:00 等于没有盯盘。
  但注意：**alert 在即时发送成功后，仍会向总线写入同一 `logical_id` 的索引**（`AlertWorker._index_alert_in_digest`），因此它也会作为一条出现在晚间摘要里——这是刻意的"盘中即时 + 晚间可回顾"，不是重复投递。
- 另有 `_digest_bypass` 标记用于**盘前账户决策包**：它走 report 路由，但必须 09:00 即时送达，因此打上 bypass 绕过总线拦截（仍会先写总线留索引）。
- 手工验收必须用 `send(..., test_mode=True)`——`test_mode` 会同时跳过总线写入，否则会污染生产总线、把测试消息混进当天摘要。

---

## 七、排障手册（按现象查）

### 现象 1：完全收不到消息

按顺序查：
1. `python main.py --check-notify` —— 看"已配置渠道"是否包含飞书、有无 Errors。
2. 确认 `.env` 里没有 `FEISHU_WEBHOOK_URL`。若有，发送会走 Webhook 路径而绕开 App Bot，且本项目未配置 webhook 相关项。
3. 确认 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID` 三项齐全。
4. 查 `logs/` 中 `App Bot` 相关日志（`_app_send_raw` 会打印状态码与错误）。

### 现象 2：报 `230002 Bot/User can NOT be out of the chat`

含义：机器人不在目标会话中（飞书 API 侧错误码）。本项目历史上群被解散后曾命中此错。

正确处置：改用 **open_id 私聊**（`FEISHU_RECEIVE_ID_TYPE=open_id` + 用户 `ou_...`），不要依赖群 ID。本机当前即为该形态，已修复。

注意：**代码不会自动降级**——`chat_id` 失效时不会自动改用 open_id，必须手工改 `.env` 并重启。这是一个已知的可用性缺口：群被解散后若无人干预，推送会持续失败直到配置被修正（失败会体现在 `send_with_results` 的 `success=False`，并可触发 `system_error` 告警）。

### 现象 3：同一条内容收到多份

- 多半是路由键留空导致多渠道投递 → 检查第五节的三个 `NOTIFICATION_*_CHANNELS`。
- 也可能是同一逻辑事件被不同 `dedup_key` 写了两次总线 → 查当天 `notification_bus/<date>.jsonl`，确认生产推送与手工验收是否混入同一总线。

### 现象 4：`feishu_stream_alive: false`，发命令无反应

先理解该字段的**真实含义**（`api/v1/endpoints/assistant.py`）：它只表示"`feishu_stream` 模块能构造出客户端对象"，即 SDK 已安装、凭据字段非空。它**不等于长连接当前处于已连接状态**——连接中途断开、飞书侧未开通事件订阅时，该字段仍可能为 `true`。

因此排查顺序：

- `true` 但发命令无反应 → 不要只看这个字段，去查服务日志里的长连接建立/重连记录；
- `false` → 依次确认：`FEISHU_STREAM_ENABLED=true`、`lark-oapi` 已安装（`pip install -r requirements.txt`）、`FEISHU_APP_ID`/`SECRET` 非空；
- 无论真假，都要确认飞书开放平台已开启**事件订阅 `im.message.receive_v1`** 并发布应用版本——仅配凭据不会建立长连接。

### 现象 5：诊断报告说"每日摘要为预留配置、不会发送"

**这是已修复的过时文案**。`notification_diagnostics.py` 曾把每日摘要报为 P4 预留项，与实现相反。现已改为 info 级说明实际行为（写总线 + 到点聚合 + 例外即时发送）。若你看到该 warning，说明代码被回退到了旧版本。

---

## 八、迁移到新环境的最小步骤

1. 在飞书开放平台创建/复用一个自建应用，拿到 `App ID` / `App Secret`。
2. 开通权限：`im:message`（发消息）；若启用 Stream 交互，再开事件订阅 `im.message.receive_v1`；若用 `FEISHU_SEND_AS_FILE` 再加 `im:file`。
3. 发布应用版本，并把机器人可用范围设为你自己（或目标群/成员）。
4. 确定 `FEISHU_RECEIVE_ID_TYPE`：
   - 直发自己 → `open_id`，把你的 open_id 填入 `FEISHU_CHAT_ID`
   - 发群 → `chat_id`，把群 ID 填入 `FEISHU_CHAT_ID`
5. 写 `.env`（**注释单独成行**，不要行内注释）：

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_DOMAIN=feishu
FEISHU_RECEIVE_ID_TYPE=open_id
FEISHU_CHAT_ID=ou_xxx
FEISHU_STREAM_ENABLED=true
NOTIFICATION_REPORT_CHANNELS=feishu
NOTIFICATION_ALERT_CHANNELS=feishu
NOTIFICATION_SYSTEM_ERROR_CHANNELS=feishu
```

6. 验证：`python main.py --check-notify` → 启动服务 → `GET /api/v1/assistant/runtime/status` 应见 `feishu_stream_alive: true` → 在飞书发 `/健康` 应收到回复 → 触发一次推送确认收到卡片。

---

## 九、权限与安全边界

- 应用凭据只存在于本机 `.env`，**不得**写入代码、文档或提交历史。
- 建议最小权限：只要 `im:message`（+ 按需 `im:file`）。不要为本项目开放通讯录、群管理等无关权限。
- `FEISHU_APP_SECRET` 一旦泄漏，应在飞书开放平台重置并同步更新本机 `.env`。
- 本项目**不会**通过飞书执行任何交易动作；飞书仅承载决策通知、告警、命令查询与成交回填。

---

## 十、相关文件索引

| 关注点 | 文件 |
| --- | --- |
| 发送实现（App Bot / Webhook） | `src/notification_sender/feishu_sender.py` |
| 交互实现（Stream Bot） | `bot/platforms/feishu_stream.py` |
| 渠道路由表 | `src/notification_routing.py` |
| 每日摘要总线 | `src/services/notification_digest_service.py` |
| 配置诊断 | `src/services/notification_diagnostics.py`、`python main.py --check-notify` |
| 配置注册项 | `src/core/config_registry.py`（搜 `FEISHU_`） |
| 环境变量模板 | `daily_stock_analysis/.env.example` |
| 飞书群 Webhook 图形化步骤 | `docs/bot/feishu-bot-config.md` |
| 通知总览（含路由/摘要语义） | `docs/notifications.md` |

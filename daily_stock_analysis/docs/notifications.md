# 通知能力基线（飞书单渠道）

> **2026-09-26 P5 起仅保留飞书一个通知渠道**（14 渠道时代结束，多渠道架构史见
> git 历史与 CHANGELOG）。本文档 = 飞书渠道的机制说明（路由/摘要/降噪/失败隔离）。
> 配置方法、凭证、Stream 接入与排障手册见 **[飞书配置交接说明](feishu-handoff.md)（现行真源）**。

## 发送链路

- **路由优先级**：群机器人 Webhook（`FEISHU_WEBHOOK_URL`，requests 直发）优先；
  App Bot（`FEISHU_APP_ID/APP_SECRET/FEISHU_CHAT_ID`，lark-oapi SDK `im.v1.message.create`）兜底。
  当前生产配置为 App Bot 模式（无 webhook 键）。
- **msg_type**：interactive 卡片先行、text 兜底；支持 file 消息（报告文件）。
- **分片**：超长内容按 `FEISHU_MAX_BYTES` 分片发送（片间 1 秒）。
- **message_id 留存（U1 漏斗第一步）**：App Bot 发送成功后，message_id + logical_id
  落 `data/feishu_dispatch.jsonl`（追加式旁路台账，留盘失败不影响发送）。
  webhook 响应无 message_id，不记录。
- 实现唯一入口：`src/notification_sender/feishu_sender.py`；通知服务：
  `src/notification.py`（`send_with_results`）。

## 通知路由策略

| 路由类型 | 配置 key | 当前生产者 |
| --- | --- | --- |
| `report` | `NOTIFICATION_REPORT_CHANNELS` | 单股推送、聚合日报、大盘复盘、合并推送 |
| `alert` | `NOTIFICATION_ALERT_CHANNELS` | EventMonitor 触发通知 |
| `system_error` | `NOTIFICATION_SYSTEM_ERROR_CHANNELS` | 预留能力；当前不新增自动系统错误生产者 |

- 配置值为渠道枚举（P5 后合法值实际只有 `feishu`）；留空 = 发到所有已配置静态渠道。
- 非空时只发送到路由列表与已配置渠道的交集，交集为空不 fallback。
- `send_to_context()`（机器人会话回复）不受路由限制。
- 路由过滤发生在 Markdown 转图片前。

### 每日摘要总线与逻辑事件

`NOTIFICATION_DAILY_DIGEST_ENABLED=true` 时，report 通知进入 JSONL 总线
（`data/cache/notification_bus/<day>.jsonl`），按 `logical_id` 聚合：同一逻辑事件
刷新内容时**替换**旧版本而非追加。账户决策包用 `daily_operation_sheet:<date>`，
事件告警用 `event_alert:<symbol>:<type>:<direction>`。alert 仍即时发送，成功后向
总线写入同一 `logical_id` 索引；晚间 18:00 汇总推送。测试/手工验收必须用
`send(..., test_mode=True)`，不污染生产总线。

## 聚合报告失败隔离

- `_send_notifications()` 对每个静态渠道单独发送：单渠道异常只记日志，
  不跳过后续渠道、不中断分析主流程。
- 任一静态渠道成功时 P4 降噪 reservation 写入正式记录；全部失败时释放 reservation。
- `send_to_context()` 独立于静态渠道路由与降噪记录。
- 告警通知发送失败只记录尝试，不回滚已写入的 trigger 或 DecisionSignal。

DecisionSignal 字段、脱敏、迁移与回滚边界见 [DecisionSignal 决策信号专题](decision-signals.md)。

## GitHub Actions 映射

仓库自带 `.github/workflows/00-daily-analysis.yml` 只显式导入固定变量名，P0/P3/P4/P6 的通知 key 已纳入默认 workflow 映射。下面的表格由 `scripts/generate_notification_actions_env_table.py` 从 workflow `env:` 和通知诊断元数据生成，避免手写对照表和真实 Actions 映射继续漂移。

<!-- notification-actions-env-table:start -->

| Key | Tier | Channel / feature | Actions source | Default |
| --- | --- | --- | --- | --- |
| `FEISHU_WEBHOOK_URL` | minimal | feishu | Secret | - |
| `FEISHU_WEBHOOK_SECRET` | advanced | feishu | Secret | - |
| `FEISHU_WEBHOOK_KEYWORD` | advanced | feishu | Variable or Secret | - |
| `FEISHU_APP_ID` | minimal | feishu | Secret | - |
| `FEISHU_APP_SECRET` | minimal | feishu | Secret | - |
| `FEISHU_CHAT_ID` | minimal | feishu | Variable or Secret | - |
| `FEISHU_RECEIVE_ID_TYPE` | advanced | feishu | Variable or Secret | - |
| `FEISHU_DOMAIN` | advanced | feishu | Variable or Secret | - |
| `FEISHU_SEND_AS_FILE` | advanced | feishu | Variable or Secret | - |
| `NOTIFICATION_REPORT_CHANNELS` | advanced | routing | Variable or Secret | - |
| `NOTIFICATION_ALERT_CHANNELS` | advanced | routing | Variable or Secret | - |
| `NOTIFICATION_SYSTEM_ERROR_CHANNELS` | advanced | routing | Variable or Secret | - |
| `NOTIFICATION_DEDUP_TTL_SECONDS` | advanced | noise | Variable or Secret | `0` |
| `NOTIFICATION_COOLDOWN_SECONDS` | advanced | noise | Variable or Secret | `0` |
| `NOTIFICATION_QUIET_HOURS` | advanced | noise | Variable or Secret | - |
| `NOTIFICATION_TIMEZONE` | advanced | noise | Variable or Secret | - |
| `NOTIFICATION_MIN_SEVERITY` | advanced | noise | Variable or Secret | - |
| `NOTIFICATION_DAILY_DIGEST_ENABLED` | advanced | noise | Variable or Secret | `false` |

<!-- notification-actions-env-table:end -->

默认 workflow 仍不映射 `MARKDOWN_TO_IMAGE_CHANNELS` 与 `MERGE_EMAIL_NOTIFICATION`。它们是发送形态或聚合行为开关，不是渠道凭证；在 Actions 中自动开始读取同名 Secret/Variable 会引入额外行为变化。

## 通知降噪机制

进程内降噪，只影响静态渠道，不影响机器人会话回执。默认全关。

| 配置 key | 默认值 | 说明 |
| --- | --- | --- |
| `NOTIFICATION_DEDUP_TTL_SECONDS` | `0` | 同一稳定去重 key 在 TTL 内只发送一次；`0` 关闭 |
| `NOTIFICATION_COOLDOWN_SECONDS` | `0` | 同一冷却 key 在窗口内限频；`0` 关闭 |
| `NOTIFICATION_QUIET_HOURS` | 空 | 静默时段 `HH:MM-HH:MM`，支持跨午夜 |
| `NOTIFICATION_TIMEZONE` | 空 | 静默时段时区；留空用运行时本地时区 |
| `NOTIFICATION_MIN_SEVERITY` | 空 | `info`/`warning`/`error`/`critical`；留空不过滤 |
| `NOTIFICATION_DAILY_DIGEST_ENABLED` | `false` | 开启后 report 通知进入摘要总线按 logical_id 聚合（见上文） |

严重级别默认值：`report`=info、`alert`=warning、`system_error`=error。

实现边界：去重/冷却状态是**进程内** dict（重启即丢；多 worker/多容器不共享，
per-worker 近似生效）；降噪判断异常时 fail-open 继续发送。

## 诊断

```bash
python main.py --check-notify   # 只读诊断：不发送、不写 .env、不进分析流程
```

## 回滚方式

- 恢复旧 `.env` 或清空对应 key 后重启进程；渠道键清理以 `.env.example` 为准。
- 版本回退：已删除渠道的 key 在新版本中被忽略；为避免误导应同步从 `.env` 移除。

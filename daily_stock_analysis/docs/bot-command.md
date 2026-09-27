

## 一、整体设计

```mermaid
flowchart TB
    subgraph Platforms [外部平台]
        FS[飞书（Stream 长连接）]
    end

    subgraph BotModule [bot/ 模块]
        Stream[FeishuStreamClient]
        Adapters[平台适配器]
        Dispatcher[命令分发器]
        Commands[命令处理器]
    end

    subgraph Core [现有核心模块]
        AS[AnalysisService]
        MA[MarketAnalyzer]
        NS[NotificationService]
    end

    FS -->|WebSocket im.message.receive_v1| Stream
    Stream --> Adapters
    Adapters -->|统一消息格式| Dispatcher
    Dispatcher --> Commands
    Commands --> AS
    Commands --> MA
    Commands --> NS
```

> P5（2026-09-26）后仅保留飞书 Stream 接入；钉钉/企业微信/Telegram 平台适配器已物理删除（多渠道架构史见 CHANGELOG）。Webhook HTTP 回调端点随之移除，收消息唯一机制 = Stream 长连接。



## 二、目录结构

在项目根目录新建 `bot/` 目录：

```
bot/
├── __init__.py             # 模块入口，导出主要类
├── models.py               # 统一的消息/响应模型
├── dispatcher.py           # 命令分发器（核心）
├── commands/               # 命令处理器
│   ├── __init__.py
│   ├── base.py             # 命令抽象基类
│   ├── analyze.py          # /analyze 股票/指数分析
│   ├── market.py           # /market 大盘复盘
│   ├── help.py             # /help 帮助信息
│   └── status.py           # /status 系统状态
└── platforms/              # 平台适配器（P5：仅保留飞书）
    ├── __init__.py
    ├── base.py             # 平台抽象基类
    └── feishu_stream.py    # 飞书 Stream 机器人
```

## 三、核心抽象设计

### 3.1 统一消息模型 (`bot/models.py`)

```python
@dataclass
class BotMessage:
    """统一的机器人消息模型"""
    platform: str           # 平台标识: feishu（P5 后唯一平台）
    user_id: str            # 发送者 ID
    user_name: str          # 发送者名称
    chat_id: str            # 会话 ID（群聊或私聊）
    chat_type: str          # 会话类型: group/private
    content: str            # 消息文本内容
    raw_data: Dict          # 原始请求数据（平台特定）
    timestamp: datetime     # 消息时间
    mentioned: bool = False # 是否@了机器人

@dataclass
class BotResponse:
    """统一的机器人响应模型"""
    text: str               # 回复文本
    markdown: bool = False  # 是否为 Markdown
    at_user: bool = True    # 是否@发送者
```

### 3.2 平台适配器基类 (`bot/platforms/base.py`)

```python
class BotPlatform(ABC):
    """平台适配器抽象基类"""
    
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台标识名称"""
        pass
    
    @abstractmethod
    def verify_request(self, headers: Dict, body: bytes) -> bool:
        """验证请求签名（安全校验）"""
        pass
    
    @abstractmethod
    def parse_message(self, data: Dict) -> Optional[BotMessage]:
        """解析平台消息为统一格式"""
        pass
    
    @abstractmethod
    def format_response(self, response: BotResponse) -> Dict:
        """将统一响应转换为平台格式"""
        pass
```

### 3.3 命令基类 (`bot/commands/base.py`)

```python
class BotCommand(ABC):
    """命令处理器抽象基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """命令名称 (如 'analyze')"""
        pass
    
    @property
    @abstractmethod
    def aliases(self) -> List[str]:
        """命令别名 (如 ['a', '分析'])"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """命令描述"""
        pass
    
    @property
    @abstractmethod
    def usage(self) -> str:
        """使用说明"""
        pass
    
    @abstractmethod
    async def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        """执行命令"""
        pass
```

### 3.4 命令分发器 (`bot/dispatcher.py`)

```python
class CommandDispatcher:
    """命令分发器 - 单例模式"""
    
    def __init__(self):
        self._commands: Dict[str, BotCommand] = {}
        self._aliases: Dict[str, str] = {}
    
    def register(self, command: BotCommand) -> None:
        """注册命令"""
        self._commands[command.name] = command
        for alias in command.aliases:
            self._aliases[alias] = command.name
    
    def dispatch(self, message: BotMessage) -> BotResponse:
        """分发消息到对应命令"""
        # 1. 解析命令和参数
        # 2. 查找命令处理器
        # 3. 执行并返回响应
```

## 四、已支持的命令

### 4.1 分析类

| 命令 | 别名 | 说明 | 示例 |
|------|------|------|------|
| /analyze | /a, 分析, 查 | 分析指定股票或已登记指数 | `/analyze 600519`、`/analyze sh000016`、`/analyze 上证50` |
| /market | /m, 大盘, 复盘, 行情 | 大盘复盘 | `/market` |
| /batch | /b, 批量, 全部 | 批量分析自选股 | `/batch` |
| /ask | /问股 | 对指定股票发起技能问答 | `/ask 600519` |
| /research | /深研 | 深度研究 | `/research 600519` |
| /chat | /c, 问 | 通用问答 | `/chat <问题>` |
| /strategies | /skills, 策略 | 查看可用策略/技能 | `/strategies [active]` |
| /help | /h, 帮助, ? | 显示帮助信息 | `/help` |
| /status | /s, 状态 | 系统状态 | `/status` |
| /history | /历史 | 会话历史 | `/history [session_id \| clear]` |

### 4.2 账户与交易闭环

这是"半自动交易"的主链路：**系统给决策 → 你在券商 App 手动下单 → 回飞书回填 → 系统记账并对比**。完整机制见 [`account-decision-rules.md`](account-decision-rules.md)。

| 命令 | 别名 | 说明 | 示例 |
|------|------|------|------|
| /操作单 | 今日操作, 单子 | 生成/查看当日**账户决策包**（持仓+现金的买/卖/持有结论、限价、放弃条件） | `/操作单`、`/操作单 历史 2026-09-14` |
| /成交回填 | 回填, fill | 记录真实成交（幂等；支持部分成交与可选费用） | `/成交回填 002475 卖出 100 56.50`、`… 费用12.5 确认` |
| /撤单 | cancel | 撤销当日操作单某腿（部分成交后撤剩余） | `/撤单 002475 原因` |
| /持仓 | 持仓列表, positions | 查看当前持仓 | `/持仓` |
| /持仓登记 | 记持仓, 记账 | 登记历史交易（建仓/补录） | `/持仓登记 600519 买入 100 1820.00 [手续费]` |
| /入金 | 转入, deposit | 登记入金（现金账本） | `/入金 5000 工资结余` |
| /出金 | 转出, withdraw | 登记出金 | `/出金 2000` |
| /清空持仓 | — | 清空持仓登记（谨慎） | `/清空持仓` |
| /对比 | 模拟对比, 信不信 | 模拟账本 vs 真实成交对比（含影子期周报） | `/对比`、`/对比 周报` |
| /健康 | 运行状况, 运行状态 | 系统体检：现金、决策快照、择时、集中度、告警覆盖 | `/健康` |
| /暂停交易 | 暂停, halt | 业务级 kill switch：停止一切指令生成 | `/暂停交易 出差` |
| /恢复交易 | 恢复 | 恢复指令生成 | `/恢复交易` |

### 4.3 `/操作单` 输出语义（2026-09-15 账户中心重构后）

`/操作单` 的正文是**账户决策包**，不是策略目标 diff：

```
【账户决策包 2026-09-15】
决策依据：你的规则 > 破位诊断 > 集中度风险 > 择时防守
（AI 分析只提供解释，不参与买卖决策）

◆ 000899 赣能股份（100股 · 成本11.16 · 现价9.52 · 浮亏-14.7%）
  规则状态：止损位 10.87/10.65，已跌破（低于最深一档 10.6%）
  破位诊断：连续 22 日收于止损下方 · 均线纠缠（站上 0/3 均线） · 放量破位 → 建议立即执行
  → 决策：止损卖出全部 100股 @9.52 限价（回收约 ¥952）
    开盘<9.330 放弃 · 14:50 未成交撤单

◆ 现金 ¥1,549（组合的 12.1%）
  → 决策：持有 —— E2 择时：估值分位 87.5%（>80% 高分位防守区）——不新增买入
```

要点：

- **决策对象是真实持仓与现金**，不是 CB 策略模拟组合；
- 每只持仓给"卖出 / 减仓 / 持有"明确结论 + 限价 + 放弃条件；
- 可转债策略目标降级为文末"研究观察"附注，**不再构成操作指令**；
- `/操作单 历史 [日期]` 读归档决策快照，回放"系统那天为什么这么判"的**完整判断**（每票卖出/减仓/持有判决 + 破位诊断 + 现金/E2 结论 + 依据），不只是订单；每份归档用 `content_hash` 全长重算校验，被篡改或损坏会标 ⚠。日期参数经 ISO 校验（拒绝路径穿越）。归档记录的是**当时生成的原判**（execution_mode: generated/reused/halted），非事后重算。

### `/analyze` 支持边界

- 股票：A 股 6 位数字（`600519`）、港股 `HK+5 位数字`（`hk00700`）、美股 1-5 个字母（`AAPL`、`usfd`→`USFD`）。股票名称（`贵州茅台`）由本次 Bot 入口新暴露：复用既有名称解析器（`resolve_name_to_code`）解析后提交 legacy code，不携带结构化 target。
- 已登记指数：显式代码（`sh000016`）、CSI alias（`930955.CSI` 收敛为 `csi930955`）、注册中文名（`上证50`）均可提交；指数以结构化 `AnalysisTarget` 贯穿到分析 Pipeline，`sh000016` 不会被改写为 `SH000016`。
- 未登记 CSI（如 `930956.CSI`）、旧闸门拒绝的代码形态（如 `12345`、裸 `00700`、`600519.SH`、未登记 `sh999999`）或无法识别的名称：返回明确错误，不提交任务。
- 注册表内存在等价名称歧义时：要求改用显式代码，不猜测、不进入股票名称兜底。

### Bot 指数入口在线 E2E smoke（`scripts/smoke_bot_index_entry.py`）

用于从真实 `CommandDispatcher.dispatch_async -> AnalyzeCommand -> TaskService -> StockAnalysisPipeline` 在线贯穿单标的分析，覆盖 `SH.000016` / `上证50` / `930955.CSI` 三条矩阵场景，不经过任何 webhook transport（不 mock 在线依赖、不 dry-run）。

运行（单 target 参数，`--timeout` 默认 900 秒）：

```powershell
.venv\Scripts\python.exe scripts/smoke_bot_index_entry.py "SH.000016"
.venv\Scripts\python.exe scripts/smoke_bot_index_entry.py "上证50"
.venv\Scripts\python.exe scripts/smoke_bot_index_entry.py "930955.CSI"
```

进程职责：worker 独立子进程提交并轮询（同进程 `TaskService`），输出单行 `E2E_EVENT {json}` 事件（`phase=submitted|completed|failed|timeout`，含 `target`，按阶段附加 `task_id`/`stock_code`/`result`/`error`）；父进程只负责 deadline、进程树清理和退出码（0=成功 / 1=失败 / 124=超时）。

失败语义：任务失败、completed 但结果不完整（canonical code 或注册名称与矩阵期望不符，或 `analysis_summary`/`operation_advice`/`trend_prediction` 任一为空或仅空白）即输出 `failed` 事件并不为零退出；超时清理进程树（Windows `taskkill /T /F`，POSIX 杀进程组），清理成功输出 `timeout` 事件并退出 124，清理失败则输出含清理错误的 `failed` 事件并退出 1，不回滚 DB / 报告 / 通知等既有副作用。用户 Ctrl-C 中止时父进程同样先清理进程树：清理成功透传中断，清理失败输出含清理错误的 `failed` 事件并退出 1，绝不静默吞掉清理失败。worker 的期望 code/name 来自脚本内置的矩阵权威映射（`SH.000016`/`上证50` → `sh000016`/`上证50`，`930955.CSI` → `csi930955`/`红利低波100`），不信任响应或任务结果自报身份：提交响应的 `stock_code` 与期望不符时输出含期望值与实际值的显式 mismatch 错误（failed 事件同时携带结构化 `stock_code` 实际值）；矩阵之外的 target 与非正 `--timeout` 在提交与子进程 spawn 之前即被拒绝（参数/输入错误，退出码 2）。worker 意外异常（dispatch/轮询/事件序列化）输出 `failed` 事件并退出 1（异常证据保留在 stderr），`KeyboardInterrupt` 不按普通失败处理；父进程将 worker 的任意其他退出码归一化为 1，运行时契约只暴露 0 / 1 / 124。父进程以内部 `--worker` flag 显式拉起子进程，不依赖环境变量。

前置条件：需要网络与数据源/AI 凭据配置齐全（在线链路）。本脚本不属于离线 gate，不进入 CI。

## 五、`/status` 与模型配置诊断说明

### 可配置层级与可用性判断依据

- `/status` 显示的 LLM 可用性遵循系统统一运行时优先级：
  - `LITELLM_CONFIG`（LiteLLM YAML）
  - `LLM_CHANNELS`
  - legacy provider 键（`GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY`）
- 当主模型（`LITELLM_MODEL` 或 `AGENT_LITELLM_MODEL`）在当前激活层无可用来源时，会展示“AI 服务未配置”，并保留用户可见原因行。
- 本仓库 `requirements.txt` 的运行时依赖约束为 `litellm>=1.80.10,!=1.82.7,!=1.82.8,<1.99.0`，该约束内本链路以现有兼容行为为准。
- 该诊断规则与 `GET /api/v1/system/config/setup/status` 的 LLM 检查保持一致：`LITELLM_CONFIG`/`LLM_CHANNELS` 为高优先级；模式切换时不会做静默迁移，切回旧模式由用户显式恢复历史值或回滚。

### 回退与迁移边界

- `LITELLM_CONFIG` 与 `LLM_CHANNELS` 任一生效时，下层 legacy 配置会被该层忽略（不会继续作为本次调用来源）。
- 诊断增强不进行 silent migration：不会主动清空/删除 `GEMINI_*`、`OPENAI_*`、`ANTHROPIC_*`、`LITELLM_*` 的历史值，仅在可用性诊断上提示。

### 官方兼容来源（用于排障核对）

- LiteLLM 官网：<https://docs.litellm.ai/>
- LiteLLM OpenAI Compatible 说明：<https://docs.litellm.ai/docs/providers/openai_compatible>
- OpenAI Chat API：<https://platform.openai.com/docs/api-reference/chat>
- DeepSeek API 文档：<https://api-docs.deepseek.com/>
- Kimi Moonshot 兼容说明：<https://platform.moonshot.ai/docs/guide/compatibility>
- Gemini OpenAI 兼容说明：<https://ai.google.dev/gemini-api/docs/openai>
- Ollama API 文档：<https://github.com/ollama/ollama/blob/main/docs/api.md>

## 六、收消息机制（P5 后）

收消息唯一机制 = **飞书 Stream 长连接**（`bot/platforms/feishu_stream.py`，
lark-oapi SDK WebSocket，`FEISHU_STREAM_ENABLED=true` 启用）。事件注册：
`im.message.receive_v1`（text 消息）。P5 已删除 Webhook HTTP 回调端点与
钉钉/企业微信/Telegram 平台适配器；新增平台需重建 `BotPlatform` 适配器
（见下文"扩展说明"）。

## 配置

在 [config.py](../src/config.py) 中新增机器人配置：

```python
# === 机器人配置（P5 后现行键，详见 .env.example 与 feishu-handoff.md）===
FEISHU_STREAM_ENABLED=true            # Stream 长连接收消息开关
FEISHU_APP_ID / FEISHU_APP_SECRET     # 应用凭证
FEISHU_CHAT_ID / FEISHU_RECEIVE_ID_TYPE  # 目标会话与 ID 类型
# （feishu_encrypt_key / feishu_verification_token 代码仍读，.env.example 未列）
```

## 扩展说明
### 怎样新增一个通知平台

1. 在 `bot/platforms/` 创建新文件
2. 继承 `BotPlatform` 基类
3. 实现 `verify_request`, `parse_message`, `format_response`
4. 在路由中注册 Webhook 端点

### 怎样新增新增命令

1. 在 `bot/commands/` 创建新文件
2. 继承 `BotCommand` 基类
3. 实现 `execute` 方法
4. 在分发器中注册命令

## 安全相关配置

- 支持命令频率限制（防刷）
- 敏感操作（如批量分析）可设置权限白名单

在 [config.py](../src/config.py) 中新增机器人安全配置：

```python
    bot_rate_limit_requests: int = 10     # 频率限制：窗口内最大请求数
    bot_rate_limit_window: int = 60       # 频率限制：窗口时间（秒）
    bot_admin_users: List[str] = field(default_factory=list)  # 管理员用户 ID 列表，限制敏感操作
```

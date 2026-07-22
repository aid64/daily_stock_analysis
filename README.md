# 📈 daily_stock_analysis

> 基于 AI 大模型的多市场自选股智能分析系统
>
> Forked from [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) (v3.26.0)

## 项目概述

每日自动分析自选股（A 股/港股/美股/日股/韩股/台股），基于 AI 大模型生成决策仪表盘，支持 14 种通知渠道推送分析报告。

## 目录结构

```
daily_stock_analysis/          ← Git 仓库根目录
├── README.md                  ← 本文件
├── CHANGELOG.md               ← 项目开发日志
├── .gitignore                 ← Git 忽略规则
├── .gitattributes             ← Git 换行符规则
├── .env                       ← 环境配置 (已 gitignore)
├── .env.example               ← 环境配置模板
│
├── main.py                    ← CLI 入口
├── server.py                  ← FastAPI 服务
├── webui.py                   ← WebUI 启动
├── requirements.txt           ← Python 依赖
├── pyproject.toml             ← Python 项目配置
│
├── src/                       ← Python 核心源码
├── api/                       ← FastAPI REST API
├── apps/                      ← 前端应用 (React + Electron)
├── data_provider/             ← 数据源层 (10+ fetcher)
├── strategies/                ← 15 种量化策略 YAML
├── bot/                       ← 聊天机器人框架
├── tests/                     ← 测试套件 (150+ 文件)
├── templates/                 ← Jinja2 报告模板
├── scripts/                   ← 构建/工具脚本
├── docker/                    ← Docker 配置
├── .github/                   ← GitHub CI/CD
│
├── docs/                      ← 项目文档 (本地 + 上游)
│   ├── INDEX.md               ← 文档索引
│   ├── project-analysis.md    ← 项目深度分析报告
│   ├── upstream-README.md     ← 上游官方 README
│   └── ...                    ← 上游官方文档
│
├── archive/                   ← 历史参考
│   └── DEPRECATED.md          ← 废弃 Fork 说明
│
└── Daily Stock Analysis/      ← 打包后的 Electron 桌面应用 (gitignored)
```

简体中文 | [English](docs/README_EN.md) | [繁體中文](docs/README_CHT.md)

</div>

## 💖 赞助商 (Sponsors)
<div align="center">
  <p align="center">
    <a href="https://open.anspire.cn/dsa?share_code=QFBC0FYC" target="_blank"><img src="./docs/assets/anspire.png" alt="Anspire Open 一站式模型和搜索服务" width="300" height="141" style="width: 300px; height: 141px; object-fit: contain;"></a>
    <a href="https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis" target="_blank"><img src="./docs/assets/serpapi_banner_zh.png" alt="轻松抓取搜索引擎上的实时金融新闻数据 - SerpApi" width="300" height="141" style="width: 300px; height: 141px; object-fit: contain;"></a>
  </p>
</div>


## 🖥️ 产品预览

<p align="center">
  <img src="docs/assets/readme_workspace_tour_20260510.gif" alt="DSA Web 工作台演示" width="720">
</p>

## ✨ 功能特性

| 能力 | 覆盖内容 |
|------|------|
| AI 决策报告 | 核心结论、评分、趋势、买卖点位、风险警报、催化因素、操作检查清单 |
| 多市场数据聚合 | 覆盖 A股、港股、美股、日股、韩股、台股和 ETF，支持行情、K 线、技术指标、新闻、公告、基本面与报告辅助数据；不同市场的数据源和能力边界见 [市场支持边界](docs/market-support.md) |
| Web / 桌面工作台 | 手动分析、任务进度、历史报告、完整 Markdown、回测、持仓、配置管理、浅色 / 深色主题 |
| Agent 策略问股 | 多轮追问，支持均线、缠论、波浪、趋势、热点、事件、成长、预期等 15 种内置策略，覆盖 Web/Bot/API |
| 智能导入与补全 | 图片、CSV/Excel、剪贴板导入；股票代码/名称/拼音/别名补全 |
| 自动化与推送 | GitHub Actions、Docker、本地定时任务、FastAPI 服务和企业微信/飞书/Telegram/Discord/Slack/邮件推送 |

> 功能细节、字段契约、基本面 P0 超时语义、交易纪律、数据源优先级、Web/API 行为请看 [完整配置与部署指南](docs/full-guide.md)。

### 技术栈与数据来源

| 类型 | 支持 |
|------|------|
| AI 模型 | [Anspire](https://open.anspire.cn/dsa?share_code=QFBC0FYC)、[AIHubMix](https://aihubmix.com/?aff=CfMq)、Gemini、OpenAI 兼容、DeepSeek、通义千问、Claude、Ollama 本地模型等 |
| 行情数据 | [TickFlow](https://tickflow.org/auth/register?ref=WDSGSPS5XC)、AkShare、Tushare、Pytdx、Baostock、YFinance、Longbridge |
| 新闻搜索 | [Anspire](https://open.anspire.cn/dsa/?share_code=QFBC0FYC)、[SerpAPI](https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis)、[Tavily](https://tavily.com/)、[Bocha](https://open.bocha.cn/)、[Brave](https://brave.com/search/api/)、[MiniMax](https://platform.minimaxi.com/)、SearXNG |
| 社交舆情 | [Stock Sentiment API](https://api.adanos.org/docs)（Reddit / X / Polymarket，仅美股，可选） |

> 项目默认内置 AkShare、Baostock、YFinance 等免费行情源，可零配置运行；免费源受上游限流、接口变动和网络波动影响，稳定性不保证。长期定时、批量分析或更稳定行情建议配置 TickFlow、Tushare、Longbridge 等 token 型数据源，适用市场、Actions 映射和 fallback 规则见 [数据源配置](docs/full-guide.md#数据源配置)。

## 🚀 快速开始

### 方式一：[GitHub Actions（推荐）](https://www.bilibili.com/video/BV11FEb66EXG/)

> 5 分钟完成部署，零成本，无需服务器。


#### 1. Fork 本仓库

点击右上角 `Fork` 按钮（顺便点个 Star⭐ 支持一下）

#### 2. 配置 Secrets

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

**AI 模型配置（至少配置一个）**

默认先选一个模型服务商并填写 API Key；需要多模型、图片识别、本地模型或高级路由时，再参考 [LLM 配置指南](docs/LLM_CONFIG_GUIDE.md)。

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `ANSPIRE_API_KEYS` | [Anspire](https://open.anspire.cn/dsa?share_code=QFBC0FYC) API Key，一Key同时启用全球热门大模型和联网搜索，本项目新用户提供30元等额的免费额度（GLM5.2、GPT等模型特惠中） | **推荐** |
| `AIHUBMIX_KEY` | [AIHubMix](https://aihubmix.com/?aff=CfMq) API Key，一Key切换使用全系模型，无需科学上网，本项目可享 10% 优惠 | **推荐** |
| `GEMINI_API_KEY` | Google Gemini API Key | 可选 |
| `ANTHROPIC_API_KEY` | Anthropic Claude API Key | 可选 |
| `OPENAI_API_KEY` | OpenAI 兼容 API Key（支持 DeepSeek、通义千问等） | 可选 |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | 使用 OpenAI 兼容服务时填写 | 可选 |

> Ollama 更适合本地 / Docker 部署，GitHub Actions 推荐使用云端 API。

**通知渠道配置（至少配置一个）**

| Secret 名称 | 说明 |
|------------|------|
| `WECHAT_WEBHOOK_URL` | 企业微信机器人 |
| `FEISHU_WEBHOOK_URL` | 飞书机器人 |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Telegram |
| `DISCORD_WEBHOOK_URL` | Discord Webhook |
| `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` | Slack Bot |
| `EMAIL_SENDER` + `EMAIL_PASSWORD` | 邮件推送 |

更多渠道、签名校验、分组邮件、Markdown 转图片等配置见 [通知渠道详细配置](docs/full-guide.md#通知渠道详细配置)。

**自选股配置（必填）**

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `STOCK_LIST` | 自选股代码，如 `600519,hk00700,AAPL,7203.T,005930.KS,2330.TW` | ✅ |

**新闻源配置（推荐）**

新闻源会显著影响舆情、公告、事件和催化因素质量，建议至少配置一个搜索服务。

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `ANSPIRE_API_KEYS` | [Anspire AI Search](https://open.anspire.cn/dsa?share_code=QFBC0FYC)：汇聚全球舆情信息，适配A股、美股、港股等新闻和舆情检索；同一Key可复用大模型服务，本项目新用户提供免费30元等额的免费点数 | **推荐** |
| `SERPAPI_API_KEYS` | [SerpAPI](https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis)：搜索引擎结果补强，适合实时金融新闻 | **推荐** |
| `TAVILY_API_KEYS` | [Tavily](https://tavily.com/)：通用新闻搜索 API | 可选 |
| `BOCHA_API_KEYS` | [博查搜索](https://open.bocha.cn/)：中文搜索优化，支持 AI 摘要 | 可选 |
| `BRAVE_API_KEYS` | [Brave Search](https://brave.com/search/api/)：隐私优先，美股资讯补强 | 可选 |
| `MINIMAX_API_KEYS` | [MiniMax](https://platform.minimaxi.com/)：结构化搜索结果 | 可选 |
| `SEARXNG_BASE_URLS` | SearXNG 自建实例：无配额兜底，适合私有部署 | 可选 |

更多搜索源、社交舆情和降级规则见 [搜索服务配置](docs/full-guide.md#搜索服务配置)。

**行情数据源配置（可选）**

> 默认使用 AkShare、Baostock、YFinance 等免费数据源，日志中"未配置"的提示不影响运行。
> 如需更稳定的行情，可按市场配置以下 Secret：

| Secret 名称 | 适用市场 | 说明 |
|------------|:--------:|------|
| `TUSHARE_TOKEN` | A 股 | 提升历史行情稳定性 |
| `LONGBRIDGE_OAUTH_CLIENT_ID` + `LONGBRIDGE_OAUTH_TOKEN_CACHE_B64` | 港股/美股 | 补齐量比、换手率、PE 等字段 |

> 详见 [数据源配置](docs/full-guide.md#数据源配置)。

#### 3. 启用 Actions

`Actions` 标签 → `I understand my workflows, go ahead and enable them`

#### 4. 手动测试

`Actions` → `每日股票分析` → `Run workflow` → `Run workflow`

#### 完成

默认每个**工作日 18:00（北京时间）**自动执行，也可手动触发。默认非交易日（含 A/H/US 节假日）不执行；强制运行、交易日检查、断点续传等规则见 [完整指南](docs/full-guide.md#定时任务配置)。

### 方式二：[客户端配置教程](https://www.bilibili.com/video/BV11FEb66Eyr/) / 本地运行 / Docker 部署

## 快速开始

```bash
cd source_code

# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 STOCK_LIST 和 LLM API Key

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行分析
python main.py                          # 单次分析
python main.py --schedule               # 定时模式 (每日 18:00)
python main.py --serve                  # API 服务 + Web 工作台
python -m uvicorn server:app --reload   # 仅 API 服务
```

## 文档导航

| 文档 | 说明 |
|------|------|
| [docs/project-analysis.md](docs/project-analysis.md) | 项目深度分析 (功能/架构/不足) |
| [CHANGELOG.md](CHANGELOG.md) | 版本演进与开发日志 |
| [docs/upstream-README.md](docs/upstream-README.md) | 上游官方 README（原版） |
| [docs/INDEX.md](docs/INDEX.md) | 文档中心总索引 |
| [docs/full-guide.md](docs/full-guide.md) | 完整配置与部署指南 |
| [docs/LLM_CONFIG_GUIDE.md](docs/LLM_CONFIG_GUIDE.md) | LLM 模型配置指南 |

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10+, FastAPI, SQLAlchemy + SQLite |
| AI 层 | LiteLLM 多模型统一客户端 |
| 前端 | React 19, TypeScript, Vite, TailwindCSS |
| 桌面 | Electron |
| 部署 | Docker, GitHub Actions |
| 测试 | Pytest (150+), Playwright E2E |

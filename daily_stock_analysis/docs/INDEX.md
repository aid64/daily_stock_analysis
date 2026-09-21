# 文档中心

这里是项目文档入口。README 负责项目概览和快速开始；更完整的配置、部署、功能说明和排障内容从这里进入。

## 按场景选择

| 我想要 | 先看 | 继续看 |
| --- | --- | --- |
| 快速了解项目能做什么 | [README](../../README.md) | [完整配置与部署指南](full-guide.md) |
| 第一次把项目跑起来 | [小白客户端安装与配置](beginner-client-setup.md) | [完整配置与部署指南](full-guide.md) |
| 配置大模型渠道 | [LLM 配置指南](LLM_CONFIG_GUIDE.md) | [LLM 服务商配置指南](llm-providers.md)（含 xAI Grok 直连） |
| 配置推送通知 | [通知能力基线](notifications.md) | [飞书配置交接说明](feishu-handoff.md)、[完整配置与部署指南](full-guide.md) |
| 部署到服务器或云平台 | [部署指南](DEPLOY.md) | [云端 WebUI 部署](deploy-webui-cloud.md)、[Zeabur 部署](docker/zeabur-deployment.md) |
| 备份账本 / 崩溃恢复 | [单机灾难恢复（DR）](disaster-recovery.md) | 账本备份脚本、Windows 计划任务、三类崩溃场景还原步骤 |
| 决策层参数为何这么定 | [在线决策层变更留痕](decision-layer-changelog.md) | 结构锚价位/破位分档/组合器三层参数、回放基线（分档过度设计、价位锚才是开关）、变更留痕规范 |
| 使用 Bot / IM 接入 | [Bot 命令与接入](bot-command.md) | [Bot 平台配置](bot/)（飞书） |
| 排查运行问题 | [FAQ](FAQ.md) | [更新日志](CHANGELOG.md) |
| 处理数据源失败或降级 | [数据源稳定性与故障处理图示](data-source-stability.md) | [FAQ](FAQ.md) |
| 参与开发或提交 PR | [贡献指南](CONTRIBUTING.md) | [API 规格](architecture/api_spec.json) |

## 快速开始

| 文档 | 内容 |
| --- | --- |
| [README](../../README.md) | 项目定位、核心能力、快速开始、推送效果 |
| [小白客户端安装与配置](beginner-client-setup.md) | 面向不会代码用户的客户端下载、Anspire Open / AIHubMix 模型配置、新闻源配置和常见问题 |
| [完整配置与部署指南](full-guide.md) | 环境准备、运行方式、配置说明、部署路径和常见问题 |
| [FAQ](FAQ.md) | 常见配置、模型、通知、部署和运行问题 |
| [数据源稳定性与故障处理图示](data-source-stability.md) | Tushare、TickFlow、AkShare、Efinance、YFinance、Longbridge 等已接入源的使用场景、fallback 链路和推荐配置 |
| [更新日志](CHANGELOG.md) | 版本变化、能力调整和迁移说明 |

## 配置

| 文档 | 内容 |
| --- | --- |
| [LLM 配置指南](LLM_CONFIG_GUIDE.md) | 大模型渠道、三层配置、Web 设置页和常见模型配置 |
| [LLM 服务商配置指南](llm-providers.md) | Provider 预设、Actions 映射、错误分类和诊断建议 |
| [LiteLLM YAML 示例](examples/litellm_config.example.yaml) | LiteLLM 多渠道配置示例 |
| [Grok Bot Skill 示例](examples/grok_bot/README.md) | 给 Grok Bot 粘贴的最小 Skill，调用现有 REST |
| [通知能力基线](notifications.md) | 企业微信、飞书、Telegram、Discord、Slack、邮件等通知渠道配置 |
| [飞书配置交接说明](feishu-handoff.md) | 飞书三种接入方式的区别、当前实际配置、发送/交互链路、渠道路由、每日摘要与排障手册（交接真源） |
| [账户决策规则溯源](account-decision-rules.md) | 买卖建议凭什么产生：规则来源（现为结构锚生成的价位）、验证状态、为什么目前只有卖、破位分级判断与未经验证的诚实边界 |
| [策略目录](strategy-catalog.md) | 策略从调研到部署的唯一真源：风险谱系（零风险→高风险）、每条策略规则/实证/风险来源、状态机（登记→复现→影子→准入）、已否决与排除项；对应仓库根 `strategies.yaml` 注册表与 `strategy_engine` 引擎 |
| [Tushare 股票列表指南](TUSHARE_STOCK_LIST_GUIDE.md) | Tushare 股票列表相关配置和使用说明 |

## 项目演进与重构（规划真源）

> 阅读顺序：先看当前生效的重构计划，再按需追溯策略目录与执行蓝图。带 SUPERSEDED/OUTDATED 标注的文档仅作历史记录，不指导当前开发。

| 文档 | 状态 | 内容 |
| --- | --- | --- |
| [策略中心重构计划](REFACTOR-PLAN-STRATEGY-CENTRIC.md) | **规划唯一真源（v4.1）** | 全项目重构路线：实测盘点、dsa=账户真源+quant=回测裁决所的方向、模块增删总表、P0-P5 阶段与三件套验收；v4.1 吸收 14 条外部审阅（P0.5 备份前置/P0.9 在线层回放/非交易日门/裁决新鲜度门等） |
| [策略目录](strategy-catalog.md) | 生效（策略面真源） | 策略登记与状态机（与重构计划 P1-P3 配套） |
| [账户决策规则溯源](account-decision-rules.md) | 生效但**部分过时** | 决策依据溯源；"价位=成本±%机械模板"对应 P0 前旧价位，P0 已用结构锚重生，见文内更正说明 |
| [执行蓝图](EXECUTION-BLUEPRINT.md) | 已交付形态说明（①-④） | 操作单/账本/治理/砍除产出的事实描述；⑤及以后规划权归重构计划 |
| [现状审计（2026-09-10）](ASSISTANT-GAP-AUDIT-20260910.md) | 历史记录 | 半自动助手当时的差距审计，问题多已在重构计划中处置 |
| [闭环修复计划](CLOSURE-FIX-PLAN.md) / [桌面端大修计划](DESKTOP-OVERHAUL-PLAN.md) | 历史/桌面端冻结 | 早期专项计划；桌面端已按重构计划冻结 |
| [项目深度分析](project-analysis.md) | **OUTDATED** | 描述早期三层目录结构，与当前双项目布局不符 |

## 使用专题

| 文档 | 内容 |
| --- | --- |
| [Bot 命令与接入](bot-command.md) | Bot 命令、Webhook、平台接入和回调说明 |
| [Bot 平台配置](bot/) | 飞书、钉钉、Discord 等 Bot 配置截图和补充说明 |
| [实时告警中心](alerts.md) | EventMonitor 基线、Web 规则管理、通知结果、冷却状态和 Phase 边界 |
| [个股研究聚合 API](stock-profile-api.md) | 单一 stock-profile 契约、分块质量状态、代码归一与后续 Web 边界 |
| [DecisionSignal 决策信号专题](decision-signals.md) | AI 建议池字段语义、API、Web 展示、告警/通知/组合风险联动、后验评估、脱敏、迁移与回滚 |
| [ResearchArtifact 结构化研究产物](research-artifact.md) | structured_report 字段、Thesis / Evidence / Invalidation / Next Action / Data Quality 契约和旧报告兼容边界 |
| [资讯 / 情报源](intelligence-sources.md) | RSS/Atom 合规资讯源配置、测试、拉取、去重、存储、查询与安全边界 |
| [分析上下文包契约、运行态消费与可见性](analysis-context-pack.md) | AnalysisContextPack 首版范围、字段质量状态、P1/P2 内部契约、P3 Prompt 摘要消费、P4 历史/API/Web 低敏可见性、P5 数据质量评分、P6 迁移回滚与源码锚点；完整指南补充 #1386 阶段感知分析、迁移与回滚入口 |
| [图片识别 Prompt](image-extract-prompt.md) | 图片识别股票信息的 Prompt 与使用边界 |
| [OpenClaw Skill 集成](openclaw-skill-integration.md) | OpenClaw / Skill 外部集成说明 |

## 部署与打包

| 文档 | 内容 |
| --- | --- |
| [部署指南](DEPLOY.md) | 服务器部署、Docker、systemd、Supervisor 等部署方式 |
| [云端 WebUI 部署](deploy-webui-cloud.md) | 云服务器访问 WebUI 的部署说明 |
| [Zeabur 部署](docker/zeabur-deployment.md) | Zeabur 平台部署说明 |
| [桌面端打包说明](desktop-package.md) | Electron 桌面端和 Web 构建产物打包说明 |

## 参考与开发

| 文档 | 内容 |
| --- | --- |
| [API 规格](architecture/api_spec.json) | FastAPI OpenAPI 规格产物 |
| [贡献指南](CONTRIBUTING.md) | Issue、PR、测试、文档同步和协作要求 |
| [半自动交易助手执行蓝图](EXECUTION-BLUEPRINT.md) | 终局形态、五阶段执行顺序（布线/数据地基/账本/砍除/慢线）、验收标准与执行注意事项 |
| [代码规范](coding-standards.md) | 工具链、目录结构、Python 代码规范、配置项流程、测试与文档规范、AI 协作资产与验证矩阵 |

## 多语言

| 文档 | 内容 |
| --- | --- |
| [英文文档索引](INDEX_EN.md) | English documentation index |
| [英文 README](README_EN.md) | English project overview and quick start |
| [繁中 README](README_CHT.md) | 繁體中文項目概覽與快速開始 |

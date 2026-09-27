# 文档中心

这里是项目文档入口。README 负责项目概览和快速开始；更完整的配置、部署、功能说明和排障内容从这里进入。

## 按场景选择

| 我想要 | 先看 | 继续看 |
| --- | --- | --- |
| 快速了解项目能做什么 | [README](../../README.md) | [完整配置与部署指南](full-guide.md) |
| 第一次把项目跑起来 | [小白客户端安装与配置](beginner-client-setup.md) | [完整配置与部署指南](full-guide.md) |
| 配置大模型渠道 | [LLM 配置指南](LLM_CONFIG_GUIDE.md) | [LLM 服务商配置指南](llm-providers.md)（含 xAI Grok 直连） |
| 配置推送通知 | [飞书配置交接说明](feishu-handoff.md) | [通知能力基线](notifications.md)（**⚠ 过时**：P5 后仅剩飞书渠道，文中多渠道内容失真，待重写）、[完整配置与部署指南](full-guide.md) |
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
| [通知能力基线](notifications.md) | 已重写（09-27 U5）：飞书单渠道基线 + 机制说明（路由/摘要总线/降噪/失败隔离/message_id 留存）；配置与排障以 [飞书配置交接说明](feishu-handoff.md) 为现行真源 |
| [飞书配置交接说明](feishu-handoff.md) | 飞书三种接入方式的区别、当前实际配置、发送/交互链路、渠道路由、每日摘要与排障手册（**通知配置现行真源**；缺 09-26 新增的发送侧 `feishu_dispatch.jsonl` 台账说明，待补） |
| [Tushare 股票列表指南](TUSHARE_STOCK_LIST_GUIDE.md) | Tushare 股票列表相关配置和使用说明 |

## 项目演进与重构（规划真源）

> 阅读顺序：先看当前生效的重构计划，再按需追溯策略目录与执行蓝图。带 SUPERSEDED/OUTDATED 标注的文档仅作历史记录，不指导当前开发。
> 从属链（v5.0 第七节）：重构计划 = 规划唯一真源；执行蓝图 = 已交付形态说明；策略目录 = 策略面登记真源；`strategies.yaml` = 策略参数真源（U0 确认台账为旁路 JSONL，不改写本文件）。

| 文档 | 状态 | 内容 |
| --- | --- | --- |
| [策略中心重构计划](REFACTOR-PLAN-STRATEGY-CENTRIC.md) | **规划唯一真源（v5.0 使用中心计划）** | 核心论点"建设已超前于使用"（真实账本成交停 09-07、零执行 19 天）；中心从建议正确性转向：**U0 意图归化**（规则逐条用户签署——已生效：R1/R3/R4/R5 确认、R2 停用，台账 `data/rule_confirmations.jsonl` + 消费端门控）、**U1 执行漏斗**（第一步 message_id 留存已落地）、**U2 建议休眠**、**U4 耐久性**（备份任务 0x800710E0 已修、非交易日门补齐、打新非交易日守护——均已上线）、U3 后台线等 quant 产物。历史进度：P0✅→P0.5✅（09-26 修复首日成功，观察 09-27~29）→P0.9◐→P1✅→P1.5✅（代码/替代验收，首笔真实执行未发生）→P2✅→P3◐→P4◐→P4.5✅→P5◐。**第二波待做**：U1 漏斗（回应命令+聚合）、U2、卡片按钮形态 |
| [策略目录](strategy-catalog.md) | 生效（策略面真源） | 策略登记与状态机；第五节含 09-26 两项目对账（17 池版 etf_momentum=管道第一候选，社区四资产版"未复现不推进"） |
| [账户决策规则溯源](account-decision-rules.md) | 生效（头部已附 09-26 更正注） | 决策依据溯源；"价位=成本±%"为 P0 前旧描述，P0 起结构锚，见文内更正说明 |
| [执行蓝图](EXECUTION-BLUEPRINT.md) | 已交付形态说明（①-⑤ 全 ✅） | 操作单/账本/治理/砍除/收尾产出的事实描述；规划权归重构计划 v5.0 |
| 历史文档（审计/闭环/桌面/深度分析/run-diagnostics 等） | 已删除（09-27 U5） | 均为自标 ARCHIVED/OUTDATED 的时点记录，git 历史可回溯；现行状态以本表与重构计划为准 |
| **策略部署管道真源链** | **跨项目契约** | `strategy-catalog.md`（策略面）→ 仓库根 `strategies.yaml`（参数/状态/证据引用）→ `src/services/strategy_intake.py`（裁决消费端，输入 `quant_research/output/strategy_intake/`，schema `strategy_intake_verdict_v1`，新鲜度门 5 交易日）→ `strategy_engine`（只评估 active）。**当前断点**：intake 目录不存在、quant 侧无裁决报告生产脚本，且 quant 自 09-13 起 8 周功能冻结（约 11-08 期满）——首批裁决报告的产出需冻结期满或用户豁免；ETF 动量的"禁止实盘（HANDOFF:47）vs shadow 候选"定性对账见 quant `HANDOFF.md` 第 0.3 节 |

## 运维与可靠性

| 文档 | 内容 |
| --- | --- |
| [单机灾难恢复（DR）](disaster-recovery.md) | 账本备份对象（portfolio 8 表+.env+strategies.yaml+快照/操作单）、Windows 计划任务注册命令、三类崩溃场景（账本损坏/配置丢失/整机迁移）还原步骤 |
| [在线决策层变更留痕](decision-layer-changelog.md) | 结构锚价位/破位分档/组合器三层参数清单、基线回放结论（价位锚 window 才是卖出开关）、变更留痕规范——改这三层参数必附回放 diff |

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

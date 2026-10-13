# Domain Docs

## Repository Structure

此 Git 仓库包含两个独立运行项目：

- `daily_stock_analysis/`：股票分析产品 + 半自动交易助手（API、Web、Desktop、报告、通知、bot 操作单/持仓命令、模拟账本）。
- `quant_research/`：量化研究、历史数据、回测和交易实验（策略信号的生产者，经 `output/paper_tracking/current_plan.json` 契约供 dsa 消费）。
- 集成契约：两项目只通过 `quant_research/output/paper_tracking/current_plan.json` 与 `daily_stock_analysis/output/simulation_ledger/` 文件交互，禁止跨项目 import。

不得把它们当作同一个应用。跨项目工作必须明确说明集成契约。

## Cross-Project Contracts

**新增任何跨项目输出文件，必须先在本节登记**（登记后才可实现）。

| 契约 | 方向 | 形态 | 说明 |
| --- | --- | --- | --- |
| `quant_research/output/paper_tracking/current_plan.json` | quant → dsa | 文件 | schema `cb_current_plan_v2`（DB 级兼容 v1）；dsa `cb_signal_bridge` 消费生成操作单。`targets={}` 时 `load_cb_plan` 返回 `None` |
| runner ↔ quant 子进程 | dsa → quant | **子进程 stdout JSON** | `scripts/compare_deck_runner.py` 以 `subprocess` 调 `quant_research/scripts/extract_mwu_targets.py`（G/H 槽目标）并解析其 stdout JSON；同时读 `current_plan.json`（E 槽）。stdout 解析需容忍非 JSON 前置噪声（如 tushare login 日志） |
| `daily_stock_analysis/output/simulation_ledger/` | dsa 内部 | 文件 | 模拟账本（不跨项目） |

## Known Boundary Drift（受控例外，登记后允许，收口路径见右）

以下 dsa 侧代码**直接读取 quant PIT 数据文件**（`quant_research/data/pit/convertible_pit.parquet`），属于跨项目 import/读文件禁令的**已登记例外**——先如实登记，后续把所需指标/指纹经 `current_plan.json` 契约发布后再收口：

| 位置 | 用途 | 收口方向 |
| --- | --- | --- |
| `src/services/decision_snapshot_service.py:34`（`PIT_PARQUET`） | 决策快照指纹/指标 | 指标经 current_plan 发布 |
| `bot/commands/health.py:209` | `/健康` E2 择时行（PIT 缺失时显式告警，不静默） | 同上 |
| `src/services/strategies/e2_timing.py:30` | E2 估值分位择时 | 同上 |
| `src/services/daily_pipeline_service.py:294` | 每日管线 E2 分位 | 同上 |

**未来扩展**：若 quant 侧发布多策略信号，走 `cb_current_plan_v2` 顶层不动、**新增独立 `paper_deck` 子 namespace**（严格子 schema、独立解析、单一原子发布者）；`targets={}` 时 `load_cb_plan` 返回 `None` 的语义不得影响纸面子计划。

## Before Exploring

先读取仓库根 `CONTEXT.md`，或在存在时读取 `CONTEXT-MAP.md`；再读取相关的 `docs/adr/`。这些文件不存在时静默继续，只有确实产生术语或决策时才创建。

## Collaboration Layout

本仓库采用单一协作上下文：根 `CONTEXT.md` 记录仓库级术语，`docs/adr/` 记录仓库级决策。可能产生歧义时，必须明确使用 `daily_stock_analysis` 或 `quant_research` 的项目限定词。

## Local Task Names

主项目使用 `.scratch/dsa-<feature>/`；量化研究使用 `.scratch/quant-<feature>/`。

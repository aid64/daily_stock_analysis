# Domain Docs

## Repository Structure

此 Git 仓库包含两个独立运行项目：

- `daily_stock_analysis/`：股票分析产品 + 半自动交易助手（API、Web、Desktop、报告、通知、bot 操作单/持仓命令、模拟账本）。
- `quant_research/`：量化研究、历史数据、回测和交易实验（策略信号的生产者，经 `output/paper_tracking/current_plan.json` 契约供 dsa 消费）。
- 集成契约：两项目只通过 `quant_research/output/paper_tracking/current_plan.json` 与 `daily_stock_analysis/output/simulation_ledger/` 文件交互，禁止跨项目 import。

不得把它们当作同一个应用。跨项目工作必须明确说明集成契约。

## Before Exploring

先读取仓库根 `CONTEXT.md`，或在存在时读取 `CONTEXT-MAP.md`；再读取相关的 `docs/adr/`。这些文件不存在时静默继续，只有确实产生术语或决策时才创建。

## Collaboration Layout

本仓库采用单一协作上下文：根 `CONTEXT.md` 记录仓库级术语，`docs/adr/` 记录仓库级决策。可能产生歧义时，必须明确使用 `daily_stock_analysis` 或 `quant_research` 的项目限定词。

## Local Task Names

主项目使用 `.scratch/dsa-<feature>/`；量化研究使用 `.scratch/quant-<feature>/`。

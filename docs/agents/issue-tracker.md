# Issue Tracker: Local Markdown

Issue 和 spec 使用 `.scratch/` 下的 Markdown 文件管理。

## Conventions

- 主项目任务使用 `.scratch/dsa-<feature-slug>/`；量化研究任务使用 `.scratch/quant-<feature-slug>/`。
- Spec 位于 `.scratch/<feature-slug>/spec.md`。
- 实现任务位于 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号。
- 每个任务文件顶部使用 `Status:` 标注状态；状态词汇见 `triage-labels.md`。
- 讨论和历史记录追加在 `## Comments` 标题下。

## Publish and Fetch

当 skill 要求发布 issue 或 spec 时，在 `.scratch/<feature-slug>/` 新建文件；除非用户明确要求，不创建远端 Issue。当 skill 要求读取任务时，读取用户提供路径对应的本地 Markdown 文件。

## Wayfinding

- Map：`.scratch/<effort>/map.md`。
- 子任务：`.scratch/<effort>/issues/<NN>-<slug>.md`，顶部包含 `Type:` 和 `Status:`。
- 依赖：`Blocked by: NN, NN`；所有依赖 resolved 后任务才解除阻塞。
- 领取：开始前写入 `Status: claimed`。
- 完成：追加 `## Answer`，设置 `Status: resolved`，并更新 map 的决策记录。

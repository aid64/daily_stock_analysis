# 代码规范与项目结构

本文档汇总本仓库已有的代码规范、工具链配置与目录结构约定，便于新成员快速对齐。
**本文件是现有约定的总结，不引入新规则**；与 `AGENTS.md` 冲突时以 `AGENTS.md` 为准。

## 1. 工具链

仓库已配置以下工具，配置位于 `pyproject.toml` 与 `setup.cfg`：

| 工具 | 配置位置 | 关键参数 | 用途 |
|------|----------|----------|------|
| black | `pyproject.toml` `[tool.black]` | `line-length=120`，`target-version=['py310','py311','py312']` | 代码格式化 |
| isort | `pyproject.toml` `[tool.isort]` + `setup.cfg` `[isort]` | `profile=black`，`line_length=120` | import 排序 |
| flake8 | `setup.cfg` `[flake8]` | `max-line-length=120`，忽略 `E501,W503,E203,E402` | 语法与未定义名称检查（CI 仅跑 `E9,F63,F7,F82`） |
| bandit | `pyproject.toml` `[tool.bandit]` | `exclude_dirs=["tests"]`，`skips=["B101"]` | 安全扫描（测试中允许 assert） |
| pytest | `setup.cfg` `[tool:pytest]` | `testpaths=.`，`python_files=test_*.py`，markers: `unit/integration/network` | 测试运行 |

### 1.1 CI 验证命令

```bash
# 完整后端验证（CI 必跑）
./scripts/ci_gate.sh

# 分阶段验证
./scripts/ci_gate.sh syntax        # 仅 py_compile 关键模块
./scripts/ci_gate.sh flake8        # 仅 flake8 关键错误
./scripts/ci_gate.sh deterministic # 本地确定性测试
./scripts/ci_gate.sh offline-tests # pytest -m "not network"

# 格式化与 lint（本地推荐）
black . && isort .
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics

# 网络相关测试（非阻断）
pytest -m network
```

### 1.2 pytest marker 约定

- `@pytest.mark.unit` — 快速离线单元测试
- `@pytest.mark.integration` — 服务级集成测试，不依赖外部网络
- `@pytest.mark.network` — 需要外部网络或第三方服务的测试（CI 默认跳过）

CI 通过 `pytest -m "not network"` 排除网络测试，避免外部依赖导致的 flaky。

## 2. 目录结构

仓库根目录的职责边界（与 `AGENTS.md` §3 一致）：

```
daily_stock_analysis/
├── src/                    # 后端核心逻辑
│   ├── core/              # 主流程编排
│   ├── services/          # 业务服务层
│   ├── repositories/      # 数据访问层
│   ├── reports/           # 报告生成
│   ├── schemas/           # Schema / 数据结构
│   ├── agent/             # Agent / LLM 适配
│   └── patches/           # 第三方库补丁
├── data_provider/         # 多数据源适配与 fallback
├── api/                   # FastAPI API
│   ├── v1/endpoints/      # 路由
│   ├── v1/schemas/        # 请求/响应 schema
│   └── middlewares/       # 中间件
├── bot/                   # 机器人接入
│   ├── commands/          # 命令处理
│   └── platforms/         # 平台适配
├── apps/
│   ├── dsa-web/           # React Web 前端
│   └── dsa-desktop/       # Electron 桌面端
├── scripts/               # 本地脚本
├── .github/
│   ├── workflows/         # CI / 发布 / 每日任务
│   └── scripts/           # GitHub Actions 自动化脚本
├── docker/                # Docker 配置
├── tests/                 # pytest 测试
├── docs/                  # 文档与说明
├── archive/               # 已废弃的 fork 内容（只读归档）
├── .claude/skills/        # AI 协作 skill（入库）
└── .claude/reviews/       # AI 协作产物（本地，不入库）
```

### 2.1 改动边界硬规则

- 后端逻辑放 `src/`、`data_provider/`、`api/`、`bot/`
- Web 前端改动在 `apps/dsa-web/`
- 桌面端改动在 `apps/dsa-desktop/`
- 部署与流水线改动在 `scripts/`、`.github/workflows/`、`docker/`
- `archive/` 目录只读，不在其中继续开发

## 3. Python 代码规范

### 3.1 文件头格式

所有新建 `.py` 文件使用以下文件头：

```python
# -*- coding: utf-8 -*-
"""模块简述（一句话）。

可选的详细说明：职责、设计目标、与其他模块的关系。
"""
```

示例见 `src/storage.py`、`data_provider/moneyflow_fetcher.py`、`src/services/data_purge.py`。

### 3.2 命名约定

| 类型 | 约定 | 示例 |
|------|------|------|
| 模块 / 文件 | snake_case | `moneyflow_fetcher.py`、`data_purge.py` |
| 函数 / 方法 | snake_case | `get_capital_flow()`、`_safe_float()` |
| 变量 | snake_case | `main_net_inflow`、`sector_rankings` |
| 类 | PascalCase | `MoneyFlowFetcher`、`AkshareFundamentalAdapter` |
| 常量 | UPPER_SNAKE_CASE | `_STOCK_GET_URL`、`_RETRY_BACKOFF_SEC` |
| 私有成员 | 单下划线前缀 | `_build_session()`、`self._timeout` |
| Env 变量 | UPPER_SNAKE_CASE | `MONEYFLOW_API_ENABLED`、`DATA_PURGE_INTERVAL_SECONDS` |

### 3.3 类型注解

- 公开 API 函数 / 方法**必须**带类型注解（参数 + 返回值）
- 内部辅助函数可省略，但建议带返回值类型
- 复杂类型用 `typing` 模块：`Optional`、`List`、`Dict`、`Tuple`、`Any`
- 使用 `from __future__ import annotations` 延迟注解求值（py3.10+ 兼容）

```python
def _request_json(
    session: requests.Session,
    url: str,
    params: Dict[str, Any],
    timeout: float,
    block_name: str,
    *,
    retry_on_conn_error: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    ...
```

### 3.4 Docstring 风格

- 模块级 docstring：说明职责与设计目标（中文为主）
- 公开类 / 方法 docstring：说明用途、参数、返回值、异常
- 私有方法可只写一行说明
- 注释、docstring、日志文案以清晰准确为准，**不强制英文**，但应与文件语境保持一致

```python
def get_capital_flow(self, stock_code: str, *, top_n: int = 5) -> Dict[str, Any]:
    """返回完整资金面上下文（个股 + 行业 + 北向）。

    与 AkshareFundamentalAdapter.get_capital_flow 的输出 schema 对齐，
    在 stock_flow / sector_rankings 基础上新增 northbound / stock_industry 块。
    任一子接口失败不影响其他子接口（fail-open）。
    """
```

### 3.5 Import 顺序（isort black profile）

```python
# 1. 标准库
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple

# 2. 第三方库
import requests

# 3. 本仓库模块（src.* / data_provider.* / api.* 等）
from src.config import get_config
from .base import normalize_stock_code
```

### 3.6 fail-open 模式

数据源适配器、通知发送、报告生成等模块遵循 fail-open：

```python
try:
    result = fetcher.get_capital_flow(stock_code)
    if result.get("status") in ("ok", "partial"):
        return result
except Exception as exc:
    errors.append(f"moneyflow_fetcher:{type(exc).__name__}")

# 兜底逻辑
return {"status": "not_supported", "errors": errors}
```

- 单一数据源 / 通知渠道失败不应拖垮整个分析流程
- 异常必须转为错误信息返回，不向上抛
- 错误信息格式：`{block_name}:{error_type}:{detail}`，便于 grep

## 4. 配置项规范

新增 `.env` 配置项时**必须**：

1. 在 `src/config.py` dataclass 中定义字段，带类型注解与默认值
2. 在 `src/config.py` 的 `Config.from_env()` 中读取 env，用 `parse_env_float` / `parse_env_int` 等带边界校验的工具
3. 在 `.env.example` 中追加注释说明（用途、单位、默认值、边界）
4. 在 `docs/CHANGELOG.md` `[Unreleased]` 段追加扁平格式条目

```python
# src/config.py
moneyflow_api_timeout_seconds: float = 4.0

# from_env()
moneyflow_api_timeout_seconds=parse_env_float(
    os.getenv('MONEYFLOW_API_TIMEOUT_SECONDS'),
    4.0,
    field_name='MONEYFLOW_API_TIMEOUT_SECONDS',
    minimum=1.0,
    maximum=30.0,
),
```

```text
# .env.example
# 单次子请求超时秒数（共 4 个子请求）
# 建议 3-5s；最小 1.0，最大 30.0
# MONEYFLOW_API_TIMEOUT_SECONDS=4.0
```

## 5. 测试规范

### 5.1 文件命名

- 单元测试：`tests/test_<module>.py`（如 `tests/test_moneyflow_fetcher.py`）
- 服务级测试：`tests/test_<service>_service.py`
- API 测试：`tests/test_<endpoint>_api.py`
- 子目录测试：`tests/<dir>/test_<module>.py`（如 `tests/agent/test_disagreement.py`）

### 5.2 测试类与方法

```python
class TestMoneyFlowFetcher(unittest.TestCase):
    """MoneyFlowFetcher 单元测试。"""

    def test_get_stock_flow_returns_partial_when_realtime_only(self):
        """realtime 成功 + kline 失败时返回 partial 状态。"""
        ...
```

- 测试类用 PascalCase，按被测模块命名（`Test<ModuleName>`）
- 测试方法用 snake_case，描述行为而非实现（`test_<condition>_<expected_result>`）

### 5.3 Mock 与网络

- 涉及网络的测试**必须** mock，并打 `@pytest.mark.network` 标记的真实网络测试单独存放
- Mock 用 `unittest.mock.patch` / `MagicMock`
- 不要在单元测试中发起真实 HTTP 请求

### 5.4 测试隔离

- 每个测试用例独立运行，不依赖其他用例的副作用
- 涉及 `os.environ` 的测试需在 `setUp` / `tearDown` 中保存与恢复
- 涉及 SQLite 文件锁的测试需在 `tearDown` 中显式清理（参考 `tests/conftest.py` 的 `safe_cleanup_temp_dir`）

## 6. 文档规范

### 6.1 文档位置

- `README.md` — 项目定位、核心能力总览、快速开始、主要入口
- `docs/CHANGELOG.md` — 发布日志（`[Unreleased]` 段使用扁平格式）
- `docs/INDEX.md` — 文档索引
- `docs/coding-standards.md` — 本文件
- `docs/<topic>.md` — 专题文档（如 `docs/data-source-stability.md`、`docs/decision-signals.md`）

### 6.2 CHANGELOG 扁平格式

`[Unreleased]` 段每条独立一行，格式为 `- [类型] 描述`：

```markdown
## [Unreleased]

- [新功能] A 股资金面数据源 `data_provider/moneyflow_fetcher.py` 直接对接 EastMoney push2 JSON API
- [改进] MoneyFlowFetcher 准确率与效率优化：实时字段 kline 兜底 + ConnectionError 单次重试
- [修复] ...
- [文档] ...
- [测试] ...
- [chore] ...
```

类型取值：`新功能` / `改进` / `修复` / `文档` / `测试` / `chore`。**禁止**在 `[Unreleased]` 内新增 `### 类目标题`，以减少并发 PR 的 merge 冲突。发版时由 maintainer 汇总整理成带标题的正式格式。

### 6.3 中英文同步

变更中英双语文档之一时，需评估另一份是否需要同步；若未同步，交付说明里要写明原因。

## 7. Git 与 PR 规范

### 7.1 Commit message

- 使用英文，不添加 `Co-Authored-By`
- 推荐格式：`<类型>: <修改内容>`，类型为 `fix` / `feat` / `refactor` / `docs` / `chore` / `test` / `ci`
- 不写死密钥、账号、路径、模型名、端口或环境差异逻辑

### 7.2 PR 标题

- 推荐 `<类型>: <修改内容>`，不添加 `[codex]` / `copilot` 等工具来源前缀
- 该规范非阻断项，仅用于协作可读性

### 7.3 自动 tag

自动 tag 默认 opt-in：只有 commit title 含 `#patch` / `#minor` / `#major` 才触发版本号更新。

### 7.4 禁止事项

- 未经明确确认不执行 `git commit` / `git tag` / `git push`
- 不执行 `git push --force` / `reset --hard` / `clean -f` 等破坏性操作（除非用户明确要求）
- 不在 PR 描述中夹带 issue / PR 编号截图作为仓库文件合入

## 8. AI 协作资产

- `AGENTS.md` 是仓库内 AI 协作规则的唯一真源
- `CLAUDE.md` 必须是指向 `AGENTS.md` 的软链接
- `.github/copilot-instructions.md` 与 `.github/instructions/*.instructions.md` 是镜像或分层补充
- 修改 AI 协作治理资产后执行 `python scripts/check_ai_assets.py`

## 9. 验证矩阵（按改动面）

| 改动面 | 验证命令 | 阻断级别 |
|--------|----------|----------|
| Python 后端 | `./scripts/ci_gate.sh`（最低 `python -m py_compile <changed_files>`） | 阻断 |
| Web 前端 | `cd apps/dsa-web && npm ci && npm run lint && npm run build` | 阻断（触发时） |
| 桌面端 | 先构建 Web，再构建桌面端 | 阻断 |
| API / Schema | 后端验证 + 受影响客户端构建验证 | 阻断 |
| 文档 / 治理 | `python scripts/check_ai_assets.py`（AI 资产改动时） | 不强制测试 |
| 工作流 / Docker | 最接近改动面的本地验证 | 阻断 |

## 10. 相关文档

- `AGENTS.md` — 仓库开发流程硬规则（唯一真源）
- `docs/INDEX.md` — 文档总索引
- `docs/CONTRIBUTING.md` — 贡献指南
- `docs/data-source-stability.md` — 数据源 fallback 与熔断策略
- `docs/decision-signals.md` — 决策信号 8 状态模型
- `pyproject.toml` / `setup.cfg` — 工具链配置
- `scripts/ci_gate.sh` — CI 验证脚本

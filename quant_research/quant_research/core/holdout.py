"""holdout 期代码强制隔离（ROADMAP-V2.1 F2）

纪律背景：2026-08-09 之后的数据被指定为最终 holdout（C2 paper tracking
期间的未触碰样本）。此前该纪律只存在于文档；本模块使其成为代码强制——
任何研究命令（网格扫描 / Walk-Forward / 回测 / 过拟合审计）的窗口
越过 HOLDOUT_START 即直接退出非零。

paper tracking 与数据补采（update_pit_tail / build_pit_snapshot）豁免：
前者是记录不是研究，后者是数据管线的持续新鲜度维护。

用法：
    from quant_research.core.holdout import assert_holdout_clean
    assert_holdout_clean(end_date)   # 研究命令 main() 首行调用
"""

from __future__ import annotations

import datetime
from typing import Optional

# C2 启动（2026-09-02）前最后可用研究数据日 + 1 天。选取依据：
# A1/A2/H1/H2 全部实验窗口 ≤ 2026-08-08；08-09 起的数据从未进入任何
# 已注册研究实验，故为干净起点。
HOLDOUT_START = datetime.date(2026, 8, 9)

_EXEMPT_NOTE = "paper tracking 与数据补采豁免（记录/管线维护，非研究）"


class HoldoutViolationError(RuntimeError):
    """研究窗口触碰 holdout 期。"""


def assert_holdout_clean(end: Optional[datetime.date | str] = None,
                         *, task: str = "research") -> None:
    """校验研究窗口终点不越过 holdout 起点。

    Args:
        end: 研究窗口终点（date 或 YYYY-MM-DD 字符串；None 表示今天，
            研究命令默认终点常为"今天"，此时必然触界）。
        task: 任务名（用于错误信息）。
    Raises:
        HoldoutViolationError: end >= HOLDOUT_START。
    """
    if end is None:
        end = datetime.date.today()
    if isinstance(end, str):
        try:
            end = datetime.date.fromisoformat(end.strip()[:10])
        except ValueError as exc:
            raise ValueError(f"无法解析 end 日期: {end!r}") from exc
    if end >= HOLDOUT_START:
        raise HoldoutViolationError(
            f"[holdout] {task} 窗口终点 {end} 触碰 holdout 期"
            f"（>= {HOLDOUT_START}）。研究命令终点必须早于 holdout 起点；"
            f"holdout 数据保留给 C2 周期结束后的最终评估。"
        )

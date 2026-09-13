# -*- coding: utf-8 -*-
"""业务级交易暂停开关（FINAL-MODIFICATION-PLAN P0.7）

比停进程/重启 server 更高一层的产品语义：暂停后系统继续更新数据、
继续发送健康状态、继续允许查询，只是**不再生成任何买卖指令**。

状态文件 output/trading_halt.json（原子写）：
- halted=true 时，操作单生成器把全部目标降级为"交易已暂停"；
- 恢复后自动清状态并刷新决策快照；
- 每次切换记录原因、操作者、时间，写入快照可审计。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

HALT_FILE = Path(__file__).resolve().parents[2] / "output" / "trading_halt.json"


def _read() -> Dict[str, Any]:
    if not HALT_FILE.exists():
        return {"halted": False}
    try:
        data = json.loads(HALT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"halted": False}
    except (OSError, json.JSONDecodeError):
        return {"halted": False}


def _write(state: Dict[str, Any]) -> None:
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HALT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, HALT_FILE)


def is_trading_halted() -> bool:
    """当前是否处于交易暂停。读取失败视为未暂停（fail-open 只对读取；
    生成指令侧另有新鲜度/现金/生命周期多重闸门兜底）。"""
    return bool(_read().get("halted"))


def halt_trading(reason: str = "", operator: str = "user") -> Dict[str, Any]:
    """暂停交易：立即生效，幂等（重复暂停只更新原因）。"""
    state = {
        "halted": True,
        "reason": (reason or "用户手动暂停").strip(),
        "operator": operator,
        "halted_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write(state)
    logger.warning("[trading-halt] 交易已暂停: %s（by %s）", state["reason"], operator)
    return state


def resume_trading(operator: str = "user") -> Dict[str, Any]:
    """恢复交易：清除暂停状态并刷新决策快照。"""
    prev = _read()
    if not prev.get("halted"):
        return {"halted": False, "note": "当前未处于暂停状态"}
    _write({"halted": False,
            "resumed_at": datetime.now().isoformat(timespec="seconds"),
            "resumed_by": operator,
            "previous_halt": prev})
    logger.info("[trading-halt] 交易已恢复（此前暂停原因: %s）", prev.get("reason"))
    try:
        from src.services.decision_snapshot_service import refresh
        refresh()
    except Exception as exc:  # noqa: BLE001 - 快照失败不影响恢复本身
        logger.warning("[trading-halt] 恢复后快照刷新失败: %r", exc)
    return {"halted": False, "previous_halt": prev}


def halt_status() -> Dict[str, Any]:
    """当前暂停状态（供 /健康 与 runtime/status 使用）。"""
    return _read()

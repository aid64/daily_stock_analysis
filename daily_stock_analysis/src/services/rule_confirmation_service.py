# -*- coding: utf-8 -*-
"""规则确认台账服务（v5.0 计划 U0：意图归化）

U0 的存储层：用户对系统规则的签署记录（确认/停用），旁路追加式
JSONL——不写回 strategies.yaml（现有 yaml 写回会抹掉全部行内注释）。

消费契约（"确认/停用即时生效"）：
- portfolio_compositor：R1 价位锚 / R2 集中度 / R3 E2 门
- strategy_engine：R4 打新 / R5 逆回购
均经 :func:`is_rule_enabled` 查询；无记录时默认启用（fail-open——
台账缺失不得让系统整体失聪）。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# 台账文件（data/ 已 gitignore，运行态产物不入库）
LEDGER_FILENAME = "rule_confirmations.jsonl"

# 规则编号 ↔ 消费点（与 bot/commands/rule_confirm.py 的 RULE_INVENTORY 对齐）
STRATEGY_RULE_GATE = {
    "reverse_repo_window": "R5",
    "cb_new_issue": "R4",
}

# U0 披露用规则编号集（与 bot/commands/rule_confirm.py 的 RULE_INVENTORY 对齐；
# 此处只维护编号，不复制描述文本——描述真源仍在命令模块，避免双真源漂移）
DISCLOSURE_RULES = ("R1", "R2", "R3", "R4", "R5")


def pending_rule_ids() -> List[str]:
    """尚无任何签署记录的规则编号（默认启用但未确认）。

    供决策包依据披露引用（U0 验收：披露引用确认状态而非"系统登记"）。
    台账读取失败返回空列表（fail-open——披露失败不得阻塞决策包）。
    """
    try:
        states = load_rule_states()
    except Exception:  # noqa: BLE001
        return []
    return [rule_id for rule_id in DISCLOSURE_RULES if rule_id not in states]


def _ledger_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / LEDGER_FILENAME


def load_rule_states() -> Dict[str, Dict[str, str]]:
    """读台账，返回 {rule_id: 最新记录}。坏行跳过，文件缺失视为全未确认。"""
    states: Dict[str, Dict[str, str]] = {}
    path = _ledger_path()
    if not path.exists():
        return states
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return states
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rule_id = str(rec.get("rule_id") or "")
        if rule_id:
            states[rule_id] = rec
    return states


def record_rule_action(rule_id: str, action: str, note: str = "",
                       source: str = "bot_command") -> None:
    """追加一条确认记录（追加式，不重写历史——审计可回溯）。"""
    rec = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "rule_id": rule_id,
        "action": action,
        "note": note,
        "source": source,
    }
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def is_rule_enabled(rule_id: str, default: bool = True) -> bool:
    """规则是否启用（最新一次签署为准；无记录 fail-open 到 default）。

    台账读取失败/文件缺失 → default=True：规则确认是治理增强，
    不得因台账故障让决策引擎整体失聪。
    """
    try:
        rec = load_rule_states().get(rule_id)
    except Exception:  # noqa: BLE001 - 任何台账异常都不阻塞决策
        return default
    if not rec:
        return default
    return str(rec.get("action")) != "disabled"

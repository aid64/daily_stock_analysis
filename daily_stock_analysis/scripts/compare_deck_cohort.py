#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比盘 cohort 切换工具：旧账封存（归档）→ 新 cohort 开账。

依据 spec Phase 1 第 3 条与 §4 回滚纪律：

- **旧 v3 JSON 封存不删**：归档 = 复制到 `archive/` + 追加 sha256 清单条目；原文件字节保留。
  （封存哈希与 `ledger-seal-manifest.json` 的记录互为印证。）反例已证明旧账存在重复分红、
  凭空卖出现金、重复执行与净值时序污染——不存在可反推的唯一真实历史，故不倒填、不用新规则重算。
- **新 cohort 独立文件起步**：每槽相同虚拟资金 `capital_each`（¥12,411）、冻结配置、
  明确交易日；仅保留策略描述与研究证据类顶层键（prereg/各 study），清空持仓/现金/净值/
  marks/控制键——即"从明确净值起算"。
- **禁止重新启动旧 writer 写归档账**：归档后 `compare_strategies.json` 即为**新 cohort**，
  故误启用也不会再污染旧账。

安全默认：不加 `--apply` 只打印计划（**零写入**）。

用法：
    py scripts/compare_deck_cohort.py --start 2026-10-15            # dry-run 预览
    py scripts/compare_deck_cohort.py --start 2026-10-15 --apply    # 归档 + 开账

回滚：归档不删原文件；开账前若 `--apply` 已执行，回滚 = 用归档副本覆盖
`compare_strategies.json` 并删除本轮归档条目（详见 docs/strategy-compare-deck.md §6）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_DIR = _HERE.parent
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

LEDGER = _PROJECT_DIR.parent / ".scratch" / "dsa-strategy-validation" / "compare_strategies.json"
ARCHIVE_DIR = LEDGER.parent / "archive"
MANIFEST = ARCHIVE_DIR / "cohort-manifest.jsonl"
SCHEMA_VERSION = 3
CAPITAL_EACH = 12411.0

# 开账时清空（位置/控制类）；其余顶层键（prereg、各 study 证据）原样保留
_RESET_TOP_KEYS = ("strategies", "marks", "_actions", "_control", "week0", "start_date", "capital_each")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slot_names() -> list[str]:
    """槽位名唯一真源：deck_registry.SLOTS（18 槽）。"""
    from src.services.deck_registry import SLOTS

    return [spec.slot for spec in SLOTS]


def build_plan(ledger_path: Path = LEDGER, start: str | None = None,
               archive_dir: Path = ARCHIVE_DIR) -> dict:
    """构造归档 + 开账计划（纯计算，不写盘）。"""
    if not ledger_path.exists():
        raise SystemExit(f"台账不存在: {ledger_path}")
    start = start or date.today().isoformat()
    date.fromisoformat(start)  # 非法日期直接报错

    old = json.loads(ledger_path.read_text(encoding="utf-8"))
    old_slots = sorted(old.get("strategies", {}).keys())
    keep_top = {k: v for k, v in old.items() if k not in _RESET_TOP_KEYS and k != "_meta"}

    fresh_slots: dict[str, dict] = {}
    for slot in _slot_names():
        prior = old.get("strategies", {}).get(slot, {})
        fresh_slots[slot] = {
            "desc": prior.get("desc", ""),
            "holds": [],
            "cash": CAPITAL_EACH,
            "equity": CAPITAL_EACH,
            "note": f"新 cohort 开账 {start}（每槽 {CAPITAL_EACH:.0f}，冻结配置）",
        }

    fresh = dict(keep_top)
    fresh["_meta"] = {"schema_version": SCHEMA_VERSION}
    fresh["start_date"] = start
    fresh["capital_each"] = CAPITAL_EACH
    fresh["strategies"] = fresh_slots
    fresh["marks"] = []
    # runner 契约（build_close_summary）：`_actions` 是**按日期键的 dict**
    # （`led["_actions"].get(today)`）；清空内容只清键，不得改成 list。
    fresh["_actions"] = {}
    fresh["_control"] = {}
    fresh["week0"] = {"date": start, "actions": ["新 cohort 开账（旧账封存，spec Phase1-3）"]}

    archive_path = archive_dir / f"compare_strategies.cohort1.{start}.json"
    return {
        "ledger_path": ledger_path,
        "archive_path": archive_path,
        "manifest_path": archive_dir / MANIFEST.name,
        "start": start,
        "old_sha256": _sha256(ledger_path),
        "old_slots": old_slots,
        "new_slots": sorted(fresh_slots),
        "missing_registry_slots": sorted(set(_slot_names()) - set(old_slots)),
        "dropped_positional_keys": [k for k in _RESET_TOP_KEYS if k in old],
        "fresh": fresh,
    }


def render(plan: dict) -> str:
    lines = [
        "[cohort] 计划（dry-run，未写盘）",
        f"  旧账: {plan['ledger_path']}",
        f"  sha256: {plan['old_sha256']}",
        f"  归档 →: {plan['archive_path']}",
        f"  清单 →: {plan['manifest_path']}",
        f"  开账日: {plan['start']}　每槽: {CAPITAL_EACH:.0f}　槽数: {len(plan['new_slots'])}",
        f"  旧账槽位: {len(plan['old_slots'])} 个；注册表缺失: {plan['missing_registry_slots'] or '无'}",
        f"  重置顶层键: {plan['dropped_positional_keys']}",
        "  新 cohort 每槽: cash/equity=%.0f, holds=[], 清空 marks/_control/_actions" % CAPITAL_EACH,
    ]
    return "\n".join(lines)


def apply_plan(plan: dict) -> list[str]:
    """执行计划（写盘）。返回动作清单。"""
    done: list[str] = []
    plan["archive_path"].parent.mkdir(parents=True, exist_ok=True)
    if plan["archive_path"].exists():
        raise SystemExit(f"归档已存在，拒绝覆盖: {plan['archive_path']}")
    shutil.copyfile(plan["ledger_path"], plan["archive_path"])
    done.append(f"archived -> {plan['archive_path']}")

    entry = {
        "archived_at": date.today().isoformat(),
        "source": str(plan["ledger_path"]),
        "archive": str(plan["archive_path"]),
        "sha256": _sha256(plan["archive_path"]),
        "cohort_open_day": plan["start"],
        "note": "旧 cohort 封存（污染实验）；新 cohort 独立文件起步，不迁移历史",
    }
    with plan["manifest_path"].open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    done.append(f"manifest appended -> {plan['manifest_path']}")

    tmp = plan["ledger_path"].with_suffix(".json.cohort-tmp")
    tmp.write_text(json.dumps(plan["fresh"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(plan["ledger_path"])
    done.append(f"opened new cohort -> {plan['ledger_path']}")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="新 cohort 开账交易日（YYYY-MM-DD）；缺省=今天")
    ap.add_argument("--ledger", default=str(LEDGER), help="台账路径（默认对比盘生产台账）")
    ap.add_argument("--archive-dir", default=str(ARCHIVE_DIR), help="归档目录")
    ap.add_argument("--apply", action="store_true", help="真正写盘（缺省为 dry-run）")
    args = ap.parse_args(argv)

    plan = build_plan(Path(args.ledger), args.start, Path(args.archive_dir))
    print(render(plan))
    if not args.apply:
        print("[cohort] dry-run 结束：未写任何文件。确认后加 --apply 执行。")
        return 0
    for line in apply_plan(plan):
        print(f"[cohort] {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

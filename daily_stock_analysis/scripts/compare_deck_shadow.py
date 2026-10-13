#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比盘影子对照装置：新内核 dry 模式逐交易日归档，供启用前对照。

依据 spec Phase 3 第 6 条：「新内核 dry 模式与旧记录并行对照 3 个交易日，
估值差与成交意图差异逐日归档；一致后由用户确认启用新 cohort」。

实现要点（**只读**）：
- 以 `compare_deck_runner.py --mode close --dry-run` 运行新内核：dry 模式
  按设计「只计算与打印，不写台账、不发通知、不执行调仓」；
- 归档前一后校验台账 sha256 **必须一致**（否则判为异常并记为 FAIL）；
- 逐日追加 `shadow/shadow_log.jsonl`：日期、退出码、dry 意图行（各槽
  `(dry) …` 消息）、stdout 尾部、台账哈希、与上一交易日的意图差异；
- `--status` 打印已有交易日数与对照进度（3 日为 spec 要求）。

不启用任何计划任务；`DSA_Deck_Intraday` 保持 Disabled（红线）。

用法：
    py scripts/compare_deck_shadow.py                # 跑一轮并归档
    py scripts/compare_deck_shadow.py --status       # 只看进度与差异
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_DIR = _HERE.parent
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

LEDGER = _PROJECT_DIR.parent / ".scratch" / "dsa-strategy-validation" / "compare_strategies.json"
SHADOW_DIR = LEDGER.parent / "shadow"
SHADOW_LOG = SHADOW_DIR / "shadow_log.jsonl"
RUNNER = _HERE / "compare_deck_runner.py"
REQUIRED_DAYS = 3


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dry_intent_lines(stdout: str) -> list[str]:
    """从 runner stdout 提取 dry 意图行（各槽 `(dry) …`）。"""
    out = []
    for raw in stdout.splitlines():
        line = raw.strip().lstrip("·-• ").strip()
        if "(dry)" in line:
            out.append(line)
    return out


def read_log(shadow_log: Path = SHADOW_LOG) -> list[dict]:
    if not shadow_log.exists():
        return []
    return [json.loads(ln) for ln in shadow_log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def diff_lines(prev: dict | None, cur: dict) -> dict:
    """与上一交易日对照：意图行的新增/消失。"""
    if not prev:
        return {"added": cur.get("dry_lines", []), "removed": []}
    a, b = set(prev.get("dry_lines", [])), set(cur.get("dry_lines", []))
    return {"added": sorted(b - a), "removed": sorted(a - b)}


def record_entry(shadow_log: Path, *, day: str, exit_code: int, stdout: str,
                 ledger_sha_before: str, ledger_sha_after: str) -> dict:
    """构造并追加一条影子记录（纯逻辑，便于测试）。"""
    entries = read_log(shadow_log)
    entry = {
        "day": day,
        "recorded_at": date.today().isoformat(),
        "exit_code": exit_code,
        "ledger_sha256": ledger_sha_after,
        "ledger_unchanged": ledger_sha_before == ledger_sha_after,
        "dry_lines": dry_intent_lines(stdout),
        "stdout_tail": "\n".join(stdout.splitlines()[-40:]),
    }
    prev = next((e for e in reversed(entries) if e.get("day") != day), None)
    entry["diff_vs_prev"] = diff_lines(prev, entry)
    if not entry["ledger_unchanged"]:
        entry["verdict"] = "FAIL: dry-run 改动了台账（dry 模式不应写盘）"
    elif exit_code != 0:
        entry["verdict"] = f"FAIL: runner 退出码 {exit_code}"
    else:
        entry["verdict"] = "ok"
    shadow_log.parent.mkdir(parents=True, exist_ok=True)
    with shadow_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def progress(entries: list[dict]) -> dict:
    days = sorted({e["day"] for e in entries})
    fails = [e for e in entries if e.get("verdict", "").startswith("FAIL")]
    return {"days": days, "count": len(days), "required": REQUIRED_DAYS,
            "remaining": max(0, REQUIRED_DAYS - len(days)), "fails": len(fails)}


def render_status(entries: list[dict]) -> str:
    pr = progress(entries)
    lines = [f"[shadow] 已归档交易日 {pr['count']}/{pr['required']}"
             f"（不足 {pr['remaining']} 日）｜异常 {pr['fails']} 条",
             f"[shadow] 日期: {', '.join(pr['days']) or '无'}"]
    if entries:
        last = entries[-1]
        lines.append(f"[shadow] 最近: {last['day']} verdict={last.get('verdict')}"
                     f" 意图 {len(last.get('dry_lines', []))} 条"
                     f" 台账未变={last.get('ledger_unchanged')}")
        d = last.get("diff_vs_prev") or {}
        lines.append(f"[shadow]   相对上一交易日: 新增 {len(d.get('added', []))} / 消失 {len(d.get('removed', []))}")
    return "\n".join(lines)


def run_shadow(ledger: Path = LEDGER, shadow_log: Path = SHADOW_LOG,
               mode: str = "close", timeout: int = 900) -> dict:
    before = _sha256(ledger)
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--mode", mode, "--dry-run", "--force"],
        cwd=str(_PROJECT_DIR), capture_output=True, text=True, timeout=timeout)
    stdout = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    return record_entry(shadow_log, day=date.today().isoformat(), exit_code=proc.returncode,
                        stdout=stdout, ledger_sha_before=before, ledger_sha_after=_sha256(ledger))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--shadow-dir", default=str(SHADOW_DIR))
    ap.add_argument("--mode", default="close", choices=["close", "intraday"])
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--status", action="store_true", help="只打印进度，不运行")
    args = ap.parse_args(argv)

    shadow_log = Path(args.shadow_dir) / SHADOW_LOG.name
    if args.status:
        print(render_status(read_log(shadow_log)))
        return 0
    entry = run_shadow(Path(args.ledger), shadow_log, args.mode, args.timeout)
    print(f"[shadow] {entry['day']} verdict={entry['verdict']} "
          f"意图 {len(entry['dry_lines'])} 条 台账未变={entry['ledger_unchanged']}")
    print(render_status(read_log(shadow_log)))
    return 0 if entry["verdict"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

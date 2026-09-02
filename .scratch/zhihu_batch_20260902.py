# -*- coding: utf-8 -*-
"""架构专题知乎搜索（2026-09-02 重新审视轮）。

运行：cd /d/daily_stock_analysis && ZHIHU_ACCESS_SECRET=... py .scratch/zhihu_batch_20260902.py
产物：.scratch/zhihu_research_20260902.json
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = r"C:\Users\60468\.zcode\skills\zhihu-search\scripts\zhihu-search.py"
OUT = Path(__file__).with_name("zhihu_research_20260902.json")

QUERIES = [
    ("可转债 量化 回测 滑点 实盘差距", 6),
    ("个人量化 paper trading 模拟盘 多久 实盘", 5),
    ("量化策略 滑点 冲击成本 建模 回测", 6),
    ("可转债 季度调仓 策略 实盘", 5),
    ("量化 持仓 止损 回撤 控制 转债", 5),
    ("个人量化 研究 infra 因子平台 架构", 5),
    ("backtrader vectorbt 迁移 经验", 5),
    ("可转债 强赎 触发 历史 数据 PIT", 5),
]


def main() -> None:
    if not os.environ.get("ZHIHU_ACCESS_SECRET"):
        print("missing ZHIHU_ACCESS_SECRET")
        sys.exit(1)
    results = []
    for i, (query, count) in enumerate(QUERIES, 1):
        payload = json.dumps({"query": query, "count": count}, ensure_ascii=False)
        entry = {"query": query, "count": count}
        for attempt in (1, 2):
            proc = subprocess.run(
                [sys.executable, SCRIPT, payload],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                try:
                    data = json.loads(proc.stdout)
                    entry["items"] = data.get("Data", {}).get("Items", [])
                    print(f"[{i}/{len(QUERIES)}] OK  {query} -> {len(entry['items'])}")
                    break
                except json.JSONDecodeError as exc:
                    entry["error"] = f"json decode: {exc}"
            else:
                entry["error"] = (proc.stderr or proc.stdout or "")[:300]
                print(f"[{i}] attempt {attempt} failed: {query}")
                time.sleep(3)
        results.append(entry)
        time.sleep(2)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done: {sum(1 for r in results if 'items' in r)}/{len(results)} -> {OUT}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""架构专题知乎搜索（2026-08-29 重构调研轮）。

运行：cd /d/daily_stock_analysis && ZHIHU_ACCESS_SECRET=... py .scratch/zhihu_batch_20260829_arch.py
产物：.scratch/zhihu_research_20260829_arch.json
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = r"C:\Users\60468\.zcode\skills\zhihu-search\scripts\zhihu-search.py"
OUT = Path(__file__).with_name("zhihu_research_20260829_arch.json")

# 聚焦"彻底重构"所需的架构知识：引擎形态 / 因子抽象 / 数据层 / 回测实盘统一
QUERIES = [
    ("量化 回测框架 架构设计 自研", 6),
    ("向量化回测 事件驱动 回测 对比 优缺点", 6),
    ("qlib 架构 数据层 因子表达式", 5),
    ("vnpy 架构 事件引擎 gateway", 5),
    ("因子库 设计 因子管理 量化 平台", 6),
    ("量化 数据层 parquet duckdb 存储", 5),
    ("聚宽 米筐 优矿 API 对比 回测", 5),
    ("策略 回测 实盘 一套代码 统一 框架", 6),
    ("QMT xtquant python 实盘 对接 经验", 6),
    ("backtrader 局限 替代 重构", 6),
]


def main() -> None:
    if not os.environ.get("ZHIHU_ACCESS_SECRET"):
        print("missing ZHIHU_ACCESS_SECRET"); sys.exit(1)
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

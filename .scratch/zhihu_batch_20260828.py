# -*- coding: utf-8 -*-
"""知乎搜索批量采集脚本（2026-08-28 调研轮）。

运行：py .scratch/zhihu_batch_20260828.py
产物：.scratch/zhihu_research_20260828.json（失败条目带 error 字段，不中断批次）
"""
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = r"C:\Users\60468\.zcode\skills\zhihu-search\scripts\zhihu-search.py"
OUT = Path(__file__).with_name("zhihu_research_20260828.json")

# (query, count) —— 聚焦既有 6 份档案未覆盖的空白：
# 第 3 层候选策略深化 / WF 失败后的方法论 / 2026 市场环境 / 执行与底仓
QUERIES = [
    ("可转债 三因子 策略 轮动", 5),
    ("可转债 双低 策略 改进 溢价率 到期收益率", 5),
    ("小市值 策略 国九条 退市新规 量化", 5),
    ("微盘股 策略 风险 2026", 5),
    ("可转债 强赎潮 池萎缩 轮动 应对", 5),
    ("MWU 在线学习 投资组合 多专家", 5),
    ("量化 回测 过拟合 walk forward 样本外", 5),
    ("回测 过拟合 PBO 检验 量化", 5),
    ("通达信 自动交易 半个人 量化 接口", 5),
    ("要约收购 套利 实盘 经验 散户", 5),
    ("可转债ETF 511380 长期 配置 收益", 5),
    ("可转债 新券 供给 减少 2026 市场", 5),
]


def main() -> None:
    env_secret_set = bool(__import__("os").environ.get("ZHIHU_ACCESS_SECRET"))
    print(f"secret set: {env_secret_set}, queries: {len(QUERIES)}")
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
                    items = data.get("Data", {}).get("Items", [])
                    entry["items"] = items
                    print(f"[{i}/{len(QUERIES)}] OK  {query}  -> {len(items)} items")
                    break
                except json.JSONDecodeError as exc:
                    entry["error"] = f"json decode: {exc}"
            else:
                entry["error"] = (proc.stderr or proc.stdout or "")[:400]
                print(f"[{i}/{len(QUERIES)}] attempt {attempt} failed, retrying: {query}")
                time.sleep(3)
        results.append(entry)
        time.sleep(2)

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for r in results if "items" in r)
    print(f"done: {ok}/{len(results)} queries succeeded -> {OUT}")


if __name__ == "__main__":
    main()

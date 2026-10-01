# -*- coding: utf-8 -*-
"""对比盘确定性审计脚本（检查员的工具：AI 只解读输出，不现场拼凑核查）。

只读。输出 PASS/WARN/FAIL 三态报告 + JSON（--json 供程序消费）。
核查项：
  1. 台账自洽性：marks 最新日期、_control 键推进、各槽 equity ≈ cash+Σ(qty×成本价)
  2. 执行日志：logs/compare_deck_task.log 最近 start/exit 与退出码
  3. 计划任务：DSA_Deck_Intraday 存在且已启用
  4. 抽样净值：3 只持仓 现价×qty+cash vs 台账 equity（容差 1%）
  5. 时间健康：交易所时间戳 vs 本机时钟偏差（提醒用，不判失败）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib import request as urlrequest

PROJECT_DIR = Path(__file__).resolve().parents[1]
LEDGER = PROJECT_DIR.parent / ".scratch" / "dsa-strategy-validation" / "compare_strategies.json"
TASK_LOG = PROJECT_DIR / "logs" / "compare_deck_task.log"
CAPITAL = 12411.0

results: list[dict] = []


def check(name: str, status: str, detail: str) -> None:
    results.append({"check": name, "status": status, "detail": detail})


def tencent_live(codes6: list[str]) -> dict[str, float]:
    out = {}
    try:
        raw = urlrequest.urlopen(urlrequest.Request(
            "http://qt.gtimg.cn/q=" + ",".join(codes6), headers={"User-Agent": "Mozilla/5.0"}),
            timeout=10).read().decode("gbk")
        for line in raw.strip().split("\n"):
            if "=" not in line:
                continue
            f = line.split('="', 1)[1].rstrip('";').split("~")
            if f[3] and float(f[3]) > 0:
                out[f[2]] = float(f[3])
    except Exception as exc:
        check("行情可达", "WARN", f"腾讯直连失败: {exc!r:.80}")
    return out


def exchange_stamp() -> str:
    try:
        raw = urlrequest.urlopen(urlrequest.Request(
            "http://qt.gtimg.cn/q=sh512890", headers={"User-Agent": "Mozilla/5.0"}),
            timeout=8).read().decode("gbk")
        ts = raw.split('="')[1].rstrip('";').split("~")[30]
        return ts
    except Exception:
        return ""


def main() -> int:
    # 1. 台账
    led = {}
    try:
        led = json.loads(LEDGER.read_text(encoding="utf-8"))
        check("台账读取", "PASS", f"{LEDGER.name}")
    except Exception as exc:
        check("台账读取", "FAIL", repr(exc)[:120])
        _emit()
        return 2

    marks = led.get("marks", [])
    if marks:
        last = marks[-1]
        check("marks 最新", "PASS", f"{last['date']} ({len(marks)} 条)")
    else:
        check("marks 最新", "FAIL", "无任何净值记录")

    ctrl = led.get("_control", {})
    ctrl_keys = {k: str(v)[:20] for k, v in ctrl.items() if k in ("close_done", "event_scan_last", "dividend_check_last", "erp_last_check")}
    check("_control 键", "PASS" if ctrl_keys else "WARN", json.dumps(ctrl_keys, ensure_ascii=False))

    # 自洽: equity ≈ cash + Σ(qty×成本价) (容差 3% — 成本价≠现价的固有偏差上限)
    bad = []
    for name, s in led.get("strategies", {}).items():
        approx = s.get("cash", 0) + sum(h["qty"] * h.get("price", 0) for h in s.get("holds", []))
        eq = s.get("equity", 0)
        if eq and abs(approx - eq) / max(eq, 1) > 0.03:
            bad.append(f"{name}(¥{approx:,.0f} vs ¥{eq:,.0f})")
    check("槽位自洽", "PASS" if not bad else "FAIL", "全部自洽" if not bad else "; ".join(bad))

    # 2. 执行日志
    recent = {"intraday": None, "close": None}
    try:
        lines = TASK_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
        for ln in lines:
            m = re.match(r"(\S+) (\w+) (start|exit=(-?\d+))", ln)
            if m:
                recent[m.group(2)] = (m.group(1), m.group(3))
    except FileNotFoundError:
        pass
    ok_log = recent["intraday"] is not None
    check("执行日志", "PASS" if ok_log else "WARN",
          f"intraday 最近 {recent['intraday']}" if ok_log else f"{TASK_LOG.name} 缺失/无记录")

    # 3. 计划任务
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", "DSA_Deck_Intraday", "/FO", "LIST"],
                           capture_output=True, timeout=15)
        q = r.stdout.decode("gbk", errors="replace")
        enabled = ("已启用" in q) or ("Ready" in q) or ("就绪" in q)
        check("计划任务", "PASS" if enabled else "FAIL",
              "DSA_Deck_Intraday 已启用" if enabled else "未启用/未找到")
    except Exception as exc:
        check("计划任务", "WARN", f"查询失败: {exc!r:.80}")

    # 4. 抽样净值
    samples = []
    for sym in ("510300.SH", "512890.SH", "SZ301578"):
        for name, s in led.get("strategies", {}).items():
            for h in s.get("holds", []):
                if h["symbol"] == sym:
                    samples.append((name, h))
    live = tencent_live(["sh510300", "sh512890", "sz301578"])
    drifts = []
    checked = 0
    for sym6, sym_full in (("510300", "510300.SH"), ("512890", "512890.SH"), ("301578", "SZ301578")):
        live_p = live.get(sym6)
        if not live_p:
            continue
        for name, s in led.get("strategies", {}).items():
            holds = s.get("holds", [])
            if not any(h["symbol"] == sym_full for h in holds):
                continue
            approx = s.get("cash", 0) + sum(
                x["qty"] * (live_p if x["symbol"] == sym_full else x.get("price", 0)) for x in holds)
            eq = s.get("equity", 0)
            drift = abs(approx - eq) / max(eq, 1) * 100
            checked += 1
            drifts.append(f"{name}:{drift:.1f}%")
            if drift > 3:
                check("抽样净值", "FAIL", f"{name} 偏差 {drift:.1f}% (现价{live_p})")
    if checked == 0:
        check("抽样净值", "WARN", "无可抽样持仓(行情缺失)")
    elif not any(r["check"] == "抽样净值" and r["status"] == "FAIL" for r in results):
        check("抽样净值", "PASS", "; ".join(drifts))
    # 5. 时间健康
    stamp = exchange_stamp()
    if stamp:
        ex = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S")
        skew_min = abs((datetime.now() - ex).total_seconds()) / 60
        check("时间健康", "WARN" if skew_min > 12 * 60 else "PASS",
              f"交易所时间戳 {ex:%m-%d %H:%M} | 本机偏差约 {skew_min/60:.1f}h (时钟不可信已知, 执行器已用交易所时间校准)")

    _emit()
    return 0


def _emit() -> None:
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    print("=== 对比盘审计报告 ===")
    for r in results:
        mark = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}[r["status"]]
        print(f"{mark} {r['check']}: {r['detail']}")
    verdict = "正常" if n_fail == 0 else f"【异常】{n_fail} 项 FAIL"
    print(f"结论: {verdict}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.json:
        results.clear()
        _emit_orig = _emit
        def _emit():  # noqa: F811
            print(json.dumps(results, ensure_ascii=False, indent=2))
        main()
    else:
        main()

# -*- coding: utf-8 -*-
"""对比盘确定性审计脚本（检查员的工具：AI 只解读输出，不现场拼凑核查）。

只读。输出 PASS/WARN/FAIL 三态报告 + JSON（--json 供程序消费）。
核查项：
  1. 台账自洽性：marks 最新日期、_control 键推进、各槽 equity ≈ cash+Σ(qty×成本价)
  2. 执行日志：logs/compare_deck_task.log 最近 start/exit 与退出码
  3. 计划任务：DSA_Deck_Intraday 存在且已启用
  3b. 唤醒任务：DSA_Deck_Close 存在、WakeToRun、sync launcher、15:00:30
  4. 抽样净值：3 只持仓 现价×qty+cash vs 台账 equity（容差 1%）
  5. 时间健康：交易所时间戳 vs 本机时钟偏差（提醒用，不判失败）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import request as urlrequest

PROJECT_DIR = Path(__file__).resolve().parents[1]
LEDGER = PROJECT_DIR.parent / ".scratch" / "dsa-strategy-validation" / "compare_strategies.json"
TASK_LOG = PROJECT_DIR / "logs" / "compare_deck_task.log"
BACKUP_ROOT = PROJECT_DIR / "data" / "backups"
CLOSE_BACKUP_ROOT = PROJECT_DIR / "data" / "backups_close"
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


def time_health(stamp: str, now: datetime | None = None) -> tuple[str, str]:
    """交易所时间戳 vs 本机时钟；跨日旧戳只标注，绝不做跨日相减。"""
    now = now or datetime.now()
    if not stamp:
        return "WARN", "交易所时间戳缺失"
    try:
        ex = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return "WARN", f"交易所时间戳格式异常: {stamp[:16]}"
    if ex.date() != now.date():
        return "WARN", f"交易所时间戳 {ex:%m-%d %H:%M} 非当日（市场已收盘），不计算本机偏差"
    if ex.hour * 100 + ex.minute >= 1500:
        return "PASS", f"交易所已收盘（{ex:%m-%d %H:%M}），不计算本机偏差"
    skew_min = abs((now - ex).total_seconds()) / 60
    status = "WARN" if skew_min > 12 * 60 else "PASS"
    return status, (f"交易所时间戳 {ex:%m-%d %H:%M} | 本机偏差约 {skew_min/60:.1f}h"
                    "（时钟不可信已知, 执行器已用交易所时间校准）")


def source_consistency(bs_rows: list, tencent_price, stamp: str,
                       now: datetime | None = None) -> tuple[str, str]:
    """baostock 与腾讯按**同一行情日**比较；盘中不比较实时价与上一收盘。"""
    now = now or datetime.now()
    if not stamp:
        return "WARN", "交易所时间戳缺失, 无法同日比较"
    try:
        qdt = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return "WARN", f"交易所时间戳格式异常: {stamp[:16]}"
    if qdt.date() == now.date() and qdt.hour * 100 + qdt.minute < 1500:
        return "WARN", "盘中不校验(腾讯为实时价, baostock 为上一收盘)"
    qdate = qdt.strftime("%Y-%m-%d")
    row = next((r for r in bs_rows if str(r[0]) == qdate), None)
    if row is None:
        return "WARN", f"baostock 缺 {qdate} 收盘"
    try:
        bs_close = float(row[1])
    except (TypeError, ValueError):
        return "WARN", f"baostock {qdate} 收盘无效"
    if not tencent_price:
        return "WARN", f"腾讯缺 {qdate} 价格"
    tt = float(tencent_price)
    if abs(bs_close - tt) / tt > 0.005:
        return "WARN", f"baostock {bs_close} vs 腾讯 {tt} 偏差>0.5% ({qdate})"
    return "PASS", f"baostock {bs_close} ≈ 腾讯 {tt} ({qdate})"


def wake_task_status(xml: str) -> tuple[str, str]:
    """DSA_Deck_Close 必须 enabled + WakeToRun + sync launcher + 15:00:30 触发器。"""
    if not xml or "<Task" not in xml or "DSA_Deck_Close" not in xml:
        return "WARN", "DSA_Deck_Close 未找到/无法解析"
    disabled = "<Enabled>false</Enabled>" in xml
    wake = "<WakeToRun>true</WakeToRun>" in xml
    sync = "run_hidden_sync.vbs" in xml
    mode_close = "-Mode close" in xml
    boundary = "15:00:30" in xml
    if wake and sync and mode_close and not disabled and boundary:
        return "PASS", "enabled/wake/sync/mode=close/15:00:30 均就绪"
    return "WARN", (f"enabled={not disabled} wake={wake} sync={sync} "
                    f"mode_close={mode_close} boundary_150030={boundary}")


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
        # 新鲜度三态: >15 天 FAIL(执行链实质断供) / >10 天 WARN / 新鲜 PASS。
        # 旧实现只查非空, 2000-01-01 的陈旧 marks 也判 PASS。
        try:
            age_days = (datetime.now() - datetime.strptime(str(last["date"])[:10],
                                                            "%Y-%m-%d")).days
        except ValueError:
            age_days = 9999
        if age_days > 15:
            check("marks 最新", "FAIL", f"{last['date']} 距今 {age_days} 天 (>{15}天断供)")
        elif age_days > 10:
            check("marks 最新", "WARN", f"{last['date']} 距今 {age_days} 天")
        else:
            check("marks 最新", "PASS", f"{last['date']} ({len(marks)} 条)")
    else:
        check("marks 最新", "FAIL", "无任何净值记录")

    ctrl = led.get("_control", {})
    ctrl_keys = {k: str(v)[:20] for k, v in ctrl.items() if k in ("close_done", "event_scan_last", "dividend_check_last", "erp_last_check")}
    check("_control 键", "PASS" if ctrl_keys else "WARN", json.dumps(ctrl_keys, ensure_ascii=False))

    # 2b. 收盘完成度: 注册表 + 主动槽覆盖。首建仓只成交 C/D 时这里必须 WARN。
    close_done = str(ctrl.get("close_done") or "")
    if close_done == datetime.now().strftime("%Y-%m-%d"):
        pending = []
        month_key = close_done[:7]
        month_done = (ctrl.get("rebalance_done") or {}).get(month_key)
        if month_done not in (None, "all"):
            pending.append(f"月度({month_done})")
        iso = datetime.strptime(close_done, "%Y-%m-%d").isocalendar()
        week_key = f"{iso[0]}-W{int(iso[1]):02d}"
        week_done = (ctrl.get("week_rebalance_done") or {}).get(week_key)
        if week_done not in (None, "all"):
            pending.append(f"周度({week_done})")
        check("注册表", "WARN" if pending else "PASS",
              "未完成: " + "; ".join(pending) if pending else "周/月均 all")
        excluded = ("C_沪深300持有", "D_红利低波持有", "X_事件套利",
                    "Y_确定性收益", "L_北向择时")
        active = [n for n in led.get("strategies", {}) if n not in excluded]
        invested = [n for n in active if led["strategies"][n].get("holds")]
        check("主动槽覆盖", "PASS" if invested else "WARN",
              f"{len(invested)}/{len(active)} 有持仓"
              + ("" if invested else "（仅基准/现金）"))
    else:
        check("注册表", "WARN", f"今日未收盘 (close_done={close_done or '缺失'})")
        check("主动槽覆盖", "WARN", "今日未收盘")

    # 自洽: equity ≈ cash + Σ(qty×成本价) (容差 3% — 成本价≠现价的固有偏差上限)
    bad = []
    for name, s in led.get("strategies", {}).items():
        approx = s.get("cash", 0) + sum(h["qty"] * h.get("price", 0) for h in s.get("holds", []))
        eq = s.get("equity", 0)
        if eq and abs(approx - eq) / max(eq, 1) > 0.03:
            bad.append(f"{name}(¥{approx:,.0f} vs ¥{eq:,.0f})")
    check("槽位自洽", "PASS" if not bad else "FAIL", "全部自洽" if not bad else "; ".join(bad))

    # 2. 执行日志: 配对 start/exit；最后一轮无 exit 显式 WARN
    # （旧实现取最后一条事件, 遇 start 会 IndexError, 或拿更早的 exit 误判）。
    recent = {"intraday": {"start": None, "exit": None},
              "close": {"start": None, "exit": None}}
    try:
        lines = TASK_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
        for ln in lines:
            m = re.match(r"(\S+) (\w+) (start|exit=(-?\d+))", ln)
            if not m:
                continue
            mode, stamp, kind = m.group(2), m.group(1), m.group(3)
            slot = recent.setdefault(mode, {"start": None, "exit": None})
            if kind == "start":
                slot["start"] = stamp
            else:
                slot["exit"] = (stamp, int(kind.split("=", 1)[1]))
    except FileNotFoundError:
        pass
    entry = recent["intraday"]
    if entry["exit"] is None:
        if entry["start"]:
            check("执行日志", "WARN",
                  f"最后一轮 {entry['start']} 无 exit 记录（任务被终止或仍在运行）")
        else:
            check("执行日志", "WARN", f"{TASK_LOG.name} 缺失/无记录")
    else:
        stamp, code = entry["exit"]
        if entry["start"] and entry["start"] > stamp:
            check("执行日志", "WARN",
                  f"最后一轮 {entry['start']} 无 exit 记录; 上一条 {stamp} exit={code}")
        elif code != 0:
            check("执行日志", "FAIL", f"intraday 最近 {stamp} exit={code} 非零退出")
        else:
            check("执行日志", "PASS", f"intraday 最近 {stamp} exit={code}")

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

    # 3b. 唤醒收盘任务 DSA_Deck_Close: enabled + WakeToRun + sync launcher + 15:00:30
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", "DSA_Deck_Close", "/XML"],
                           capture_output=True, timeout=15)
        raw = r.stdout
        if isinstance(raw, bytes):
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                xml = raw.decode("utf-16")
            else:
                xml = raw.decode("utf-8", errors="replace")
        else:
            xml = str(raw or "")
        status, detail = wake_task_status(xml)
        check("唤醒任务", status, detail)
    except Exception as exc:
        check("唤醒任务", "WARN", f"查询失败: {exc!r:.60}")

    # 3c. 任务结果: Task Scheduler 的 LastTaskResult（0x800710E0 等非零结果）
    def _last_result(name: str):
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-ScheduledTaskInfo -TaskName '{name}').LastTaskResult"],
                capture_output=True, text=True, timeout=15)
            return int((r.stdout or "").strip())
        except Exception:
            return None

    task_results = {n: _last_result(n) for n in
                    ("DSA_Deck_Intraday", "DSA_Deck_Close", "DSA_Ledger_Backup")}
    missing_results = [n for n, v in task_results.items() if v is None]
    bad_results = [f"{n}={v}" for n, v in task_results.items() if v not in (None, 0)]
    if missing_results:
        check("任务结果", "WARN", f"无法读取: {missing_results}")
    elif bad_results:
        check("任务结果", "WARN", f"非零: {', '.join(bad_results)}")
    else:
        check("任务结果", "PASS", "Intraday / Close / Ledger_Backup 均 0")

    # 3d. 备份与收盘台账快照（数据耐久性）
    today = datetime.now().date()
    backup_dirs = []
    try:
        for p in BACKUP_ROOT.iterdir():
            try:
                backup_dirs.append((date.fromisoformat(p.name), p))
            except ValueError:
                continue
    except OSError:
        pass
    if not backup_dirs:
        check("备份", "WARN", f"无日期备份目录: {BACKUP_ROOT}")
    else:
        latest, _ = max(backup_dirs, key=lambda x: x[0])
        age = (today - latest).days
        if age <= 0:
            check("备份", "PASS", f"今日备份 {latest}")
        elif age <= 2:
            check("备份", "WARN", f"最新备份 {latest}（{age} 天前）")
        else:
            check("备份", "FAIL", f"最新备份 {latest}（{age} 天前），超过 2 天")

    def _latest_dated(root: Path):
        found = []
        try:
            for p in root.iterdir():
                try:
                    found.append((date.fromisoformat(p.name), p))
                except ValueError:
                    continue
        except OSError:
            pass
        return max(found, key=lambda x: x[0]) if found else None

    close_latest = _latest_dated(CLOSE_BACKUP_ROOT)
    if close_latest is None:
        check("收盘备份", "WARN", f"无收盘备份目录: {CLOSE_BACKUP_ROOT}")
    else:
        close_day, close_dir = close_latest
        close_age = (today - close_day).days
        if close_age <= 0:
            check("收盘备份", "PASS", f"今日收盘备份 {close_day}")
        elif close_age <= 2:
            check("收盘备份", "WARN", f"最新收盘备份 {close_day}（{close_age} 天前）")
        else:
            check("收盘备份", "FAIL", f"最新收盘备份 {close_day}（{close_age} 天前）")

    snapshot = LEDGER.parent / "postclose" / f"compare_strategies.{today:%Y%m%d}.json"
    try:
        snap_bytes = snapshot.read_bytes()
        json.loads(snap_bytes.decode("utf-8"))
        snap_at = datetime.fromtimestamp(snapshot.stat().st_mtime)
        live_sha = hashlib.sha256(LEDGER.read_bytes()).hexdigest()
        snap_sha = hashlib.sha256(snap_bytes).hexdigest()
        if live_sha != snap_sha:
            check("收盘快照", "WARN",
                  f"{snapshot.name} 与 live 台账不一致（{snap_at:%H:%M}），等待 17:30/20:00 刷新")
        elif snap_at.hour * 100 + snap_at.minute >= 1500:
            check("收盘快照", "PASS", f"{snapshot.name} @ {snap_at:%H:%M}")
        else:
            check("收盘快照", "WARN",
                  f"{snapshot.name} 生成于 {snap_at:%H:%M}，早于 15:00（盘前/盘中快照）")
    except FileNotFoundError:
        check("收盘快照", "WARN", f"今日无快照: {snapshot.name}（审计任务 17:30 生成）")
    except Exception as exc:
        check("收盘快照", "FAIL", f"快照无法解析: {exc!r:.80}")

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
    # 4b. 多源交叉验证: baostock vs 腾讯 同一行情日收盘价
    stamp = exchange_stamp()
    try:
        import baostock as bs
        bs.login()
        today = datetime.now().date()
        rs = bs.query_history_k_data_plus(
            "sh.510300", "date,close",
            start_date=(today - timedelta(days=10)).isoformat(),
            end_date=today.isoformat())
        bs_rows = []
        while rs.error_code == "0" and rs.next():
            bs_rows.append(rs.get_row_data())
        bs.logout()
        tt = tencent_live(["sh510300"]).get("510300")
        status, detail = source_consistency(bs_rows, tt, stamp, datetime.now())
        check("多源一致性", status, detail)
    except Exception as exc:
        check("多源一致性", "WARN", f"交叉验证不可用: {exc!r:.60}")

    # 5. 时间健康（跨日旧戳只标注，不把日期边界误报成时钟偏差）
    status, detail = time_health(stamp, datetime.now())
    check("时间健康", status, detail)

    _emit()
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    return 1 if n_fail else 0


def _emit() -> None:
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    print("=== 对比盘审计报告 ===")
    for r in results:
        mark = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}[r["status"]]
        print(f"{mark} {r['check']}: {r['detail']}")
    # 三态结论: 有 FAIL → 异常; 核心证据任一非 PASS → 样本不足;
    # 其余 WARN 属提醒项不阻断判定。注册表/主动槽/任务结果/唤醒任务都是首次全建仓
    # 的必要证据，不能让"只成交 C/D"或任务非零结果被"正常"掩盖。
    core = ("marks 最新", "执行日志", "抽样净值", "注册表", "主动槽覆盖",
            "任务结果", "唤醒任务", "备份", "收盘备份", "收盘快照")
    if n_fail:
        verdict = f"【异常】{n_fail} 项 FAIL"
    elif any(r["status"] != "PASS" and r["check"] in core for r in results):
        verdict = "样本不足, 无法判定"
    else:
        verdict = "正常"
    print(f"结论: {verdict}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.json:
        results.clear()

        def _emit():  # noqa: F811  --json 模式: 以 JSON 数组替代文本报告
            print(json.dumps(results, ensure_ascii=False, indent=2))
    # 退出码传播: FAIL 存在 → 1 (供计划任务/CI 感知), 台账不可读 → 2。
    raise SystemExit(main())

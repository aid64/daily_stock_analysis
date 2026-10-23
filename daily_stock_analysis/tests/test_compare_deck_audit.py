# -*- coding: utf-8 -*-
"""compare_deck_audit 审计判定单测（离线，无网络）。

覆盖任务08审计升级: marks 新鲜度三态 / 执行日志 exit 码 FAIL / verdict 三态 / 退出码传播。
行情、schtasks、baostock 交叉验证全部 mock，断言只依赖判定逻辑。"""

import contextlib
import importlib.util
import io
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

AUDIT = Path(__file__).resolve().parents[1] / "scripts" / "compare_deck_audit.py"
spec = importlib.util.spec_from_file_location("compare_deck_audit", AUDIT)
mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("compare_deck_audit", mod)
spec.loader.exec_module(mod)


def _ledger(marks_dates, strategies=None):
    return {"strategies": strategies or {"F_小市值20": {"cash": 100.0, "holds": [], "equity": 100.0}},
            "marks": [{"date": d} for d in marks_dates],
            "_control": {}}


def _run_audit(monkeypatch, tmp_path, ledger_dict, log_text, live=None, last_result="0",
               backup=True, snapshot=True, close_backup=True):
    led = tmp_path / "ledger.json"
    led.write_text(json.dumps(ledger_dict, ensure_ascii=False), encoding="utf-8")
    log = tmp_path / "task.log"
    log.write_text(log_text, encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", led)
    monkeypatch.setattr(mod, "TASK_LOG", log)
    monkeypatch.setattr(mod, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(mod, "CLOSE_BACKUP_ROOT", tmp_path / "backups_close")
    if backup:
        (tmp_path / "backups" / _today()).mkdir(parents=True)
    if close_backup:
        (tmp_path / "backups_close" / _today()).mkdir(parents=True)
    if snapshot:
        post = tmp_path / "postclose"
        post.mkdir()
        snap = post / f"compare_strategies.{_today().replace('-', '')}.json"
        snap.write_text(json.dumps(ledger_dict, ensure_ascii=False), encoding="utf-8")
        after_close = datetime.now().replace(hour=16, minute=0, second=0, microsecond=0)
        os.utime(snap, (after_close.timestamp(), after_close.timestamp()))
    monkeypatch.setattr(mod, "tencent_live", lambda codes: dict(live or {}))
    monkeypatch.setattr(mod, "exchange_stamp", lambda: "")
    close_xml = ('<Task><RegistrationInfo><URI>DSA_Deck_Close</URI></RegistrationInfo>'
                 '<Settings><WakeToRun>true</WakeToRun></Settings>'
                 '<Triggers><StartBoundary>2026-10-21T15:00:30+08:00</StartBoundary></Triggers>'
                 '<Actions><Exec><Arguments>run_hidden_sync.vbs</Arguments></Exec></Actions></Task>')

    def _fake_run(cmd, *a, **k):
        joined = " ".join(str(x) for x in cmd)
        if "LastTaskResult" in joined:
            return SimpleNamespace(stdout=f"{last_result}\n", returncode=0)
        if "DSA_Deck_Close" in joined and "/XML" in joined:
            return SimpleNamespace(stdout=close_xml, returncode=0)
        return SimpleNamespace(stdout="已启用", returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    fake_bs = SimpleNamespace(login=lambda: None, logout=lambda: None,
                              query_history_k_data_plus=lambda *a, **k:
                              SimpleNamespace(error_code="1"))
    monkeypatch.setitem(sys.modules, "baostock", fake_bs)
    mod.results.clear()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main()
    return rc, buf.getvalue(), [dict(r) for r in mod.results]


def _today(days=0):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _row(rows, name):
    return next(r for r in rows if r["check"] == name)


def test_audit_fails_marks_stale_over_15_days(monkeypatch, tmp_path):
    """marks 距今>15天 → FAIL + 退出码1（旧实现仅查非空, 陈旧净值也判 PASS）。"""
    rc, out, rows = _run_audit(monkeypatch, tmp_path,
                               _ledger([_today(30)]),
                               f"{_today()}T09:00:00 intraday start\n{_today()}T09:00:01 intraday exit=0\n")
    assert _row(rows, "marks 最新")["status"] == "FAIL"
    assert rc == 1
    assert "【异常】" in out


def test_audit_warns_marks_slightly_stale(monkeypatch, tmp_path):
    """marks 距今 11~15 天 → WARN（不阻断退出码）。"""
    rc, _, rows = _run_audit(monkeypatch, tmp_path,
                             _ledger([_today(12)]),
                             f"{_today()}T09:00:00 intraday start\n{_today()}T09:00:01 intraday exit=0\n")
    assert _row(rows, "marks 最新")["status"] == "WARN"
    assert rc == 0


def test_audit_fails_nonzero_exit_code(monkeypatch, tmp_path):
    """intraday 最近 exit=9 → 执行日志 FAIL（旧实现判 PASS 的缺陷）。"""
    rc, _, rows = _run_audit(monkeypatch, tmp_path,
                             _ledger([_today()]),
                             "2000-01-01T09:00:00 intraday start\n2000-01-01T09:00:01 intraday exit=9\n")
    assert _row(rows, "执行日志")["status"] == "FAIL"
    assert rc == 1


def test_audit_verdict_insufficient_when_core_not_pass(monkeypatch, tmp_path):
    """核心证据(抽样净值)WARN 且无 FAIL → 样本不足, 不得宣称正常; WARN 不阻断退出码。"""
    rc, out, rows = _run_audit(monkeypatch, tmp_path,
                               _ledger([_today()]),
                               f"{_today()}T09:00:00 intraday start\n{_today()}T09:00:01 intraday exit=0\n")
    assert _row(rows, "marks 最新")["status"] == "PASS"
    assert _row(rows, "执行日志")["status"] == "PASS"
    assert _row(rows, "抽样净值")["status"] == "WARN"      # 无可抽样持仓
    assert "样本不足" in out
    assert "正常" not in out.replace("样本不足", "")
    assert rc == 0


def test_audit_verdict_normal_when_all_core_pass(monkeypatch, tmp_path):
    """核心证据全 PASS（含可抽样持仓）→ 正常。"""
    strategies = {"F_小市值20": {"cash": 41.2,
                                  "holds": [{"symbol": "510300.SH", "qty": 100, "price": 4.5}],
                                  "equity": 491.2}}
    led = _ledger([_today()], strategies=strategies)
    iso = datetime.strptime(_today(), "%Y-%m-%d").isocalendar()
    week_key = f"{iso[0]}-W{int(iso[1]):02d}"
    led["_control"] = {"close_done": _today(),
                       "rebalance_done": {_today()[:7]: "all"},
                       "week_rebalance_done": {week_key: "all"}}
    rc, out, rows = _run_audit(monkeypatch, tmp_path, led,
                               f"{_today()}T09:00:00 intraday start\n{_today()}T09:00:01 intraday exit=0\n",
                               live={"510300": 4.5})
    assert _row(rows, "marks 最新")["status"] == "PASS"
    assert _row(rows, "执行日志")["status"] == "PASS"
    assert _row(rows, "抽样净值")["status"] == "PASS"
    assert _row(rows, "注册表")["status"] == "PASS"
    assert _row(rows, "主动槽覆盖")["status"] == "PASS"
    assert _row(rows, "任务结果")["status"] == "PASS"
    assert _row(rows, "唤醒任务")["status"] == "PASS"
    assert "结论: 正常" in out
    assert rc == 0


def test_audit_warns_missing_backup_and_snapshot(monkeypatch, tmp_path):
    """备份/收盘快照缺失属于核心证据缺口，不得宣称正常。"""
    led = _ledger([_today()])
    rc, out, rows = _run_audit(monkeypatch, tmp_path, led,
                               f"{_today()}T09:00:00 intraday start\n{_today()}T09:00:01 intraday exit=0\n",
                               backup=False, snapshot=False, close_backup=False)
    assert _row(rows, "备份")["status"] == "WARN"
    assert _row(rows, "收盘备份")["status"] == "WARN"
    assert _row(rows, "收盘快照")["status"] == "WARN"
    assert "样本不足" in out
    assert "结论: 正常" not in out
    assert rc == 0


def test_audit_warns_nonzero_task_result(monkeypatch, tmp_path):
    """任务调度器 LastTaskResult 非 0 → 任务结果 WARN，且不得宣称正常。"""
    strategies = {"F_小市值20": {"cash": 41.2,
                                  "holds": [{"symbol": "510300.SH", "qty": 100, "price": 4.5}],
                                  "equity": 491.2}}
    led = _ledger([_today()], strategies=strategies)
    iso = datetime.strptime(_today(), "%Y-%m-%d").isocalendar()
    week_key = f"{iso[0]}-W{int(iso[1]):02d}"
    led["_control"] = {"close_done": _today(),
                       "rebalance_done": {_today()[:7]: "all"},
                       "week_rebalance_done": {week_key: "all"}}
    rc, out, rows = _run_audit(monkeypatch, tmp_path, led,
                               f"{_today()}T09:00:00 intraday start\n{_today()}T09:00:01 intraday exit=0\n",
                               live={"510300": 4.5}, last_result="267009")
    assert _row(rows, "任务结果")["status"] == "WARN"
    assert "267009" in _row(rows, "任务结果")["detail"]
    assert "样本不足" in out
    assert "结论: 正常" not in out
    assert rc == 0


def test_audit_flags_partial_first_fill(monkeypatch, tmp_path):
    """首建仓只成交 C/D、主动槽全现金、周/月仍 [] → 注册表/主动槽 WARN，不得宣称正常。"""
    strategies = {
        "C_沪深300持有": {"cash": 269.5, "holds": [{"symbol": "510300.SH", "qty": 2900, "price": 4.185}], "equity": 12411.0},
        "D_红利低波持有": {"cash": 276.0, "holds": [{"symbol": "512890.SH", "qty": 10000, "price": 1.213}], "equity": 12411.0},
        "F_小市值20": {"cash": 12411.0, "holds": [], "equity": 12411.0},
    }
    led = _ledger([_today()], strategies=strategies)
    iso = datetime.strptime(_today(), "%Y-%m-%d").isocalendar()
    week_key = f"{iso[0]}-W{int(iso[1]):02d}"
    led["_control"] = {"close_done": _today(),
                       "rebalance_done": {_today()[:7]: []},
                       "week_rebalance_done": {week_key: []}}
    rc, out, rows = _run_audit(monkeypatch, tmp_path, led,
                               f"{_today()}T09:00:00 intraday start\n{_today()}T09:00:01 intraday exit=0\n",
                               live={"510300": 4.185, "512890": 1.213})
    assert _row(rows, "注册表")["status"] == "WARN"
    assert _row(rows, "主动槽覆盖")["status"] == "WARN"
    assert "样本不足" in out
    assert "结论: 正常" not in out
    assert rc == 0


def test_time_health_after_close_does_not_report_clock_skew():
    status, detail = mod.time_health("20261021161400", datetime(2026, 10, 21, 20, 35))
    assert status == "PASS" and "已收盘" in detail and "本机偏差约" not in detail


# ------------------------------------------------- 时间健康/多源同日比较（A4/A5）
def test_time_health_previous_day_stamp_is_not_clock_skew():
    """跨日旧戳不得被当成 8h 时钟偏差（A4 实测：10-20 16:14 vs 10-21 00:30）。"""
    status, detail = mod.time_health("20261020161400", datetime(2026, 10, 21, 0, 30))
    assert status == "WARN"
    assert "非当日" in detail and "本机偏差约" not in detail


def test_time_health_same_day_pass():
    status, _ = mod.time_health("20261021093000", datetime(2026, 10, 21, 9, 35))
    assert status == "PASS"


def test_source_consistency_same_date_pass():
    """baostock 必须与腾讯**同一行情日**比较；10-20 收盘与 10-20 价一致 → PASS。"""
    status, detail = mod.source_consistency(
        [["2026-10-20", "4.2200"]], 4.22, "20261020161400",
        datetime(2026, 10, 21, 0, 30))
    assert status == "PASS" and "2026-10-20" in detail


def test_source_consistency_intraday_skips():
    """盘中腾讯是实时价、baostock 是上一收盘：跳过而不是误报背离。"""
    status, detail = mod.source_consistency(
        [["2026-10-21", "4.20"]], 4.22, "20261021100000",
        datetime(2026, 10, 21, 10, 0))
    assert status == "WARN" and "盘中" in detail


def test_source_consistency_flags_divergence_on_same_date():
    """同一行情日偏差>0.5% 仍必须 WARN（A5 的真异常路径不能丢）。"""
    status, detail = mod.source_consistency(
        [["2026-10-20", "4.4320"]], 4.22, "20261020161400",
        datetime(2026, 10, 21, 0, 30))
    assert status == "WARN" and "偏差" in detail


# ------------------------------------------------- 执行日志 start/exit 配对
def test_audit_warns_when_last_run_has_no_exit(monkeypatch, tmp_path):
    """最后一轮只有 start（被终止/仍在运行）→ WARN，不得拿上一条 exit 误判 PASS。"""
    rc, out, rows = _run_audit(
        monkeypatch, tmp_path, _ledger([_today()]),
        f"{_today()}T09:00:00 intraday start\n{_today()}T09:00:01 intraday exit=0\n"
        f"{_today()}T10:00:00 intraday start\n")
    row = _row(rows, "执行日志")
    assert row["status"] == "WARN" and "无 exit" in row["detail"]
    assert rc == 0 and "样本不足" in out


def test_audit_start_only_log_does_not_crash(monkeypatch, tmp_path):
    """旧实现遇 start-only 日志会 IndexError；新实现必须降级为 WARN。"""
    rc, _, rows = _run_audit(
        monkeypatch, tmp_path, _ledger([_today()]),
        f"{_today()}T09:00:00 intraday start\n")
    row = _row(rows, "执行日志")
    assert row["status"] == "WARN" and "无 exit" in row["detail"]
    assert rc == 0


def test_wake_task_status_pass():
    xml = ('<Task><RegistrationInfo><URI>DSA_Deck_Close</URI></RegistrationInfo>'
           '<Settings><WakeToRun>true</WakeToRun></Settings>'
           '<Triggers><StartBoundary>2026-10-21T15:00:30+08:00</StartBoundary></Triggers>'
           '<Actions><Exec><Arguments>run_hidden_sync.vbs</Arguments></Exec></Actions></Task>')
    status, detail = mod.wake_task_status(xml)
    assert status == "PASS" and "sync" in detail


def test_wake_task_status_warns_when_disabled_or_missing_sync():
    xml = ('<Task><URI>DSA_Deck_Close</URI>'
           '<Settings><WakeToRun>true</WakeToRun><Enabled>false</Enabled></Settings>'
           '<StartBoundary>2026-10-21T15:00:30</StartBoundary>'
           '<Arguments>run_hidden.vbs</Arguments></Task>')
    status, detail = mod.wake_task_status(xml)
    assert status == "WARN" and "sync=False" in detail

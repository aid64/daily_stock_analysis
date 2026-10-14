"""对比盘影子对照装置测试（纯逻辑，tmp 归档，不运行 runner、不触碰生产）。"""

from __future__ import annotations

import json
from pathlib import Path

import scripts.compare_deck_shadow as shadow

STDOUT = """[dry-run] 通知抑制: deck_close_2026-10-15
A_动量(dry) 引擎信号提取
B_轮动(dry) 25日收益{'510300.SH': +1.2%}→510300.SH
F_小市值20(dry) 目标['600519.SH']
普通日志行不应被当作意图
"""


def test_dry_intent_lines_only_picks_dry_markers():
    lines = shadow.dry_intent_lines(STDOUT)
    assert len(lines) == 3
    assert all("(dry)" in ln for ln in lines)
    assert not any("普通日志" in ln for ln in lines)


def test_record_entry_ok_and_persists_jsonl(tmp_path):
    log = tmp_path / "shadow" / "shadow_log.jsonl"
    entry = shadow.record_entry(log, day="2026-10-15", exit_code=0, stdout=STDOUT,
                                ledger_sha_before="abc", ledger_sha_after="abc")
    assert entry["verdict"] == "ok"
    assert entry["ledger_unchanged"] is True
    assert entry["diff_vs_prev"] == {"added": entry["dry_lines"], "removed": []}
    written = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert written == [entry]


def test_ledger_change_is_fail(tmp_path):
    log = tmp_path / "shadow_log.jsonl"
    entry = shadow.record_entry(log, day="2026-10-15", exit_code=0, stdout=STDOUT,
                                ledger_sha_before="abc", ledger_sha_after="def")
    assert entry["ledger_unchanged"] is False
    assert entry["verdict"].startswith("FAIL")


def test_nonzero_exit_is_fail(tmp_path):
    log = tmp_path / "shadow_log.jsonl"
    entry = shadow.record_entry(log, day="2026-10-15", exit_code=2, stdout=STDOUT,
                                ledger_sha_before="abc", ledger_sha_after="abc")
    assert entry["verdict"].startswith("FAIL")


def test_diff_vs_prev_tracks_added_and_removed(tmp_path):
    log = tmp_path / "shadow_log.jsonl"
    shadow.record_entry(log, day="2026-10-15", exit_code=0,
                        stdout="A_动量(dry) x\nB_轮动(dry) y\n",
                        ledger_sha_before="a", ledger_sha_after="a")
    second = shadow.record_entry(log, day="2026-10-16", exit_code=0,
                                 stdout="A_动量(dry) x\nC_沪深300(dry) z\n",
                                 ledger_sha_before="a", ledger_sha_after="a")
    assert second["diff_vs_prev"]["added"] == ["C_沪深300(dry) z"]
    assert second["diff_vs_prev"]["removed"] == ["B_轮动(dry) y"]


def test_progress_counts_distinct_days_and_fails(tmp_path):
    log = tmp_path / "shadow_log.jsonl"
    for day in ("2026-10-15", "2026-10-16", "2026-10-16"):
        shadow.record_entry(log, day=day, exit_code=0, stdout=STDOUT,
                            ledger_sha_before="a", ledger_sha_after="a")
    shadow.record_entry(log, day="2026-10-19", exit_code=1, stdout=STDOUT,
                        ledger_sha_before="a", ledger_sha_after="a")
    pr = shadow.progress(shadow.read_log(log))
    assert pr["days"] == ["2026-10-15", "2026-10-16", "2026-10-19"]
    assert pr["count"] == 3 and pr["remaining"] == 0 and pr["fails"] == 1
    assert "3/3" in shadow.render_status(shadow.read_log(log))


def test_rerun_same_day_supersedes_previous_for_counting(tmp_path):
    """同日重跑：证据全部保留，但计数与 FAIL 判定取当天最后一次。"""
    log = tmp_path / "shadow_log.jsonl"
    shadow.record_entry(log, day="2026-10-14", exit_code=1, stdout="",
                        ledger_sha_before="a", ledger_sha_after="a")          # 首次 FAIL
    shadow.record_entry(log, day="2026-10-14", exit_code=0,
                        stdout="A_动量(dry) x\n", ledger_sha_before="a",
                        ledger_sha_after="a")                                # 重跑 ok
    entries = shadow.read_log(log)
    assert len(entries) == 2                       # 证据不删
    pr = shadow.progress(entries)
    assert pr["count"] == 1 and pr["fails"] == 0   # 计数取代先前
    assert "1/3" in shadow.render_status(entries)
    assert shadow.latest_per_day(entries)["2026-10-14"]["verdict"] == "ok"


def test_status_on_missing_log_is_empty(tmp_path):
    assert shadow.read_log(tmp_path / "nope.jsonl") == []
    assert "0/3" in shadow.render_status([])

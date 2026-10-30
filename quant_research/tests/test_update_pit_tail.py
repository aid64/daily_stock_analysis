# -*- coding: utf-8 -*-
"""PIT 尾部增量的滚动游标测试（全合成，无网络）。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from scripts import update_pit_tail


def _seed(root: Path, names: list[str], *, source: str | None = "akshare") -> None:
    for name in names:
        sym_dir = root / name
        sym_dir.mkdir(parents=True)
        if source is not None:
            (sym_dir / "meta.json").write_text(
                json.dumps({"source": source}), encoding="utf-8")
        pd.DataFrame({
            "date": ["2026-10-01"],
            "close": [100.0],
            "premium_rate": [5.0],
        }).to_csv(sym_dir / "2026.csv", index=False)


def _fake_fetch(calls: list[str]):
    def _fetch(code: str) -> pd.DataFrame:
        calls.append(code)
        return pd.DataFrame({
            "date": ["2026-10-02", "2026-10-03"],
            "close": [101.0, 102.0],
            "premium_rate": [4.0, 3.0],
        })

    return _fetch


def _run(root: Path, monkeypatch, calls: list[str], *extra: str) -> int:
    monkeypatch.setattr(update_pit_tail, "_fetch_history", _fake_fetch(calls))
    return update_pit_tail.main([
        "--raw-dir", str(root),
        "--throttle", "0",
        "--symbols", "999999.SH",
        *extra,
    ])


def test_limit_advances_cursor_across_runs(tmp_path, monkeypatch):
    names = ["110001.SH", "110002.SH", "110003.SH", "110004.SH", "110005.SH"]
    _seed(tmp_path, names)
    calls: list[str] = []

    assert _run(tmp_path, monkeypatch, calls, "--limit", "2") == 0
    assert calls == ["110001", "110002"]
    cursor = json.loads((tmp_path / update_pit_tail._CURSOR_FILE).read_text(encoding="utf-8"))
    assert cursor["last_symbol"] == "110002.SH"

    calls.clear()
    assert _run(tmp_path, monkeypatch, calls, "--limit", "2") == 0
    assert calls == ["110003", "110004"]


def test_priority_symbol_runs_first_without_consuming_limit(tmp_path, monkeypatch):
    _seed(tmp_path, ["110001.SH", "110002.SH", "110003.SH"])
    calls: list[str] = []
    monkeypatch.setattr(update_pit_tail, "_fetch_history", _fake_fetch(calls))

    rc = update_pit_tail.main([
        "--raw-dir", str(tmp_path),
        "--throttle", "0",
        "--symbols", "110003.SH",
        "--limit", "1",
    ])

    assert rc == 0
    assert calls == ["110003", "110001"]
    cursor = json.loads((tmp_path / update_pit_tail._CURSOR_FILE).read_text(encoding="utf-8"))
    assert cursor["last_symbol"] == "110001.SH"


def test_skipped_non_akshare_symbol_still_advances_cursor(tmp_path, monkeypatch):
    _seed(tmp_path, ["110001.SH", "110002.SH"])
    _seed(tmp_path, ["110003.SH"], source=None)
    calls: list[str] = []

    assert _run(tmp_path, monkeypatch, calls, "--limit", "3") == 0
    assert calls == ["110001", "110002"]
    cursor = json.loads((tmp_path / update_pit_tail._CURSOR_FILE).read_text(encoding="utf-8"))
    assert cursor["last_symbol"] == "110003.SH"

    calls.clear()
    assert _run(tmp_path, monkeypatch, calls, "--limit", "1") == 0
    assert calls == ["110001"]


def test_full_sweep_wraps_back_to_start(tmp_path, monkeypatch):
    names = ["110001.SH", "110002.SH", "110003.SH"]
    _seed(tmp_path, names)
    calls: list[str] = []

    assert _run(tmp_path, monkeypatch, calls) == 0
    assert calls == ["110001", "110002", "110003"]

    calls.clear()
    assert _run(tmp_path, monkeypatch, calls, "--limit", "1") == 0
    assert calls == ["110001"]


def test_active_only_skips_stale_symbol_unless_priority(tmp_path, monkeypatch):
    _seed(tmp_path, ["110001.SH", "110002.SH"])
    pd.DataFrame({
        "date": ["2020-01-02"],
        "close": [100.0],
        "premium_rate": [5.0],
    }).to_csv(tmp_path / "110002.SH" / "2020.csv", index=False)
    (tmp_path / "110002.SH" / "2026.csv").unlink()
    calls: list[str] = []

    assert _run(tmp_path, monkeypatch, calls, "--active-only") == 0
    assert calls == ["110001"]

    calls.clear()
    monkeypatch.setattr(update_pit_tail, "_fetch_history", _fake_fetch(calls))
    assert update_pit_tail.main([
        "--raw-dir", str(tmp_path),
        "--throttle", "0",
        "--symbols", "110002.SH",
        "--active-only",
    ]) == 0
    assert calls == ["110002", "110001"]


def test_fetch_history_uses_named_fields_not_column_positions(monkeypatch):
    """东财加列不应再破坏解析：按 DATE/FCLOSE/SWAPOR 取值。"""
    import requests

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"data": [
                {"DATE": "2026-10-23 00:00:00", "FCLOSE": 127.8,
                 "SWAPOR": 13.5, "NEW_COLUMN": "x"},
                {"DATE": "2026-10-26 00:00:00", "FCLOSE": None,
                 "SWAPOR": 12.9, "NEW_COLUMN": "x"},
            ]}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    df = update_pit_tail._fetch_history("113631")

    assert list(df.columns) == ["date", "close", "premium_rate"]
    assert df.to_dict("records") == [
        {"date": "2026-10-23", "close": 127.8, "premium_rate": 13.5},
    ]


def test_completed_cutoff_excludes_intraday_until_1530():
    assert update_pit_tail._completed_cutoff(datetime(2026, 10, 30, 9, 0)) == date(2026, 10, 29)
    assert update_pit_tail._completed_cutoff(datetime(2026, 10, 30, 15, 29)) == date(2026, 10, 29)
    assert update_pit_tail._completed_cutoff(datetime(2026, 10, 30, 15, 30)) == date(2026, 10, 30)


def test_clean_partial_rows_removes_unfinished_day(tmp_path):
    sym = tmp_path / "110001.SH"
    sym.mkdir()
    pd.DataFrame({
        "date": ["2026-10-28", "2026-10-29", "2026-10-30"],
        "close": [100.0, 101.0, 102.0],
    }).to_csv(sym / "2026.csv", index=False)
    removed = update_pit_tail._clean_partial_rows(tmp_path, date(2026, 10, 29))
    assert removed == 1
    kept = pd.read_csv(sym / "2026.csv")
    assert kept["date"].tolist() == ["2026-10-28", "2026-10-29"]


def test_tail_excludes_current_intraday_bar(tmp_path, monkeypatch):
    _seed(tmp_path, ["110001.SH"])
    monkeypatch.setattr(
        update_pit_tail, "_fetch_history",
        lambda code: pd.DataFrame({
            "date": ["2026-10-29", "2026-10-30"],
            "close": [101.0, 102.0],
            "premium_rate": [4.0, 3.0],
        }),
    )
    monkeypatch.setattr(update_pit_tail, "_completed_cutoff", lambda: date(2026, 10, 29))
    rc = update_pit_tail.main([
        "--raw-dir", str(tmp_path), "--throttle", "0", "--symbols", "110001.SH",
    ])
    assert rc == 0
    df = pd.read_csv(tmp_path / "110001.SH" / "2026.csv")
    assert df["date"].tolist() == ["2026-10-01", "2026-10-29"]


def test_write_csv_atomic_replaces_and_cleans(tmp_path):
    path = tmp_path / "2026.csv"
    path.write_text("date,close\n2026-10-01,100\n", encoding="utf-8")
    df = pd.DataFrame({"date": ["2026-10-02"], "close": [101.0],
                       "premium_rate": [4.0]})
    update_pit_tail._write_csv_atomic(df, path)
    assert not (tmp_path / "2026.csv.tmp").exists()
    text = path.read_text(encoding="utf-8")
    assert "2026-10-02" in text and "2026-10-01" not in text

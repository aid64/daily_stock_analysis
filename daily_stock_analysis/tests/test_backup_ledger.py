# -*- coding: utf-8 -*-
"""backup_ledger 单测：对比盘台账纳入备份 + 完整性校验（离线，tmp 目录）。"""

import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

BACKUP = Path(__file__).resolve().parents[1] / "scripts" / "backup_ledger.py"
spec = importlib.util.spec_from_file_location("backup_ledger", BACKUP)
mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("backup_ledger", mod)
spec.loader.exec_module(mod)

_REQUIRED = ("portfolio_accounts", "portfolio_trades", "portfolio_cash_ledger",
             "portfolio_positions", "portfolio_event_audit")


def _env(tmp_path, monkeypatch):
    project = tmp_path / "daily_stock_analysis"
    repo = tmp_path / "repo"
    deck = repo / ".scratch" / "dsa-strategy-validation"
    (project / "data").mkdir(parents=True)
    (project / "output" / "decision_snapshot").mkdir(parents=True)
    (project / "output" / "order_sheets").mkdir(parents=True)
    deck.mkdir(parents=True)

    db = project / "data" / "stock_analysis.db"
    conn = sqlite3.connect(db)
    try:
        for table in _REQUIRED:
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")
        conn.commit()
    finally:
        conn.close()

    (project / ".env").write_text("KEY=value\n", encoding="utf-8")
    (repo / "strategies.yaml").write_text("strategies: []\n", encoding="utf-8")
    (project / "output" / "decision_snapshot" / "a.json").write_text("{}", encoding="utf-8")
    (project / "output" / "order_sheets" / "b.json").write_text("{}", encoding="utf-8")
    (deck / "compare_strategies.json").write_text(json.dumps({
        "strategies": {}, "marks": [], "_control": {},
    }, ensure_ascii=False), encoding="utf-8")
    (deck / "cohort-manifest.jsonl").write_text('{"cohort": 1}\n', encoding="utf-8")
    (deck / "ledger-seal-manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(mod, "_PROJECT_ROOT", project)
    monkeypatch.setattr(mod, "_REPO_ROOT", repo)
    monkeypatch.setattr(mod, "_DB", db)
    monkeypatch.setattr(mod, "_BACKUP_ROOT", project / "data" / "backups")
    monkeypatch.setattr(mod, "_OUTPUT", project / "output")
    monkeypatch.setattr(mod, "_DECK_DIR", deck)
    return project, deck


def test_backup_includes_deck_ledger_and_manifest_hash(tmp_path, monkeypatch):
    project, deck = _env(tmp_path, monkeypatch)
    day = mod.run_backup(verify=True)
    assert day == project / "data" / "backups" / date.today().isoformat()

    copied = day / "dsa-strategy-validation" / "compare_strategies.json"
    assert copied.read_bytes() == (deck / "compare_strategies.json").read_bytes()
    assert (day / "dsa-strategy-validation" / "cohort-manifest.jsonl").exists()
    assert mod._verify_deck(copied) is True

    manifest = (day / "MANIFEST.txt").read_text(encoding="utf-8")
    assert f"deck_ledger_sha256={mod._sha256(copied)}" in manifest
    assert f"deck_ledger_bytes={copied.stat().st_size}" in manifest


def test_backup_verify_rejects_broken_deck_ledger(tmp_path, monkeypatch):
    _, deck = _env(tmp_path, monkeypatch)
    (deck / "compare_strategies.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        mod.run_backup(verify=True)
    assert exc.value.code == 3


def test_backup_without_deck_ledger_still_succeeds(tmp_path, monkeypatch):
    """干净检出/未启用对比盘时不应阻断账户备份。"""
    _, deck = _env(tmp_path, monkeypatch)
    (deck / "compare_strategies.json").unlink()
    day = mod.run_backup(verify=True)
    assert (day / "stock_analysis.db").exists()
    assert not (day / "dsa-strategy-validation" / "compare_strategies.json").exists()
    assert "deck_ledger_sha256" not in (day / "MANIFEST.txt").read_text(encoding="utf-8")

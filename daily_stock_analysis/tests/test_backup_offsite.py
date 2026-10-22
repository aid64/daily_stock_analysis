# -*- coding: utf-8 -*-
"""backup_offsite 单测：离机复制/凭据排除/校验/保留策略（离线，tmp 目录）。"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

OFFSITE = Path(__file__).resolve().parents[1] / "scripts" / "backup_offsite.py"
spec = importlib.util.spec_from_file_location("backup_offsite", OFFSITE)
mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("backup_offsite", mod)
spec.loader.exec_module(mod)


def _env(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    src = root / "2026-10-22"
    (src / "dsa-strategy-validation" / "archive").mkdir(parents=True)
    (src / "stock_analysis.db").write_bytes(b"db-fixture")
    (src / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (src / "MANIFEST.txt").write_text("backup_at=fixture\n", encoding="utf-8")
    ledger = {"strategies": {}, "marks": [], "_control": {}}
    (src / "dsa-strategy-validation" / "compare_strategies.json").write_text(
        json.dumps(ledger), encoding="utf-8")
    (src / "dsa-strategy-validation" / "archive" / "cohort-manifest.jsonl").write_text(
        '{"cohort": 1}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "_BACKUP_ROOT", root)
    return root, src, tmp_path / "offsite"


def test_sync_copies_files_and_skips_env(tmp_path, monkeypatch):
    _, src, dest = _env(tmp_path, monkeypatch)
    target = mod.sync_offsite(dest, "2026-10-22")
    assert target == dest / "daily_stock_analysis" / "2026-10-22"
    assert (target / "stock_analysis.db").read_bytes() == b"db-fixture"
    assert (target / "dsa-strategy-validation" / "compare_strategies.json").exists()
    assert (target / "dsa-strategy-validation" / "archive" / "cohort-manifest.jsonl").exists()
    assert not (target / ".env").exists()

    manifest = (target / "OFFSITE_MANIFEST.txt").read_text(encoding="utf-8")
    assert f"{mod._sha256(src / 'stock_analysis.db')}" in manifest
    assert "skipped=.env" in manifest


def test_sync_include_env_when_requested(tmp_path, monkeypatch):
    _, _, dest = _env(tmp_path, monkeypatch)
    target = mod.sync_offsite(dest, "2026-10-22", include_env=True)
    assert (target / ".env").read_text(encoding="utf-8") == "SECRET=1\n"
    assert "skipped=-" in (target / "OFFSITE_MANIFEST.txt").read_text(encoding="utf-8")


def test_sync_default_removes_stale_env_from_target(tmp_path, monkeypatch):
    _, _, dest = _env(tmp_path, monkeypatch)
    target_dir = dest / "daily_stock_analysis" / "2026-10-22"
    target_dir.mkdir(parents=True)
    (target_dir / ".env").write_text("OLD_SECRET=1\n", encoding="utf-8")
    mod.sync_offsite(dest, "2026-10-22")
    assert not (target_dir / ".env").exists()


def test_sync_dry_run_writes_nothing(tmp_path, monkeypatch):
    _, _, dest = _env(tmp_path, monkeypatch)
    target = mod.sync_offsite(dest, "2026-10-22", dry_run=True)
    assert target == dest / "daily_stock_analysis" / "2026-10-22"
    assert not dest.exists()


def test_sync_retention_keeps_target_and_latest(tmp_path, monkeypatch):
    _, _, dest = _env(tmp_path, monkeypatch)
    dest_root = dest / "daily_stock_analysis"
    for day in ("2026-10-19", "2026-10-20", "2026-10-21"):
        (dest_root / day).mkdir(parents=True)
    target = mod.sync_offsite(dest, "2026-10-22", retention=2)
    remaining = sorted(p.name for p in dest_root.iterdir() if p.is_dir())
    assert remaining == ["2026-10-21", "2026-10-22"]
    assert target.exists()


def test_sync_rejects_destination_inside_backup_root(tmp_path, monkeypatch):
    root, _, _ = _env(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        mod.sync_offsite(root, "2026-10-22")
    assert exc.value.code == 2

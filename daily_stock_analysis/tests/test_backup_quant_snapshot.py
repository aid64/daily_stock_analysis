# -*- coding: utf-8 -*-
"""quant 信号输入副本测试（离线，tmp 目录）。"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_quant_snapshot.py"
spec = importlib.util.spec_from_file_location("backup_quant_snapshot", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("backup_quant_snapshot", mod)
spec.loader.exec_module(mod)


def _seed(tmp_path):
    quant = tmp_path / "quant"
    deck = tmp_path / "deck"
    (quant / "data" / "pit").mkdir(parents=True)
    (quant / "data" / "pit" / "convertible_pit.parquet").write_bytes(b"pit")
    (quant / "output" / "paper_tracking").mkdir(parents=True)
    (quant / "output" / "paper_tracking" / "current_plan.json").write_text("{}", encoding="utf-8")
    (quant / "output" / "feeds_snapshot_v5.pkl").write_bytes(b"snapshot")
    (quant / "config" / "strategy").mkdir(parents=True)
    (quant / "config" / "strategy" / "etf_mwu_cb_monthly.yaml").write_text("name: x\n", encoding="utf-8")
    (deck / "output" / "deck_signal_cache").mkdir(parents=True)
    (deck / "output" / "deck_signal_cache" / "etf_mwu_cb_20261027.json").write_text("{}", encoding="utf-8")
    return quant, deck, tmp_path / "dest"


def _run(quant, deck, dest):
    return mod.main([
        "--quant-root", str(quant),
        "--deck-root", str(deck),
        "--destination", str(dest),
        "--date", "2026-10-28",
    ])


def test_backup_copies_required_and_latest_h_cache(tmp_path):
    quant, deck, dest = _seed(tmp_path)
    assert _run(quant, deck, dest) == 0
    target = dest / "quant" / "2026-10-28"
    for name in ("convertible_pit.parquet", "current_plan.json",
                 "feeds_snapshot.pkl", "etf_mwu_cb.json",
                 "etf_mwu_cb_monthly.yaml", "QUANT_MANIFEST.txt"):
        assert (target / name).is_file()
    manifest = (target / "QUANT_MANIFEST.txt").read_text(encoding="utf-8")
    assert mod._sha256(quant / "data" / "pit" / "convertible_pit.parquet") in manifest
    assert "etf_mwu_cb_20261027.json" in manifest


def test_missing_required_pit_fails(tmp_path):
    quant, deck, dest = _seed(tmp_path)
    (quant / "data" / "pit" / "convertible_pit.parquet").unlink()
    with pytest.raises(SystemExit) as exc:
        _run(quant, deck, dest)
    assert exc.value.code == 2

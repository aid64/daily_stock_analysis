# -*- coding: utf-8 -*-
"""W3 决策快照测试：新鲜度评估/构建/原子落盘/每日归档/保留期修剪"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from src.services import decision_snapshot_service as dss
from src.services.decision_snapshot_service import (
    build_snapshot,
    evaluate_plan_freshness,
    load_latest_snapshot,
    refresh,
    save_snapshot,
)


class TestPlanFreshness:
    def test_stale_plan_rejected(self):
        plan = {"pit_end": (date.today() - timedelta(days=9)).isoformat(),
                "generated_at": (date.today() - timedelta(days=9)).isoformat()}
        fresh, msg = evaluate_plan_freshness(plan)
        assert fresh is False and "信号过期" in msg

    def test_unregenerated_plan_rejected(self):
        plan = {"pit_end": date.today().isoformat(),
                "generated_at": "2026-09-01T09:00:00"}
        fresh, msg = evaluate_plan_freshness(plan)
        assert fresh is False and "未随 PIT 更新重生" in msg

    def test_fresh_plan_passes(self):
        today = date.today().isoformat()
        fresh, msg = evaluate_plan_freshness(
            {"pit_end": today, "generated_at": today + "T09:00:00"})
        assert fresh is True and msg == ""

    def test_missing_pit_end_rejected(self):
        fresh, msg = evaluate_plan_freshness({"generated_at": "x"})
        assert fresh is False and "缺少 pit_end" in msg

    def test_none_plan_treated_as_fresh(self):
        # 契约缺失由调用方按"信号源不可用"处理，新鲜度本身不拦
        assert evaluate_plan_freshness(None) == (True, "")


class TestSnapshotBuild:
    def test_build_collects_all_blocks(self, monkeypatch):
        monkeypatch.setattr(dss, "_pit_data_version", lambda: {
            "pit_end": "2026-09-11", "cross_section_bonds": 283,
            "pit_sha256": "abc"})
        fake_sheet = type("S", (), {
            "sheet_id": "s1", "status": "pending", "orders": [], "holds": [],
            "fills": {}})()
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(oss, "load_latest_sheet", lambda d: fake_sheet)
        snap = build_snapshot()

        assert snap["trade_date"] == date.today().isoformat()
        assert snap["snapshot_id"].startswith(snap["trade_date"])
        for key in ("plan", "portfolio", "order_sheet", "data_versions",
                    "quality"):
            assert key in snap
        assert snap["data_versions"]["pit_sha256"] == "abc"

    def test_build_records_warning_when_plan_stale(self, monkeypatch):
        import src.services.cb_signal_bridge as bridge
        monkeypatch.setattr(bridge, "load_cb_plan", lambda: {
            "pit_end": (date.today() - timedelta(days=9)).isoformat(),
            "generated_at": "2026-09-01T00:00:00", "targets": {}})
        snap = build_snapshot()
        assert snap["quality"]["fresh"] is False
        assert any("信号过期" in w for w in snap["quality"]["warnings"])


class TestSnapshotPersistence:
    @pytest.fixture()
    def snap_dir(self, tmp_path: Path, monkeypatch) -> Path:
        d = tmp_path / "snap"
        monkeypatch.setattr(dss, "SNAPSHOT_DIR", d)
        return d

    def _snapshot(self, trade_date=None):
        return {"snapshot_id": f"{trade_date or date.today().isoformat()}_x",
                "trade_date": trade_date or date.today().isoformat(),
                "generated_at": datetime.now().isoformat()}

    def test_save_load_roundtrip_and_no_tmp(self, snap_dir):
        save_snapshot(self._snapshot())
        loaded = load_latest_snapshot()
        assert loaded["snapshot_id"].endswith("_x")
        assert not (snap_dir / "latest.json.tmp").exists()

    def test_archive_one_file_per_day_overwritten(self, snap_dir):
        save_snapshot(self._snapshot())
        save_snapshot({**self._snapshot(), "snapshot_id": "second"})
        archives = list((snap_dir / "archive").glob("????-??-??.json"))
        assert len(archives) == 1
        assert json.loads(archives[0].read_text(encoding="utf-8"))[
            "snapshot_id"] == "second"

    def test_old_archives_pruned(self, snap_dir):
        old_day = (date.today() - timedelta(days=120)).isoformat()
        save_snapshot(self._snapshot(trade_date=old_day))
        assert not (snap_dir / "archive" / f"{old_day}.json").exists()

    def test_load_missing_returns_none(self, snap_dir):
        assert load_latest_snapshot() is None

    def test_refresh_returns_snapshot(self, snap_dir, monkeypatch):
        monkeypatch.setattr(dss, "_pit_data_version", lambda: {"pit_end": "x"})
        snap = refresh()
        assert snap is not None and load_latest_snapshot()["snapshot_id"] == snap["snapshot_id"]


class TestSnapshotDatabasePath:
    def test_snapshot_records_database_path(self, monkeypatch):
        """快照必须记录本次进程实际使用的数据库绝对路径（可回溯）。"""
        monkeypatch.setattr(dss, "_pit_data_version", lambda: {"pit_end": "x"})
        snap = build_snapshot()
        assert snap["data_versions"]["database_path"]


class TestPortfolioRegressionGuard:
    """同日 + 不同库 + 持仓读成空 → 拒绝降级覆盖（多进程 DATABASE_PATH 不一致）。"""

    @pytest.fixture()
    def snap_dir(self, tmp_path: Path, monkeypatch) -> Path:
        d = tmp_path / "snap_guard"
        monkeypatch.setattr(dss, "SNAPSHOT_DIR", d)
        return d

    def _snap(self, *, positions, db, day=None, snapshot_id="x"):
        day = day or date.today().isoformat()
        return {"snapshot_id": snapshot_id, "trade_date": day,
                "portfolio": {"total_cash": 0.0, "positions": positions},
                "data_versions": {"database_path": db}}

    def test_regression_detected_when_db_differs(self):
        prev = self._snap(positions=[{"symbol": "000899"}], db="A")
        new = self._snap(positions=[], db="B")
        assert dss._is_portfolio_regression(prev, new) is True

    def test_no_regression_when_same_db(self):
        prev = self._snap(positions=[{"symbol": "000899"}], db="A")
        new = self._snap(positions=[], db="A")
        assert dss._is_portfolio_regression(prev, new) is False

    def test_no_regression_for_different_day(self):
        prev = self._snap(positions=[{"symbol": "000899"}], db="A",
                          day=(date.today() - timedelta(days=1)).isoformat())
        new = self._snap(positions=[], db="B")
        assert dss._is_portfolio_regression(prev, new) is False

    def test_no_regression_when_new_snapshot_has_positions(self):
        prev = self._snap(positions=[{"symbol": "000899"}], db="A")
        new = self._snap(positions=[{"symbol": "002475"}], db="B")
        assert dss._is_portfolio_regression(prev, new) is False

    def test_refresh_keeps_previous_on_regression(self, snap_dir, monkeypatch):
        save_snapshot(self._snap(positions=[{"symbol": "000899"}], db="A",
                                 snapshot_id="prev"))
        monkeypatch.setattr(
            dss, "build_snapshot",
            lambda: self._snap(positions=[], db="B", snapshot_id="new"))
        result = refresh()
        assert result["snapshot_id"] == "prev"
        assert load_latest_snapshot()["snapshot_id"] == "prev"

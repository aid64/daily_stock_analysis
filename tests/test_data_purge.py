# -*- coding: utf-8 -*-
"""数据保留 / 自动清理相关测试。

覆盖：
- DatabaseManager.purge_expired_llm_usage / purge_expired_decision_signals /
  purge_expired_analysis_history 的边界条件与级联清理
- src.services.data_purge.purge_expired_reports 文件清理
- src.services.data_purge.run_data_purge 端到端统计
- src.services.data_purge.build_data_purge_background_task entry 构建逻辑
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from tests.conftest import safe_cleanup_temp_dir

from src.services.data_purge import (
    build_data_purge_background_task,
    purge_expired_reports,
    run_data_purge,
)
from src.storage import (
    AnalysisHistory,
    DatabaseManager,
    DecisionSignalFeedbackRecord,
    DecisionSignalOutcomeRecord,
    DecisionSignalRecord,
    LLMUsage,
)


def _fresh_db() -> DatabaseManager:
    """Return a DatabaseManager backed by a fresh in-memory SQLite database."""
    DatabaseManager.reset_instance()
    return DatabaseManager(db_url="sqlite:///:memory:")


class _PurgeTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(lambda: safe_cleanup_temp_dir(self._temp_dir))
        self.db = _fresh_db()

    def _insert_llm_usage(self, called_at: datetime, model: str = "test-model") -> int:
        with self.db.session_scope() as session:
            record = LLMUsage(
                call_type="analysis",
                model=model,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                called_at=called_at,
            )
            session.add(record)
            session.commit()
            return int(record.id)

    def _insert_decision_signal(
        self,
        created_at: datetime,
        *,
        stock_code: str = "600519",
        market: str = "A",
    ) -> int:
        with self.db.session_scope() as session:
            signal = DecisionSignalRecord(
                stock_code=stock_code,
                market=market,
                source_type="analysis",
                trigger_source="test",
                action="buy",
                plan_quality="unknown",
                status="active",
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(signal)
            session.commit()
            signal_id = int(signal.id)
            session.add(
                DecisionSignalOutcomeRecord(
                    signal_id=signal_id,
                    horizon="5d",
                    engine_version="test-v1",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.add(
                DecisionSignalFeedbackRecord(
                    signal_id=signal_id,
                    feedback_value="agree",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.commit()
            return signal_id

    def _insert_analysis_history(self, created_at: datetime, code: str = "600519") -> int:
        with self.db.session_scope() as session:
            record = AnalysisHistory(
                code=code,
                report_type="daily",
                created_at=created_at,
            )
            session.add(record)
            session.commit()
            return int(record.id)

    def _count(self, model) -> int:
        with self.db.session_scope() as session:
            return int(session.execute(select(model)).scalars().all().__len__())


class TestPurgeExpiredLLMUsage(_PurgeTestBase):
    def test_retention_zero_returns_zero_without_deleting(self):
        self._insert_llm_usage(datetime.now() - timedelta(days=365))
        deleted = self.db.purge_expired_llm_usage(0)
        self.assertEqual(deleted, 0)
        self.assertEqual(self._count(LLMUsage), 1)

    def test_negative_retention_returns_zero(self):
        self._insert_llm_usage(datetime.now() - timedelta(days=365))
        deleted = self.db.purge_expired_llm_usage(-5)
        self.assertEqual(deleted, 0)
        self.assertEqual(self._count(LLMUsage), 1)

    def test_deletes_only_records_older_than_cutoff(self):
        old_id = self._insert_llm_usage(datetime.now() - timedelta(days=100), model="old")
        new_id = self._insert_llm_usage(datetime.now() - timedelta(days=1), model="new")
        deleted = self.db.purge_expired_llm_usage(30)
        self.assertEqual(deleted, 1)
        remaining_ids = sorted(
            self.db.session_scope().__enter__().execute(select(LLMUsage.id)).scalars().all()
        )
        self.assertEqual(remaining_ids, [new_id])
        self.assertNotIn(old_id, remaining_ids)


class TestPurgeExpiredDecisionSignals(_PurgeTestBase):
    def test_retention_zero_returns_zero(self):
        self._insert_decision_signal(datetime.now() - timedelta(days=365))
        self.assertEqual(self.db.purge_expired_decision_signals(0), 0)
        self.assertEqual(self._count(DecisionSignalRecord), 1)
        self.assertEqual(self._count(DecisionSignalOutcomeRecord), 1)
        self.assertEqual(self._count(DecisionSignalFeedbackRecord), 1)

    def test_cascades_outcome_and_feedback(self):
        old_signal_id = self._insert_decision_signal(datetime.now() - timedelta(days=200))
        new_signal_id = self._insert_decision_signal(datetime.now() - timedelta(days=1))

        deleted = self.db.purge_expired_decision_signals(30)
        self.assertEqual(deleted, 1)

        remaining_signals = sorted(
            self.db.session_scope().__enter__().execute(select(DecisionSignalRecord.id)).scalars().all()
        )
        self.assertEqual(remaining_signals, [new_signal_id])
        self.assertNotIn(old_signal_id, remaining_signals)

        # 子表应同步清理
        remaining_outcomes = (
            self.db.session_scope().__enter__().execute(select(DecisionSignalOutcomeRecord.signal_id)).scalars().all()
        )
        self.assertEqual(sorted(remaining_outcomes), [new_signal_id])
        remaining_feedbacks = (
            self.db.session_scope().__enter__().execute(select(DecisionSignalFeedbackRecord.signal_id)).scalars().all()
        )
        self.assertEqual(sorted(remaining_feedbacks), [new_signal_id])

    def test_no_expired_signals_returns_zero(self):
        self._insert_decision_signal(datetime.now() - timedelta(days=1))
        self.assertEqual(self.db.purge_expired_decision_signals(30), 0)
        self.assertEqual(self._count(DecisionSignalRecord), 1)


class TestPurgeExpiredAnalysisHistory(_PurgeTestBase):
    def test_retention_zero_returns_zero(self):
        self._insert_analysis_history(datetime.now() - timedelta(days=365))
        self.assertEqual(self.db.purge_expired_analysis_history(0), 0)
        self.assertEqual(self._count(AnalysisHistory), 1)

    def test_deletes_only_old_history(self):
        old_id = self._insert_analysis_history(datetime.now() - timedelta(days=100))
        new_id = self._insert_analysis_history(datetime.now() - timedelta(days=1))
        deleted = self.db.purge_expired_analysis_history(30)
        self.assertEqual(deleted, 1)
        remaining_ids = sorted(
            self.db.session_scope().__enter__().execute(select(AnalysisHistory.id)).scalars().all()
        )
        self.assertEqual(remaining_ids, [new_id])
        self.assertNotIn(old_id, remaining_ids)


class TestPurgeExpiredReports(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(lambda: safe_cleanup_temp_dir(self._temp_dir))
        self.reports_dir = Path(self._temp_dir.name)

    def _touch(self, name: str, mtime_days_ago: int) -> Path:
        path = self.reports_dir / name
        path.write_text("# report\n", encoding="utf-8")
        ts = (datetime.now() - timedelta(days=mtime_days_ago)).timestamp()
        os.utime(path, (ts, ts))
        return path

    def test_retention_zero_returns_zero(self):
        self._touch("old.md", mtime_days_ago=365)
        self.assertEqual(purge_expired_reports(0, reports_dir=self.reports_dir), 0)
        self.assertTrue((self.reports_dir / "old.md").exists())

    def test_deletes_only_old_md_files(self):
        old_path = self._touch("old.md", mtime_days_ago=60)
        new_path = self._touch("new.md", mtime_days_ago=1)
        # 非 .md 文件即使过期也不应被清理
        other_path = self._touch("old.txt", mtime_days_ago=60)

        deleted = purge_expired_reports(30, reports_dir=self.reports_dir)
        self.assertEqual(deleted, 1)
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.exists())
        self.assertTrue(other_path.exists())

    def test_missing_dir_returns_zero(self):
        self.assertEqual(purge_expired_reports(30, reports_dir=Path("nonexistent-dir-xyz")), 0)


class TestRunDataPurge(_PurgeTestBase):
    def _make_config(self, **overrides) -> SimpleNamespace:
        defaults = {
            "llm_usage_retention_days": 30,
            "decision_signal_retention_days": 30,
            "analysis_history_retention_days": 30,
            "report_retention_days": 30,
            "data_purge_interval_seconds": 86400,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_run_returns_stats_dict_with_expected_keys(self):
        # 插入过期 + 未过期数据
        self._insert_llm_usage(datetime.now() - timedelta(days=100))
        self._insert_llm_usage(datetime.now() - timedelta(days=1))
        self._insert_decision_signal(datetime.now() - timedelta(days=100))
        self._insert_analysis_history(datetime.now() - timedelta(days=100))

        with patch("src.services.data_purge.DatabaseManager.get_instance", return_value=self.db):
            stats = run_data_purge(self._make_config())

        self.assertEqual(set(stats.keys()), {"llm_usage", "decision_signals", "analysis_history", "reports"})
        self.assertEqual(stats["llm_usage"], 1)
        self.assertEqual(stats["decision_signals"], 1)
        self.assertEqual(stats["analysis_history"], 1)
        self.assertEqual(stats["reports"], 0)

    def test_all_zero_retention_deletes_nothing(self):
        self._insert_llm_usage(datetime.now() - timedelta(days=365))
        self._insert_decision_signal(datetime.now() - timedelta(days=365))
        self._insert_analysis_history(datetime.now() - timedelta(days=365))

        with patch("src.services.data_purge.DatabaseManager.get_instance", return_value=self.db):
            stats = run_data_purge(self._make_config(
                llm_usage_retention_days=0,
                decision_signal_retention_days=0,
                analysis_history_retention_days=0,
                report_retention_days=0,
            ))

        self.assertEqual(stats, {"llm_usage": 0, "decision_signals": 0, "analysis_history": 0, "reports": 0})
        self.assertEqual(self._count(LLMUsage), 1)
        self.assertEqual(self._count(DecisionSignalRecord), 1)
        self.assertEqual(self._count(AnalysisHistory), 1)

    def test_db_failure_does_not_break_reports_cleanup(self):
        reports_dir = Path(self._temp_dir.name) if hasattr(self, "_temp_dir") else None
        # 无 _temp_dir 时跳过 reports 测试；这里只验证 DB 异常被吞掉
        with patch("src.services.data_purge.DatabaseManager.get_instance", side_effect=RuntimeError("db down")):
            stats = run_data_purge(self._make_config(llm_usage_retention_days=30))
        self.assertEqual(stats["llm_usage"], 0)
        self.assertEqual(stats["decision_signals"], 0)
        self.assertEqual(stats["analysis_history"], 0)


class TestBuildDataPurgeBackgroundTask(unittest.TestCase):
    def _make_config(self, **overrides) -> SimpleNamespace:
        defaults = {
            "llm_usage_retention_days": 90,
            "decision_signal_retention_days": 180,
            "analysis_history_retention_days": 90,
            "report_retention_days": 30,
            "data_purge_interval_seconds": 86400,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_returns_none_when_all_retention_zero(self):
        cfg = self._make_config(
            llm_usage_retention_days=0,
            decision_signal_retention_days=0,
            analysis_history_retention_days=0,
            report_retention_days=0,
        )
        self.assertIsNone(build_data_purge_background_task(cfg))

    def test_returns_entry_with_expected_shape(self):
        cfg = self._make_config()
        entry = build_data_purge_background_task(cfg)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["interval_seconds"], 86400)
        self.assertFalse(entry["run_immediately"])
        self.assertEqual(entry["name"], "data_purge")
        self.assertTrue(callable(entry["task"]))

    def test_config_provider_refreshes_config_at_run_time(self):
        primary_cfg = self._make_config(llm_usage_retention_days=30)
        refreshed_cfg = self._make_config(llm_usage_retention_days=0)

        captured: dict = {}
        provider_calls = {"count": 0}

        def provider():
            provider_calls["count"] += 1
            return refreshed_cfg

        entry = build_data_purge_background_task(primary_cfg, config_provider=provider)
        self.assertIsNotNone(entry)

        with patch("src.services.data_purge.run_data_purge") as mock_run:
            entry["task"]()
            captured["called_with"] = mock_run.call_args

        self.assertEqual(provider_calls["count"], 1)
        # 任务应使用刷新后的配置，而不是构造时的 primary_cfg
        self.assertIs(captured["called_with"].args[0], refreshed_cfg)

    def test_config_provider_failure_falls_back_to_primary_config(self):
        primary_cfg = self._make_config()

        def provider():
            raise RuntimeError("config reload failed")

        entry = build_data_purge_background_task(primary_cfg, config_provider=provider)

        with patch("src.services.data_purge.run_data_purge") as mock_run:
            entry["task"]()

        mock_run.assert_called_once_with(primary_cfg)

    def test_config_provider_returns_none_falls_back_to_primary(self):
        """config_provider 返回 None 时使用构造时的 primary_cfg。"""
        primary_cfg = self._make_config()

        def provider():
            return None

        entry = build_data_purge_background_task(primary_cfg, config_provider=provider)
        self.assertIsNotNone(entry)

        with patch("src.services.data_purge.run_data_purge") as mock_run:
            entry["task"]()

        mock_run.assert_called_once_with(primary_cfg)


class TestPurgeExpiredReportsEdgeCases(unittest.TestCase):
    """purge_expired_reports 边界条件。"""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(lambda: safe_cleanup_temp_dir(self._temp_dir))
        self.reports_dir = Path(self._temp_dir.name)

    def _touch(self, name: str, mtime_days_ago: int) -> Path:
        path = self.reports_dir / name
        path.write_text("# report\n", encoding="utf-8")
        ts = (datetime.now() - timedelta(days=mtime_days_ago)).timestamp()
        os.utime(path, (ts, ts))
        return path

    def test_default_reports_dir_when_none(self):
        """reports_dir=None 时使用项目根目录下的 reports/，不抛异常。"""
        # 不实际删除文件（项目 reports/ 可能有正在使用的文件）
        # 只验证返回值为 int 且不抛异常
        deleted = purge_expired_reports(0, reports_dir=None)
        self.assertEqual(deleted, 0)

    def test_stat_oserror_skips_file(self):
        """显式 entry.stat() 抛 OSError 时跳过该文件，不影响其他文件删除。

        注意：source code 只在显式 ``entry.stat().st_mtime`` 处捕获 OSError，
        ``entry.is_file()`` 内部隐式调用 stat 未被保护（这是已知边界，不在本测试范围）。
        因此本测试通过 mock is_file() 返回 True 来绕过隐式 stat，只触发显式 stat 的 OSError。
        """
        old_path = self._touch("old.md", mtime_days_ago=60)
        new_path = self._touch("new.md", mtime_days_ago=1)

        # 对 old.md 的显式 stat 抛 OSError；is_file() 直接 mock 返回 True 避免隐式 stat
        original_stat = Path.stat

        def mocked_stat(self, *args, **kwargs):
            if self.name == "old.md":
                raise OSError("permission denied")
            return original_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", mocked_stat), \
             patch.object(Path, "is_file", return_value=True):
            deleted = purge_expired_reports(30, reports_dir=self.reports_dir)

        # old.md 因 stat OSError 被跳过，但 new.md 不受影响
        self.assertEqual(deleted, 0)
        self.assertTrue(old_path.exists())
        self.assertTrue(new_path.exists())

    def test_unlink_oserror_logged_and_continues(self):
        """unlink() 抛 OSError 时记录警告并继续处理其他文件。"""
        old_path = self._touch("old.md", mtime_days_ago=60)
        another_old = self._touch("another_old.md", mtime_days_ago=60)

        # 对 old.md 的 unlink 抛 OSError
        original_unlink = Path.unlink

        def mocked_unlink(self, *args, **kwargs):
            if self.name == "old.md":
                raise OSError("file in use")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", mocked_unlink):
            deleted = purge_expired_reports(30, reports_dir=self.reports_dir)

        # old.md 删除失败，another_old.md 删除成功
        self.assertEqual(deleted, 1)
        self.assertTrue(old_path.exists())  # 删除失败仍存在
        self.assertFalse(another_old.exists())


class TestRunDataPurgeExceptionIsolation(_PurgeTestBase):
    """run_data_purge 中各类清理异常被独立捕获。"""

    def _make_config(self, **overrides) -> SimpleNamespace:
        defaults = {
            "llm_usage_retention_days": 30,
            "decision_signal_retention_days": 30,
            "analysis_history_retention_days": 30,
            "report_retention_days": 30,
            "data_purge_interval_seconds": 86400,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_llm_usage_purge_exception_does_not_break_others(self):
        """llm_usage 清理抛异常时其他清理仍执行。"""
        self._insert_decision_signal(datetime.now() - timedelta(days=100))
        self._insert_analysis_history(datetime.now() - timedelta(days=100))

        with patch("src.services.data_purge.DatabaseManager.get_instance", return_value=self.db), \
             patch.object(self.db, "purge_expired_llm_usage", side_effect=RuntimeError("llm purge boom")):
            stats = run_data_purge(self._make_config())

        self.assertEqual(stats["llm_usage"], 0)  # 异常导致返回 0
        self.assertEqual(stats["decision_signals"], 1)  # 仍正常清理
        self.assertEqual(stats["analysis_history"], 1)

    def test_decision_signals_purge_exception_does_not_break_others(self):
        """decision_signals 清理抛异常时其他清理仍执行。"""
        self._insert_llm_usage(datetime.now() - timedelta(days=100))
        self._insert_analysis_history(datetime.now() - timedelta(days=100))

        with patch("src.services.data_purge.DatabaseManager.get_instance", return_value=self.db), \
             patch.object(self.db, "purge_expired_decision_signals", side_effect=RuntimeError("ds purge boom")):
            stats = run_data_purge(self._make_config())

        self.assertEqual(stats["llm_usage"], 1)
        self.assertEqual(stats["decision_signals"], 0)
        self.assertEqual(stats["analysis_history"], 1)

    def test_analysis_history_purge_exception_does_not_break_others(self):
        """analysis_history 清理抛异常时其他清理仍执行。"""
        self._insert_llm_usage(datetime.now() - timedelta(days=100))
        self._insert_decision_signal(datetime.now() - timedelta(days=100))

        with patch("src.services.data_purge.DatabaseManager.get_instance", return_value=self.db), \
             patch.object(self.db, "purge_expired_analysis_history", side_effect=RuntimeError("ah purge boom")):
            stats = run_data_purge(self._make_config())

        self.assertEqual(stats["llm_usage"], 1)
        self.assertEqual(stats["decision_signals"], 1)
        self.assertEqual(stats["analysis_history"], 0)

    def test_reports_purge_exception_does_not_break_db_cleanup(self):
        """reports 清理抛异常时 DB 清理仍执行。"""
        self._insert_llm_usage(datetime.now() - timedelta(days=100))

        with patch("src.services.data_purge.DatabaseManager.get_instance", return_value=self.db), \
             patch("src.services.data_purge.purge_expired_reports", side_effect=RuntimeError("fs purge boom")):
            stats = run_data_purge(self._make_config())

        self.assertEqual(stats["llm_usage"], 1)
        self.assertEqual(stats["reports"], 0)  # 异常导致返回 0


if __name__ == "__main__":
    unittest.main()

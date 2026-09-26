# -*- coding: utf-8 -*-
"""
Regression tests for pipeline notification routing (Feishu-only static channels).
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.core.pipeline import StockAnalysisPipeline, NotificationChannel
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    current_diagnostic_snapshot,
    reset_run_diagnostic_context,
)
from src.enums import ReportType


class _FakeFeishuNotifier:
    def __init__(self, routed_channels=None, noise_should_send=True):
        self._feishu_send_as_file = False
        self.generate_dashboard_report = MagicMock(side_effect=self._generate_dashboard_report)
        self.save_report_to_file = MagicMock(return_value="/tmp/report.md")
        self.is_available = MagicMock(return_value=True)
        self.get_available_channels = MagicMock(return_value=[NotificationChannel.FEISHU])
        self.get_channels_for_route = MagicMock(
            return_value=list(
                routed_channels if routed_channels is not None else [NotificationChannel.FEISHU]
            )
        )
        self.send_to_context = MagicMock(return_value=False)
        self.evaluate_noise_control = MagicMock(
            return_value=SimpleNamespace(
                should_send=noise_should_send,
                message="noise suppressed" if not noise_should_send else "",
            )
        )
        self.record_noise_control = MagicMock()
        self.release_noise_control = MagicMock()
        self.send_to_feishu = MagicMock(return_value=True)

    @staticmethod
    def _generate_dashboard_report(results):
        return "report:" + ",".join(r.code for r in results)


class TestPipelineReportRouteFiltering(unittest.TestCase):
    def test_send_notifications_applies_report_route_before_channel_iteration(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeFeishuNotifier([NotificationChannel.FEISHU])
        pipeline.config = SimpleNamespace()
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.get_channels_for_route.assert_called_once_with(
            "report",
            channels=[NotificationChannel.FEISHU],
        )
        pipeline.notifier.send_to_feishu.assert_called_once_with("report:000001")
        pipeline.notifier.evaluate_noise_control.assert_called_once()
        noise_kwargs = pipeline.notifier.evaluate_noise_control.call_args.kwargs
        self.assertEqual(noise_kwargs["dedup_key"], "report:aggregate:simple:000001")
        self.assertEqual(noise_kwargs["cooldown_key"], "report:aggregate:simple:000001")
        pipeline.notifier.record_noise_control.assert_called_once()

    def test_route_filtering_out_all_channels_skips_feishu_send(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeFeishuNotifier([])
        pipeline.config = SimpleNamespace()
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_feishu.assert_not_called()
        pipeline.notifier.record_noise_control.assert_not_called()

    def test_noise_suppression_skips_feishu_send(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeFeishuNotifier(noise_should_send=False)
        pipeline.config = SimpleNamespace()
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_feishu.assert_not_called()
        pipeline.notifier.record_noise_control.assert_not_called()

    def test_noise_reservation_released_when_pipeline_static_send_raises(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeFeishuNotifier()
        pipeline.notifier.send_to_feishu.side_effect = RuntimeError("send failed")
        pipeline.config = SimpleNamespace()
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.record_noise_control.assert_not_called()
        pipeline.notifier.release_noise_control.assert_called_once()

    def test_all_static_channel_failures_release_noise_reservation(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeFeishuNotifier()
        pipeline.notifier.send_to_feishu.return_value = False
        pipeline.config = SimpleNamespace()
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_feishu.assert_called_once_with("report:000001")
        pipeline.notifier.record_noise_control.assert_not_called()
        pipeline.notifier.release_noise_control.assert_called_once()

    def test_context_delivery_counts_as_success_and_is_recorded_with_routed_failures(self):
        token = activate_run_diagnostic_context(
            trace_id="trace-context",
            query_id="query-context",
            stock_code="000001",
            trigger_source="bot",
        )
        try:
            pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
            pipeline.notifier = _FakeFeishuNotifier()
            pipeline.notifier.send_to_context.return_value = True
            pipeline.notifier.send_to_feishu.return_value = False
            pipeline.config = SimpleNamespace()
            pipeline.save_context_snapshot = True
            pipeline.db = MagicMock()
            results = [SimpleNamespace(code="000001", query_id="query-context")]

            with patch("src.core.pipeline.logger.info") as mock_info:
                pipeline._send_notifications(results, ReportType.SIMPLE)
            snapshot = current_diagnostic_snapshot() or {}
        finally:
            reset_run_diagnostic_context(token)
        notification_runs = snapshot.get("notification_runs", [])
        self.assertEqual([run.get("channel") for run in notification_runs], ["__context__", "feishu"])
        self.assertTrue(notification_runs[0]["success"])
        self.assertFalse(notification_runs[1]["success"])
        self.assertTrue(
            any(
                call.args and call.args[0] == "决策仪表盘推送成功"
                for call in mock_info.call_args_list
            )
        )
        final_update = pipeline.db.update_analysis_history_diagnostics.call_args_list[-1]
        persisted_runs = final_update.kwargs["diagnostics"]["notification_runs"]
        self.assertEqual([run.get("channel") for run in persisted_runs], ["__context__", "feishu"])

    def test_context_only_delivery_skips_static_channels_in_aggregate_path(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeFeishuNotifier()
        pipeline.notifier.send_to_context.return_value = True
        pipeline.notifier.should_broadcast_static_channels = MagicMock(return_value=False)
        pipeline.config = SimpleNamespace()
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.should_broadcast_static_channels.assert_called_once_with()
        pipeline.notifier.send_to_feishu.assert_not_called()
        pipeline.notifier.evaluate_noise_control.assert_not_called()
        pipeline.notifier.record_noise_control.assert_not_called()
        pipeline.notifier.release_noise_control.assert_not_called()


class _FakeFeishuFileNotifier:
    def __init__(self):
        self._feishu_send_as_file = True
        self.generate_dashboard_report = MagicMock(
            return_value="[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"
        )
        self.save_report_to_file = MagicMock(return_value="/tmp/dashboard_20260802.md")
        self.is_available = MagicMock(return_value=True)
        self.get_available_channels = MagicMock(return_value=[NotificationChannel.FEISHU])
        self.get_channels_for_route = MagicMock(return_value=[NotificationChannel.FEISHU])
        self.send_to_context = MagicMock(return_value=False)
        self.evaluate_noise_control = MagicMock(
            return_value=SimpleNamespace(should_send=True, message="")
        )
        self.record_noise_control = MagicMock()
        self.release_noise_control = MagicMock()
        self.send_feishu_file = MagicMock(return_value=True)
        self.send_to_feishu = MagicMock(return_value=True)


class TestPipelineFeishuFileRouting(unittest.TestCase):
    def test_send_notifications_strips_hidden_market_metadata_before_saving_feishu_file(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeFeishuFileNotifier()
        pipeline.config = SimpleNamespace()
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.save_report_to_file.assert_called_once_with(
            "# 市场复盘\n\n正文",
            filename=mock.ANY,
        )
        pipeline.notifier.send_feishu_file.assert_called_once_with("/tmp/dashboard_20260802.md")
        pipeline.notifier.send_to_feishu.assert_not_called()


if __name__ == "__main__":
    unittest.main()

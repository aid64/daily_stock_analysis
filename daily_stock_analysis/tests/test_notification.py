# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 通知服务单元测试
===================================

职责：
1. 验证通知服务的配置检测逻辑
2. 验证通知服务的渠道检测逻辑
3. 验证通知服务的消息发送逻辑

P5：静态通知渠道收敛为仅飞书，其余 13 个渠道已移除。本测试只覆盖
飞书渠道以及与渠道无关的通用行为（报告生成、路由、降噪、上下文会话、
本地保存等）。
"""
import os
import sys
import unittest
from datetime import date
from unittest import mock
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep this test runnable when optional LLM/runtime deps are not installed.
for optional_module in ("litellm", "json_repair"):
    try:
        __import__(optional_module)
    except ModuleNotFoundError:
        sys.modules[optional_module] = mock.MagicMock()

from src.config import Config
from src.notification import NotificationBuilder, NotificationChannel, NotificationService
from src.notification_noise import reset_notification_noise_state
from src.analyzer import AnalysisResult
from src.share_image import build_share_image_html
from bot.models import BotMessage, ChatType
import requests


def _make_config(**overrides) -> Config:
    """Create a Config instance overriding only notification-related fields."""
    return Config(stock_list=[], **overrides)


def _make_response(status_code: int, json: Optional[dict] = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    if json:
        response.json = lambda: json
    return response


def _attach_decision_signal_summary(result: AnalysisResult) -> AnalysisResult:
    result.decision_signal_summary = {
        "action": "sell",
        "action_label": "卖出",
        "horizon": "1d",
        "reason": "技术面走弱",
    }
    return result


def _make_feishu_message() -> BotMessage:
    return BotMessage(
        platform="feishu",
        message_id="msg-1",
        user_id="user-1",
        user_name="tester",
        chat_id="chat-1",
        chat_type=ChatType.GROUP,
        content="/a 600519",
    )


class TestNotificationServiceSendToMethods(unittest.TestCase):
    """测试通知发送服务（飞书）

    测试设计：

    1. 添加模拟配置：
    使用 mock.patch 装饰器来模拟 get_config 函数，
    使用 _make_config 函数添加配置，并返回 Config 实例。

    2. 检查配置是否正确：
    使用 assertIn 检查 NotificationChannel.FEISHU 是否在
    `NotificationService.get_available_channels()` 返回值中。

    3. 模拟请求响应：
    使用 mock.patch 装饰器来模拟 requests.post 函数，
    使用 _make_response 函数模拟请求响应，并返回 Response 实例。
    若使用其他函数模拟请求响应，则使用 mock.patch 装饰器来模拟该函数。

    4. 使用 assertTrue 检查 send 的返回值。

    5. 使用 assert_called_once 检查请求函数是否被调用一次。
    测试分批发送时，使用 assertAlmostEqual(mock_post.call_count, ...) 检查请求函数被调用次数

    """

    def setUp(self):
        reset_notification_noise_state()

    @mock.patch("src.notification.get_config")
    def test_no_channels_service_unavailable_and_send_returns_false(self, mock_get_config):
        mock_get_config.return_value = _make_config()

        service = NotificationService()

        self.assertFalse(service.is_available())
        result = service.send("test content")
        self.assertFalse(result)

    @mock.patch("src.notification.get_config")
    def test_send_with_results_reports_channel_exception(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(feishu_webhook_url="https://feishu.example/hook")
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", side_effect=RuntimeError("token=secret-token failed")):
            result = service.send_with_results("content")

        self.assertTrue(result.dispatched)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "all_failed")
        by_channel = {item.channel: item for item in result.channel_results}
        self.assertFalse(by_channel["feishu"].success)
        self.assertEqual(by_channel["feishu"].error_code, "exception")
        self.assertNotIn("secret-token", by_channel["feishu"].diagnostics)

    @mock.patch("src.notification.get_config")
    def test_send_with_results_reports_route_no_channel(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            notification_report_channels=["unknown-route-channel"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            result = service.send_with_results("content", route_type="report")

        self.assertFalse(result.dispatched)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "no_channel")
        self.assertEqual(result.channel_results, [])
        mock_feishu.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_send_route_empty_keeps_configured_channels(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(feishu_webhook_url="https://feishu.example/hook")
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            ok = service.send("content", route_type="report")

        self.assertTrue(ok)
        mock_feishu.assert_called_once_with("content")

    @mock.patch("src.notification.get_config")
    def test_send_report_route_matching_feishu_channel(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            notification_report_channels=["feishu"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            ok = service.send("content", route_type="report")

        self.assertTrue(ok)
        mock_feishu.assert_called_once_with("content")

    @mock.patch("src.notification.get_config")
    def test_send_alert_and_system_error_routes_filter_independently(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            notification_alert_channels=["feishu"],
            notification_system_error_channels=["feishu"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            self.assertTrue(service.send("alert", route_type="alert"))
            self.assertTrue(service.send("system", route_type="system_error"))

        self.assertEqual(mock_feishu.call_count, 2)
        mock_feishu.assert_any_call("alert")
        mock_feishu.assert_any_call("system")

    @mock.patch("src.notification.get_config")
    def test_send_route_with_no_matching_channel_does_not_fallback(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            notification_report_channels=["unknown-route-channel"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            ok = service.send("content", route_type="report")

        self.assertFalse(ok)
        mock_feishu.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_send_to_context_is_not_limited_by_route(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            notification_report_channels=["telegram"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_context", return_value=True) as mock_context, \
             mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            ok = service.send("content", route_type="report")

        self.assertTrue(ok)
        mock_context.assert_called_once_with("content")
        mock_feishu.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_context_response_strips_hidden_metadata_and_skips_static_webhook(
        self,
        mock_get_config: mock.MagicMock,
    ):
        cfg = _make_config(
            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret",
        )
        mock_get_config.return_value = cfg
        service = NotificationService(source_message=_make_feishu_message())
        content = "[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"

        with mock.patch.object(service, "_send_feishu_stream_reply", return_value=True) as mock_reply, \
             mock.patch.object(service, "send_to_feishu", return_value=True) as mock_webhook:
            result = service.send_with_results(content, route_type="report")

        self.assertTrue(result.dispatched)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "sent")
        self.assertEqual([item.channel for item in result.channel_results], ["__context__"])
        mock_reply.assert_called_once_with("chat-1", "# 市场复盘\n\n正文")
        mock_webhook.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_send_feishu_stream_reply_strips_hidden_metadata_before_chunking(
        self,
        mock_get_config: mock.MagicMock,
    ):
        cfg = _make_config(
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret",
            feishu_max_bytes=10,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        reply_client = mock.MagicMock()
        content = "[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"

        with mock.patch("bot.platforms.feishu_stream.FEISHU_SDK_AVAILABLE", True), \
             mock.patch("src.config.get_config", return_value=cfg), \
             mock.patch("bot.platforms.feishu_stream.FeishuReplyClient", return_value=reply_client), \
             mock.patch.object(service, "_send_feishu_stream_chunked", return_value=True) as mock_chunked:
            result = service._send_feishu_stream_reply("chat-1", content)

        self.assertTrue(result)
        mock_chunked.assert_called_once_with(reply_client, "chat-1", "# 市场复盘\n\n正文", 10)
        reply_client.send_to_chat.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_context_failure_does_not_fallback_to_static_webhook(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret",
        )
        mock_get_config.return_value = cfg
        service = NotificationService(source_message=_make_feishu_message())

        with mock.patch.object(service, "_send_feishu_stream_reply", return_value=False), \
             mock.patch.object(service, "send_to_feishu", return_value=True) as mock_webhook:
            result = service.send_with_results("content", route_type="report")

        self.assertTrue(result.dispatched)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "all_failed")
        self.assertEqual([item.channel for item in result.channel_results], ["__context__"])
        mock_webhook.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_webhook_still_sends_without_source_context(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret",
        )
        mock_get_config.return_value = cfg
        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_webhook:
            result = service.send_with_results("content", route_type="report")

        self.assertTrue(result.dispatched)
        self.assertTrue(result.success)
        self.assertEqual([item.channel for item in result.channel_results], ["feishu"])
        mock_webhook.assert_called_once_with("content")

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_route_report_calls_send_feishu_file(self, mock_get_config):
        """When FEISHU_SEND_AS_FILE=true and route_type=report, use file sending."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_feishu_file", return_value=True) as mock_file, \
             mock.patch.object(service, "save_report_to_file", return_value="/tmp/report.md") as mock_save:
            result = service.send_with_results("report content", route_type="report")
        self.assertTrue(result.success)
        mock_file.assert_called_once()
        mock_save.assert_called_once_with("report content", filename=mock.ANY)

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_route_report_strips_hidden_metadata_before_save(self, mock_get_config):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        content = "[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"

        with mock.patch.object(service, "send_feishu_file", return_value=True), \
             mock.patch.object(service, "save_report_to_file", return_value="/tmp/report.md") as mock_save:
            result = service.send_with_results(content, route_type="report")

        self.assertTrue(result.success)
        saved_content = mock_save.call_args.args[0]
        self.assertNotIn("[dsa-market-region]", saved_content)
        self.assertIn("# 市场复盘", saved_content)

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_route_alert_calls_send_to_feishu(self, mock_get_config):
        """When FEISHU_SEND_AS_FILE=true but route_type=alert, use text sending."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("alert content", route_type="alert")
        self.assertTrue(result.success)
        mock_text.assert_called_once_with("alert content")
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_route_none_uses_text(self, mock_get_config):
        """When FEISHU_SEND_AS_FILE=true and route_type=None, use text (not file)."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("report content")
        self.assertTrue(result.success)
        mock_text.assert_called_once_with("report content")
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_false_uses_text_even_for_report(self, mock_get_config):
        """When FEISHU_SEND_AS_FILE=false (default), report still uses text."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=False,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("report content", route_type="report")
        self.assertTrue(result.success)
        mock_text.assert_called_once_with("report content")
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_system_error_uses_text(self, mock_get_config):
        """FEISHU_SEND_AS_FILE does not activate for system_error routes."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("error", route_type="system_error")
        self.assertTrue(result.success)
        mock_text.assert_called_once_with("error")
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_send_dedup_suppresses_static_channels_after_success(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            notification_dedup_ttl_seconds=60,
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            self.assertTrue(service.send("content at 12:00", route_type="report", dedup_key="report:aggregate:simple:600519"))
            self.assertFalse(service.send("content at 12:01", route_type="report", dedup_key="report:aggregate:simple:600519"))

        mock_feishu.assert_called_once_with("content at 12:00")

    @mock.patch("src.notification.get_config")
    def test_send_releases_noise_reservation_when_static_channels_fail(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            notification_dedup_ttl_seconds=60,
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", side_effect=[False, True]) as mock_feishu:
            self.assertFalse(
                service.send(
                    "content at 12:00",
                    route_type="report",
                    dedup_key="report:aggregate:simple:600519",
                )
            )
            self.assertTrue(
                service.send(
                    "content at 12:01",
                    route_type="report",
                    dedup_key="report:aggregate:simple:600519",
                )
            )

        self.assertEqual(mock_feishu.call_count, 2)

    @mock.patch("src.notification.get_config")
    def test_send_to_context_is_not_limited_by_noise_controls(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            notification_dedup_ttl_seconds=60,
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_context", return_value=True) as mock_context, \
             mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            self.assertTrue(service.send("content at 12:00", route_type="report", dedup_key="report:aggregate:simple:600519"))
            self.assertTrue(service.send("content at 12:01", route_type="report", dedup_key="report:aggregate:simple:600519"))

        self.assertEqual(mock_context.call_count, 2)
        mock_feishu.assert_called_once_with("content at 12:00")

    @mock.patch("src.notification.get_config")
    def test_noise_check_failure_does_not_block_static_send(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(feishu_webhook_url="https://feishu.example/hook")
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch("src.notification_noise._evaluate_notification_noise", side_effect=RuntimeError("boom")), \
             mock.patch.object(service, "send_to_feishu", return_value=True) as mock_feishu:
            ok = service.send("content", route_type="report")

        self.assertTrue(ok)
        mock_feishu.assert_called_once_with("content")

    @mock.patch("src.notification.get_config")
    def test_static_text_channels_strip_hidden_market_metadata_before_sender(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config()
        service = NotificationService()
        raw_content = "[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody"
        expected = "# 🎯 Market Review\n\nBody"
        cases = [
            (NotificationChannel.FEISHU, "send_to_feishu"),
        ]

        for channel, method_name in cases:
            with self.subTest(channel=channel.value), mock.patch.object(
                service, method_name, return_value=True
            ) as mock_sender:
                ok = service._send_to_static_channel(
                    channel,
                    raw_content,
                    image_bytes=None,
                    email_stock_codes=None,
                    email_send_to_all=False,
                    route_type="report",
                )

                self.assertTrue(ok)
                mock_sender.assert_called_once_with(expected)

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_feishu_via_notification_service(self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock):
        cfg = _make_config(feishu_webhook_url="https://feishu.example")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"code": 0})

        service = NotificationService()
        self.assertIn(NotificationChannel.FEISHU, service.get_available_channels())

        ok = service.send("hello feishu")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification_sender.feishu_sender.time.sleep")
    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_feishu_via_notification_service_requires_chunking(
        self,
        mock_post: mock.MagicMock,
        mock_get_config: mock.MagicMock,
        mock_sleep: mock.MagicMock,
    ):
        cfg = _make_config(feishu_webhook_url="https://feishu.example", feishu_max_bytes=2000)
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"code": 0})

        service = NotificationService()
        self.assertIn(NotificationChannel.FEISHU, service.get_available_channels())

        ok = service.send("A" * 6000)

        self.assertTrue(ok)
        self.assertAlmostEqual(mock_post.call_count, 4, delta=1)
        self.assertEqual(mock_sleep.call_count, mock_post.call_count - 1)


class TestNotificationServiceReportGeneration(unittest.TestCase):
    """报告生成与选路相关测试。"""

    @staticmethod
    def _source_result(
        code: str,
        name: str,
        score: int,
        data_sources: Optional[str],
    ) -> AnalysisResult:
        result = AnalysisResult(
            code=code,
            name=name,
            sentiment_score=score,
            trend_prediction="震荡",
            operation_advice="观望",
            analysis_summary=f"{name} summary",
            report_language="zh",
            data_sources=data_sources or "",
        )
        if data_sources is None:
            setattr(result, "data_sources", None)
        return result

    def test_signal_metadata_uses_resolved_eight_state_action(self):
        service = NotificationService()
        cases = [
            ("avoid", "Avoid", 90, ("Avoid", "🟡", "hold")),
            ("add", "Add", 50, ("Add", "🟢", "buy")),
            ("alert", "Alert", 85, ("Alert", "🔴", "sell")),
        ]

        for action, action_label, score, expected in cases:
            with self.subTest(action=action):
                result = AnalysisResult(
                    code="AAPL",
                    name="Apple",
                    sentiment_score=score,
                    trend_prediction="Neutral",
                    operation_advice="Hold",
                    report_language="en",
                    action=action,
                    action_label=action_label,
                )

                self.assertEqual(service._get_signal_level(result), expected)

    def test_build_stock_summary_uses_resolved_eight_state_action(self):
        summary = NotificationBuilder.build_stock_summary(
            [
                AnalysisResult(
                    code="AVOID",
                    name="Avoid Corp",
                    sentiment_score=90,
                    trend_prediction="Neutral",
                    operation_advice="Avoid",
                    report_language="en",
                    action="avoid",
                    action_label="Avoid",
                ),
                AnalysisResult(
                    code="ALERT",
                    name="Alert Corp",
                    sentiment_score=85,
                    trend_prediction="Neutral",
                    operation_advice="Alert",
                    report_language="en",
                    action="alert",
                    action_label="Alert",
                ),
                AnalysisResult(
                    code="ADD",
                    name="Add Corp",
                    sentiment_score=50,
                    trend_prediction="Neutral",
                    operation_advice="Add",
                    report_language="en",
                    action="add",
                    action_label="Add",
                ),
            ]
        )

        self.assertIn("🟡 Avoid Corp(AVOID): Avoid | Score 90", summary)
        self.assertIn("🔴 Alert Corp(ALERT): Alert | Score 85", summary)
        self.assertIn("🟢 Add Corp(ADD): Add | Score 50", summary)
        self.assertNotIn("Buy | Score 50", summary)
        self.assertNotIn("Strong Buy", summary)

    @mock.patch("src.notification.get_config")
    def test_report_rows_and_summary_use_same_score_aligned_action(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=72,
            trend_prediction="Bullish",
            operation_advice="Hold",
            decision_type="hold",
            report_language="en",
        )

        out = service.generate_brief_report([result], report_date="2026-07-11")

        self.assertIn("🟢1 🟡0 🔴0", out)
        self.assertIn("Buy | Score 72", out)
        self.assertNotIn("Hold | Score 72", out)

    @mock.patch("src.notification.get_config")
    def test_generate_aggregate_report_routes_by_report_type(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config()
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )

        with mock.patch.object(service, "generate_dashboard_report", return_value="dashboard") as mock_dashboard, mock.patch.object(
            service, "generate_brief_report", return_value="brief"
        ) as mock_brief:
            self.assertEqual(service.generate_aggregate_report([result], "simple"), "dashboard")
            self.assertEqual(service.generate_aggregate_report([result], "full"), "dashboard")
            self.assertEqual(service.generate_aggregate_report([result], "detailed"), "dashboard")
            self.assertEqual(service.generate_aggregate_report([result], "brief"), "brief")

        self.assertEqual(mock_dashboard.call_count, 3)
        mock_brief.assert_called_once()

    @mock.patch("src.notification.get_config")
    def test_dashboard_sources_are_isolated_in_builtin_and_template_renderers(
        self, mock_get_config: mock.MagicMock
    ):
        alpha = self._source_result("AAA", "Alpha", 80, "agent:a,daily:AlphaFetcher")
        empty = self._source_result("NONE", "NoSource", 70, None)
        beta = self._source_result("BBB", "Beta", 60, "agent:b,daily:BetaFetcher")

        for renderer_enabled in (False, True):
            with self.subTest(renderer_enabled=renderer_enabled):
                mock_get_config.return_value = _make_config(
                    report_renderer_enabled=renderer_enabled
                )
                service = NotificationService()
                out = service.generate_dashboard_report(
                    [beta, empty, alpha], report_date="2026-08-27"
                )

                alpha_start = out.index("Alpha (AAA)")
                empty_start = out.index("NoSource (NONE)")
                beta_start = out.index("Beta (BBB)")
                alpha_block = out[alpha_start:empty_start]
                empty_block = out[empty_start:beta_start]
                beta_block = out[beta_start:]

                self.assertIn("agent:a,daily:AlphaFetcher", alpha_block)
                self.assertNotIn("daily:BetaFetcher", alpha_block)
                self.assertNotIn("数据来源", empty_block)
                self.assertIn("agent:b,daily:BetaFetcher", beta_block)
                self.assertNotIn("daily:AlphaFetcher", beta_block)
                self.assertEqual(out.count("agent:a,daily:AlphaFetcher"), 1)
                self.assertEqual(out.count("agent:b,daily:BetaFetcher"), 1)
                if not renderer_enabled:
                    self.assertIn(
                        "agent:a,daily:AlphaFetcher*\n\n---",
                        alpha_block,
                    )

    @mock.patch("src.notification.get_config")
    def test_brief_sources_are_isolated_in_builtin_and_template_renderers(
        self, mock_get_config: mock.MagicMock
    ):
        alpha = self._source_result("AAA", "Alpha", 80, "daily:AlphaFetcher")
        empty = self._source_result("NONE", "NoSource", 70, "")
        beta = self._source_result("BBB", "Beta", 60, "daily:BetaFetcher")

        for renderer_enabled in (False, True):
            with self.subTest(renderer_enabled=renderer_enabled):
                mock_get_config.return_value = _make_config(
                    report_renderer_enabled=renderer_enabled
                )
                service = NotificationService()
                out = service.generate_brief_report(
                    [beta, empty, alpha], report_date="2026-08-27"
                )

                alpha_start = out.index("**Alpha(AAA)**")
                empty_start = out.index("**NoSource(NONE)**")
                beta_start = out.index("**Beta(BBB)**")
                alpha_block = out[alpha_start:empty_start]
                empty_block = out[empty_start:beta_start]
                beta_block = out[beta_start:]

                self.assertIn("daily:AlphaFetcher", alpha_block)
                self.assertNotIn("daily:BetaFetcher", alpha_block)
                self.assertNotIn("数据来源", empty_block)
                self.assertIn("daily:BetaFetcher", beta_block)
                self.assertNotIn("daily:AlphaFetcher", beta_block)
                self.assertEqual(out.count("daily:AlphaFetcher"), 1)
                self.assertEqual(out.count("daily:BetaFetcher"), 1)

    @mock.patch("src.notification.get_config")
    def test_dashboard_summary_only_keeps_sources_without_restoring_details(
        self, mock_get_config: mock.MagicMock
    ):
        result = self._source_result("AAA", "Alpha", 80, "daily:AlphaFetcher")
        result.dashboard = {
            "core_conclusion": {"one_sentence": "summary-only conclusion"},
            "battle_plan": {"entry_plan": "detail-only plan"},
        }

        for renderer_enabled in (False, True):
            with self.subTest(renderer_enabled=renderer_enabled):
                mock_get_config.return_value = _make_config(
                    report_renderer_enabled=renderer_enabled
                )
                service = NotificationService()
                service._report_summary_only = True
                out = service.generate_dashboard_report(
                    [result], report_date="2026-08-27"
                )

                self.assertEqual(out.count("daily:AlphaFetcher"), 1)
                self.assertNotIn("核心结论", out)
                self.assertNotIn("detail-only plan", out)

    @mock.patch("src.notification.get_config")
    def test_empty_template_output_falls_back_to_source_aware_reports(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        result = self._source_result("AAA", "Alpha", 80, "daily:AlphaFetcher")
        service = NotificationService()

        with mock.patch("src.services.report_renderer.render", return_value=None) as render:
            dashboard = service.generate_dashboard_report(
                [result], report_date="2026-08-27"
            )
            brief = service.generate_brief_report([result], report_date="2026-08-27")

        self.assertEqual(render.call_count, 2)
        self.assertEqual(dashboard.count("daily:AlphaFetcher"), 1)
        self.assertEqual(brief.count("daily:AlphaFetcher"), 1)
        self.assertIn("*📋 数据来源：daily:AlphaFetcher*", dashboard)
        self.assertIn("*📋 数据来源：daily:AlphaFetcher*", brief)

    @mock.patch("src.notification.get_config")
    def test_source_labels_render_in_all_supported_languages(
        self, mock_get_config: mock.MagicMock
    ):
        labels_by_language = {
            "zh": "数据来源",
            "en": "Data Sources",
            "ko": "데이터 출처",
        }
        for language, expected_label in labels_by_language.items():
            for renderer_enabled in (False, True):
                for report_type in ("simple", "brief"):
                    with self.subTest(
                        language=language,
                        renderer_enabled=renderer_enabled,
                        report_type=report_type,
                    ):
                        mock_get_config.return_value = _make_config(
                            report_renderer_enabled=renderer_enabled
                        )
                        service = NotificationService()
                        result = self._source_result(
                            "AAA", "Alpha", 80, "daily:AlphaFetcher"
                        )
                        result.report_language = language

                        out = service.generate_aggregate_report(
                            [result], report_type, report_date="2026-08-27"
                        )

                        self.assertIn(
                            f"*📋 {expected_label}：daily:AlphaFetcher*",
                            out,
                        )

    @mock.patch("src.notification.get_config")
    def test_reports_omit_source_label_when_data_sources_are_missing(
        self, mock_get_config: mock.MagicMock
    ):
        for data_sources in (None, "", "   "):
            for renderer_enabled in (False, True):
                for report_type in ("simple", "brief"):
                    with self.subTest(
                        data_sources=data_sources,
                        renderer_enabled=renderer_enabled,
                        report_type=report_type,
                    ):
                        mock_get_config.return_value = _make_config(
                            report_renderer_enabled=renderer_enabled
                        )
                        service = NotificationService()
                        result = self._source_result(
                            "AAA", "Alpha", 80, data_sources
                        )

                        out = service.generate_aggregate_report(
                            [result], report_type, report_date="2026-08-27"
                        )

                        self.assertNotIn("数据来源", out)

    @mock.patch("src.notification.get_config")
    def test_generate_daily_report_keeps_existing_source_line(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = self._source_result(
            "AAA", "Alpha", 80, "agent:a,daily:AlphaFetcher"
        )

        out = service.generate_daily_report([result], report_date="2026-08-27")

        self.assertEqual(
            out.count("*📋 数据来源：agent:a,daily:AlphaFetcher*"),
            1,
        )

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_keeps_legacy_simple_format(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )

        with mock.patch("src.services.report_renderer.render") as mock_render:
            out = service.generate_single_stock_report(result)

        mock_render.assert_not_called()
        self.assertIn("贵州茅台", out)
        self.assertIn("600519", out)

    @mock.patch("src.notification.get_config")
    def test_generate_brief_report_shows_model_by_default(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            model_used="gemini/gemini-2.5-flash",
        )

        out = service.generate_brief_report([result], report_date="2026-02-01")

        self.assertIn("*分析模型: gemini/gemini-2.5-flash*", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_shows_model_by_default(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            model_used="gemini/gemini-2.5-flash",
        )

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertIn("*分析模型：gemini/gemini-2.5-flash*", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_shows_phase_decision_in_default_renderer(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="等待确认",
            dashboard={
                "core_conclusion": {"one_sentence": "等待确认"},
                "phase_decision": {
                    "action_window": "盘中跟踪",
                    "immediate_action": "等待确认",
                    "watch_conditions": ["放量突破"],
                    "next_check_time": "14:30",
                    "confidence_reason": "数据质量可用",
                    # E-freeze：原始枚举行（quote: stale）在渲染层被过滤，
                    # 限制段只保留完整中文句
                    "data_limitations": ["quote: stale", "行情数据存在延迟"],
                },
            },
        )

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertIn("盘中决策护栏", out)
        self.assertIn("盘中跟踪", out)
        self.assertIn("等待确认", out)
        self.assertIn("放量突破", out)
        self.assertIn("行情数据存在延迟", out)
        self.assertNotIn("quote: stale", out)  # 枚举行不再泄漏到用户报告

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_skips_context_only_phase_decision_default_renderer(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="等待确认",
            dashboard={
                "core_conclusion": {"one_sentence": "等待确认"},
                "phase_decision": {
                    "phase_context": {"phase": "intraday", "market": "cn"},
                    "watch_conditions": [],
                    "data_limitations": [],
                },
            },
        )

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertNotIn("盘中决策护栏", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_omits_decision_signal_excerpt_fallback(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertNotIn("AI 决策信号", out)
        self.assertNotIn("理由: 技术面走弱", out)

    @mock.patch("src.notification.get_config")
    def test_generate_daily_report_omits_decision_signal_excerpt_fallback(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        for summary_only in (True, False):
            service = NotificationService()
            service._report_summary_only = summary_only
            out = service.generate_daily_report([result], report_date="2026-02-01")
            self.assertNotIn("AI 决策信号", out)
            self.assertNotIn("理由: 技术面走弱", out)

    @mock.patch("src.notification.get_config")
    def test_generate_wechat_dashboard_omits_decision_signal_excerpt_fallback(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        for summary_only in (True, False):
            service = NotificationService()
            service._report_summary_only = summary_only
            out = service.generate_wechat_dashboard([result])
            self.assertNotIn("AI 决策信号", out)
            self.assertNotIn("理由: 技术面走弱", out)

    @mock.patch("src.notification.get_config")
    def test_strategy_synthesis_legacy_shapes_are_safe_in_fallback_reports(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()

        for malformed in ("bad-shape", ["bad-shape"], 42, True):
            result = AnalysisResult(
                code="600519",
                name="贵州茅台",
                sentiment_score=50,
                trend_prediction="震荡",
                operation_advice="观望",
                report_language="zh",
                dashboard={
                    "core_conclusion": {"one_sentence": "测试"},
                    "intelligence": {},
                    "battle_plan": {},
                    "strategy_synthesis": malformed,
                },
            )

            markdown = service.generate_dashboard_report([result], report_date="2026-07-19")
            wechat = service.generate_wechat_dashboard([result])

            self.assertNotIn("多策略综合", markdown)
            self.assertNotIn("多策略综合", wechat)

        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=50,
            trend_prediction="震荡",
            operation_advice="观望",
            report_language="zh",
            dashboard={
                "core_conclusion": {"one_sentence": "测试"},
                "intelligence": {},
                "battle_plan": {},
                "strategy_synthesis": {
                    "final_signal": "hold",
                    "consensus_level": "insufficient",
                    "conflict_severity": "none",
                    "conflict_count": 0,
                    "supporting_skills": "bad-shape",
                    "opposing_skills": ["bad-shape"],
                    "conflicts": "bad-shape",
                    "summary_params": {"invalid_opinion_count": "3"},
                },
            },
        )

        markdown = service.generate_dashboard_report([result], report_date="2026-07-19")
        wechat = service.generate_wechat_dashboard([result])

        self.assertIn("另有 3 个策略解析失败", markdown)
        self.assertIn("另有 3 个策略解析失败", wechat)

    @mock.patch("src.notification.get_config")
    def test_generate_wechat_summary_omits_decision_signal_excerpt(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        out = service.generate_wechat_summary([result])

        self.assertNotIn("AI 决策信号", out)
        self.assertNotIn("动作: 卖出", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_omits_decision_signal_excerpt_with_renderer(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        service = NotificationService()
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertNotIn("AI 决策信号", out)
        self.assertNotIn("理由: 技术面走弱", out)

    @mock.patch("src.notification.get_config")
    def test_aggregate_reports_show_compact_market_status_only(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.market_phase_summary = {
            "phase": "intraday",
            "market": "cn",
            "trigger_source": "portfolio",
            "is_partial_bar": True,
        }
        result.analysis_context_pack_overview = {
            "data_quality": {
                "level": "limited",
                "limitations": ["quote: stale", "news: missing", "portfolio_context: hidden"],
            }
        }
        result.raw_response = "raw context pack and prompt should not appear"

        out = service.generate_brief_report([result], report_date="2026-02-01")

        self.assertIn("市场状态：A股 · 盘中", out)
        self.assertNotIn("阶段：intraday", out)
        self.assertNotIn("触发来源：portfolio", out)
        self.assertNotIn("盘中数据提示", out)
        self.assertNotIn("数据质量: limited", out)
        self.assertNotIn("限制: quote: stale", out)
        self.assertNotIn("限制: news: missing", out)
        self.assertNotIn("portfolio_context: hidden", out)
        self.assertNotIn("raw context pack", out)
        self.assertNotIn("prompt", out.lower())

    @mock.patch("src.notification.get_config")
    def test_template_dashboard_report_uses_single_market_status_line(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.market_phase_summary = {
            "phase": "postmarket",
            "market": "cn",
            "trigger_source": "cli",
        }
        result.analysis_context_pack_overview = {
            "data_quality": {
                "level": "good",
                "limitations": ["technical: partial"],
            }
        }

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertIn("市场状态：A股 · 盘后", out)
        self.assertEqual(out.count("市场状态："), 1)
        self.assertNotIn("阶段：postmarket", out)
        self.assertNotIn("触发来源：cli", out)
        self.assertNotIn("数据质量: good", out)
        self.assertNotIn("technical: partial", out)

    @mock.patch("src.notification.get_config")
    def test_generated_reports_skip_phase_pack_excerpt_when_summary_missing(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )

        out = service.generate_brief_report([result], report_date="2026-02-01")

        self.assertNotIn("摘要来源", out)
        self.assertNotIn("评估器快照", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_collapses_unavailable_chip_structure(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            dashboard={
                "data_perspective": {
                    "chip_structure": {
                        "profit_ratio": "数据缺失，无法判断",
                        "avg_cost": "数据缺失，无法判断",
                        "concentration": "数据缺失，无法判断",
                        "chip_health": "数据缺失，无法判断",
                    }
                }
            },
        )

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertIn("**筹码**: 筹码分布未启用或数据源暂不可用，未纳入筹码判断。", out)
        self.assertEqual(out.count("数据缺失，无法判断"), 0)

    @mock.patch("src.notification.get_config")
    def test_generate_reports_hide_model_when_disabled(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(
            report_renderer_enabled=False,
            report_show_llm_model=False,
        )
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            model_used="gemini/gemini-2.5-flash",
        )

        dashboard = service.generate_dashboard_report([result], report_date="2026-02-01")
        single = service.generate_single_stock_report(result)

        self.assertNotIn("分析模型", dashboard)
        self.assertNotIn("gemini/gemini-2.5-flash", dashboard)
        self.assertNotIn("分析模型", single)
        self.assertNotIn("gemini/gemini-2.5-flash", single)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_localizes_english_fallback(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False, report_language="en")
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=78,
            trend_prediction="Bullish",
            operation_advice="Buy",
            analysis_summary="Momentum remains constructive.",
            decision_type="buy",
            report_language="en",
            dashboard={
                "core_conclusion": {
                    "one_sentence": "Favor buying on pullbacks.",
                    "position_advice": {
                        "no_position": "Open a starter position.",
                        "has_position": "Hold and trail the stop.",
                    },
                },
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "180-182",
                        "stop_loss": "172",
                        "take_profit": "195",
                    }
                },
            },
        )

        out = service.generate_dashboard_report([result], report_date="2026-03-18")

        self.assertIn("Decision Dashboard", out)
        self.assertIn("Summary", out)
        self.assertIn("Action Levels", out)
        self.assertIn("Buy", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_localizes_english_no_dashboard_fallback(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False, report_language="en")
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=61,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="Wait for confirmation.",
            report_language="en",
            buy_reason="Momentum remains constructive.",
            risk_warning="Watch for a failed breakout.",
            ma_analysis="Price remains above MA20.",
            volume_analysis="Volume is steady.",
            news_summary="Product cycle remains supportive.",
        )

        out = service.generate_dashboard_report([result], report_date="2026-03-19")

        self.assertIn("Rationale", out)
        self.assertIn("Risk Warning", out)
        self.assertIn("Technicals", out)
        self.assertIn("Moving Averages", out)
        self.assertIn("Volume", out)
        self.assertIn("News Flow", out)
        self.assertNotIn("操作理由", out)
        self.assertNotIn("风险提示", out)
        self.assertNotIn("技术面", out)
        self.assertNotIn("消息面", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_aligns_english_fallback_with_score(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False, report_language="en")
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=65,
            trend_prediction="Sideways",
            operation_advice="Hold",
            analysis_summary="Wait for a cleaner breakout.",
            report_language="en",
            dashboard={
                "core_conclusion": {"one_sentence": "Wait for confirmation."},
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "190",
                        "stop_loss": "182",
                        "take_profit": "205",
                    }
                },
            },
        )

        out = service.generate_single_stock_report(result)

        self.assertIn("Core Conclusion", out)
        self.assertIn("Action Levels", out)
        self.assertIn("Buy", out)

    @mock.patch("src.notification.get_config")
    def test_single_stock_share_image_reads_trend_from_generated_report(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="回调到支撑区可分批关注。",
            dashboard={
                "core_conclusion": {"one_sentence": "回调到支撑区可分批关注。"},
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "1420-1450",
                        "stop_loss": "1350",
                        "take_profit": "1580",
                    }
                },
            },
        )

        markdown = service.generate_single_stock_report(result)
        html = build_share_image_html(markdown, generated_on=date(2026, 7, 31))

        self.assertIn('class="signal-trend positive"', html)
        self.assertIn(">看多<", html)
        self.assertIn("个股决策卡", html)

    @mock.patch("src.notification.get_config")
    def test_dashboard_share_image_reads_chinese_volume_line_from_generated_report(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健持有。",
            dashboard={
                "core_conclusion": {"one_sentence": "稳健持有。"},
                "data_perspective": {
                    "trend_status": {
                        "ma_alignment": "多头排列",
                        "is_bullish": True,
                        "trend_score": 78,
                    },
                    "price_position": {
                        "current_price": "1450",
                        "ma5": "1442",
                        "ma10": "1430",
                        "ma20": "1408",
                        "bias_ma5": "0.55",
                        "bias_status": "偏强",
                        "support_level": "1420",
                        "resistance_level": "1490",
                    },
                    "volume_analysis": {
                        "volume_ratio": "1.35",
                        "volume_status": "放量",
                        "turnover_rate": "0.82",
                        "volume_meaning": "量能配合上攻",
                    },
                },
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "1420-1450",
                        "stop_loss": "1350",
                        "take_profit": "1580",
                    }
                },
            },
        )

        markdown = service.generate_dashboard_report([result], report_date="2026-07-31")
        html = build_share_image_html(markdown, generated_on=date(2026, 7, 31))

        self.assertIn("技术参考", html)
        self.assertIn(">量能<", html)
        self.assertIn("1.35", html)
        self.assertIn("0.82%", html)

    def _make_fundamental_context(self) -> dict:
        return {
            "earnings": {
                "status": "ok",
                "data": {
                    "financial_report": {
                        "report_date": "2024-09-30",
                        "revenue": 1_236_000_000_000.0,  # 1.236 万亿 -> 12360.00 亿元
                        "net_profit_parent": 60_800_000_000.0,
                        "operating_cash_flow": 72_500_000_000.0,
                        "roe": 22.45,
                    },
                    "dividend": {
                        "events": [
                            {
                                "event_date": "2024-06-26",
                                "ex_dividend_date": "2024-06-26",
                                "cash_dividend_per_share": 30.876,
                                "is_pre_tax": True,
                            }
                        ],
                        "ttm_event_count": 1,
                        "ttm_cash_dividend_per_share": 30.876,
                        "ttm_dividend_yield_pct": 1.85,
                    },
                },
            },
            "growth": {
                "status": "ok",
                "data": {
                    "revenue_yoy": 15.23,
                    "net_profit_yoy": 19.87,
                    "roe": 22.45,
                    "gross_margin": 91.55,
                },
            },
            "boards": {
                "status": "ok",
                "data": {
                    "top": [
                        {"name": "白酒", "change_pct": 3.42},
                        {"name": "食品饮料", "change_pct": 2.10},
                    ],
                    "bottom": [
                        {"name": "光伏设备", "change_pct": -2.65},
                    ],
                },
            },
            "concept_boards": {
                "status": "ok",
                "data": {
                    "top": [
                        {"name": "MSCI中国", "change_pct": 1.23},
                    ],
                    "bottom": [],
                },
            },
            "belong_boards": [
                {"name": "白酒", "code": "BK0596", "type": "行业"},
                {"name": "MSCI中国", "code": "BK0805", "type": "概念"},
            ],
        }

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_appends_fundamental_blocks(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = self._make_fundamental_context()

        out = service.generate_single_stock_report(result)

        # 财务摘要
        self.assertIn("财务摘要", out)
        self.assertIn("2024-09-30", out)
        self.assertIn("12360.00 亿元", out)
        self.assertIn("22.45%", out)
        self.assertIn("15.23%", out)
        self.assertIn("91.55%", out)
        # 股东回报
        self.assertIn("股东回报", out)
        self.assertIn("30.8760 元", out)
        self.assertIn("1.85%", out)
        self.assertIn("2024-06-26", out)
        # 关联板块（白酒带行业信号；MSCI中国 带概念信号）
        self.assertIn("关联板块", out)
        self.assertIn("白酒", out)
        self.assertIn("领涨", out)
        self.assertIn("+3.42%", out)
        self.assertIn("MSCI中国", out)
        self.assertIn("- 白酒 (行业板块 领涨 +3.42%)", out)
        self.assertIn("- MSCI中国 (概念板块 领涨 +1.23%)", out)
        self.assertIn("+1.23%", out)
        self.assertNotIn("| 板块 | 类型 | 板块表现 | 板块涨跌幅 |", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_uses_concept_rankings_for_concept_boards(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "boards": {"status": "ok", "data": {
                "top": [{"name": "白酒", "change_pct": 2.31}],
                "bottom": [],
            }},
            "concept_boards": {"status": "ok", "data": {
                "top": [],
                "bottom": [{"name": "白酒", "change_pct": -3.2}],
            }},
            "belong_boards": [{"name": "白酒", "type": "概念"}],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("关联板块", out)
        self.assertIn("- 白酒 (概念板块 领跌 -3.20%)", out)
        self.assertNotIn("| 白酒 | 概念 |", out)
        self.assertNotIn("+2.31%", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_skips_fundamental_blocks_when_missing(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )

        out = service.generate_single_stock_report(result)

        self.assertNotIn("财务摘要", out)
        self.assertNotIn("股东回报", out)
        self.assertNotIn("关联板块", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_handles_partial_fundamental_context(
        self, mock_get_config: mock.MagicMock
    ):
        """Only dividend data present — render shareholder return, skip the other two."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {
                "status": "partial",
                "data": {
                    "dividend": {
                        "events": [],
                        "ttm_event_count": 0,
                        "ttm_cash_dividend_per_share": 0.5,
                    }
                },
            },
        }

        out = service.generate_single_stock_report(result)

        self.assertNotIn("财务摘要", out)
        self.assertIn("股东回报", out)
        self.assertIn("0.5000 元", out)
        self.assertNotIn("关联板块", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_uses_currency_for_us(
        self, mock_get_config: mock.MagicMock
    ):
        """USD currency on financial_report yields 亿美元 suffix instead of 亿元."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple Inc.",
            sentiment_score=64,
            trend_prediction="震荡",
            operation_advice="观望",
            analysis_summary="观望等待 AI 兑现节奏。",
        )
        result.fundamental_context = {
            "earnings": {
                "status": "ok",
                "data": {
                    "financial_report": {
                        "report_date": "2026-03-31",
                        "revenue": 1.11e11,
                        "net_profit_parent": 2.95e10,
                        "operating_cash_flow": 2.87e10,
                        "roe": 141.47,
                        "currency": "USD",
                    },
                    "dividend": {
                        "events": [{
                            "event_date": "2026-05-11",
                            "ex_dividend_date": "2026-05-11",
                            "cash_dividend_per_share": 0.27,
                            "is_pre_tax": True,
                        }],
                        "ttm_event_count": 4,
                        "ttm_cash_dividend_per_share": 1.05,
                        "ttm_dividend_yield_pct": 0.36,
                    },
                },
            },
            "growth": {"status": "ok", "data": {"revenue_yoy": 16.60, "roe": 141.47, "gross_margin": 47.86}},
            "belong_boards": [
                {"name": "Technology", "type": "行业"},
                {"name": "Consumer Electronics", "type": "概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("财务摘要", out)
        self.assertIn("亿美元", out)
        self.assertNotIn("12360.00 亿元", out)
        # Sample expected formatted values
        self.assertIn("1110.00 亿美元", out)
        self.assertIn("141.47%", out)
        # Dividend per share also picks up currency suffix
        self.assertIn("1.0500 美元", out)
        # Sector + industry render as belong_boards
        self.assertIn("Technology", out)
        self.assertIn("Consumer Electronics", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_drops_signal_columns_when_no_sector_data(
        self, mock_get_config: mock.MagicMock
    ):
        """HK/US lack 板块涨跌榜 — drop status / change_pct columns entirely."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple Inc.",
            sentiment_score=64,
            trend_prediction="震荡",
            operation_advice="观望",
            analysis_summary="观望等待 AI 兑现节奏。",
        )
        result.fundamental_context = {
            "earnings": {"status": "ok", "data": {
                "financial_report": {
                    "report_date": "2026-03-31",
                    "revenue": 1.11e11,
                    "currency": "USD",
                },
            }},
            "growth": {"status": "ok", "data": {"revenue_yoy": 16.60}},
            "belong_boards": [
                {"name": "Technology", "type": "行业"},
                {"name": "Consumer Electronics", "type": "概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("关联板块", out)
        self.assertIn("Technology / Consumer Electronics", out)
        self.assertIn("Technology", out)
        self.assertIn("Consumer Electronics", out)
        # When no sector ranking data is available, drop the 4-col layout.
        self.assertNotIn("板块表现", out)
        self.assertNotIn("板块涨跌幅", out)
        # And no table/type noise either.
        self.assertNotIn("| 板块 | 类型 |", out)
        self.assertNotIn("| -- | -- |", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_without_type_infers_concepts_and_keeps_unknowns(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {"status": "ok", "data": {}},
            "growth": {"status": "ok", "data": {}},
            "belong_boards": [
                {"name": "白酒Ⅲ"},
                {"name": "白酒Ⅱ"},
                {"name": "食品饮料"},
                {"name": "贵州板块"},
                {"name": "酿酒概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("关联板块", out)
        self.assertIn("白酒Ⅲ / 白酒Ⅱ / 食品饮料 / 贵州板块 / 酿酒概念", out)
        self.assertNotIn("| 板块 | 类型 |", out)
        self.assertNotIn("| 白酒Ⅲ | N/A |", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_renders_each_board_signal_without_placeholder(
        self, mock_get_config: mock.MagicMock
    ):
        """Rows without a matching change_pct stay as plain board entries."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {"status": "ok", "data": {}},
            "growth": {"status": "ok", "data": {}},
            "boards": {"status": "ok", "data": {
                "top": [{"name": "白酒", "change_pct": 3.42}],
                "bottom": [],
            }},
            "belong_boards": [
                {"name": "白酒", "code": "BK0596", "type": "行业"},
                {"name": "MSCI中国", "code": "BK0805", "type": "概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertNotIn("板块表现", out)
        self.assertNotIn("板块涨跌幅", out)
        self.assertIn("领涨", out)
        self.assertIn("+3.42%", out)
        self.assertIn("- 白酒 (行业板块 领涨 +3.42%)", out)
        self.assertIn("MSCI中国", out)
        self.assertIn("- MSCI中国", out)
        self.assertNotIn("- MSCI中国 (", out)
        self.assertNotIn("| MSCI中国 | 概念 | -- | -- |", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_ignores_matching_signal_without_change_pct(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {"status": "ok", "data": {}},
            "growth": {"status": "ok", "data": {}},
            "boards": {"status": "ok", "data": {
                "top": [{"name": "白酒"}],
                "bottom": [],
            }},
            "belong_boards": [
                {"name": "白酒", "code": "BK0596", "type": "行业"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("关联板块", out)
        self.assertIn("白酒", out)
        self.assertNotIn("| 白酒 | 行业 |", out)
        self.assertNotIn("领涨", out)
        self.assertNotIn("板块涨跌幅", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_uses_currency_for_hk(
        self, mock_get_config: mock.MagicMock
    ):
        """HK ADRs have financialCurrency=CNY but trade/pay dividends in HKD.

        The financial summary must render in 元 (CNY income statement) while
        dividends must render in 港元 — they are NOT the same currency on
        yfinance HK payloads, so the renderer must read each block's own
        ``currency`` field rather than assuming a single global currency.
        """
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="HK09988",
            name="阿里巴巴-W",
            sentiment_score=68,
            trend_prediction="看多",
            operation_advice="逢低买入",
            analysis_summary="云业务回正，回购持续。",
        )
        result.fundamental_context = {
            "earnings": {
                "status": "ok",
                "data": {
                    "financial_report": {
                        "report_date": "2026-03-31",
                        "revenue": 1.02e12,
                        "net_profit_parent": 1.04e11,
                        "operating_cash_flow": 3.6e10,
                        "roe": 9.22,
                        "currency": "CNY",
                    },
                    "dividend": {
                        "events": [{
                            "event_date": "2025-06-11",
                            "ex_dividend_date": "2025-06-11",
                            "cash_dividend_per_share": 1.95812,
                            "is_pre_tax": True,
                        }],
                        "ttm_event_count": 1,
                        "ttm_cash_dividend_per_share": 1.95812,
                        "ttm_dividend_yield_pct": 1.75,
                        "currency": "HKD",
                    },
                },
            },
            "growth": {"status": "ok", "data": {"revenue_yoy": 2.9, "roe": 9.22, "gross_margin": 39.81}},
            "belong_boards": [
                {"name": "Consumer Cyclical", "type": "行业"},
                {"name": "Internet Retail", "type": "概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        # Income statement still rendered in CNY (financialCurrency).
        self.assertIn("10200.00 亿元", out)
        # Dividend per share follows the dividend currency, NOT the financial currency.
        self.assertIn("1.9581 港元", out)
        self.assertNotIn("1.9581 元 ", out)
        self.assertIn("Consumer Cyclical", out)

    @mock.patch("src.notification.get_config")
    def test_dividend_currency_falls_back_to_financial_when_missing(
        self, mock_get_config: mock.MagicMock
    ):
        """A-share AkShare payload has no dividend.currency — fall back to financial_report.currency."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {
                "status": "ok",
                "data": {
                    "financial_report": {"report_date": "2026-03-31", "revenue": 4.5e10},
                    "dividend": {
                        "events": [{"event_date": "2025-06-20", "ex_dividend_date": "2025-06-20"}],
                        "ttm_event_count": 1,
                        "ttm_cash_dividend_per_share": 27.6,
                    },
                },
            },
        }

        out = service.generate_single_stock_report(result)

        # Without explicit dividend currency, default to 元 (matches AkShare A-share semantics).
        self.assertIn("27.6000 元", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_appends_fundamental_blocks(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            dashboard={
                "core_conclusion": {"one_sentence": "稳健持有"},
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "1600",
                        "stop_loss": "1500",
                        "take_profit": "1800",
                    }
                },
            },
        )
        result.fundamental_context = self._make_fundamental_context()

        out = service.generate_dashboard_report([result], report_date="2026-05-20")

        self.assertIn("财务摘要", out)
        self.assertIn("股东回报", out)
        self.assertIn("关联板块", out)
        self.assertIn("白酒", out)
        self.assertIn("领涨", out)

    @mock.patch("src.notification.get_config")
    def test_history_compare_context_uses_cache(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_history_compare_n=3)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            query_id="q-1",
        )

        with mock.patch(
            "src.services.history_comparison_service.get_signal_changes_batch",
            return_value={"600519": []},
        ) as mock_batch:
            first = service._get_history_compare_context([result])
            second = service._get_history_compare_context([result])

        self.assertEqual(first, {"history_by_code": {"600519": []}})
        self.assertEqual(second, {"history_by_code": {"600519": []}})
        mock_batch.assert_called_once()


if __name__ == "__main__":
    unittest.main()

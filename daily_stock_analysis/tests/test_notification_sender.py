# -*- coding: utf-8 -*-
"""
Unit tests for src.notification_sender module.

Tests sender classes in isolation (config, request shape, error handling).
Does not duplicate test_notification.py which tests NotificationService.send() flow.

P5：仅保留飞书 sender，其余 13 个 sender 已移除。
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import Config
from src.notification_sender import FeishuSender


def _config(**overrides):
    """Minimal Config for sender tests."""
    return Config(stock_list=[], **overrides)


def _response(status_code: int, json_body: Optional[dict] = None):
    resp = mock.MagicMock()
    resp.status_code = status_code
    if status_code == 200:
        resp.text = "ok"
    else:
        resp.text = "error"
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


def _sdk_response(success: bool, *, code: int = 0, msg: str = "ok", log_id: str = "log-id"):
    resp = mock.MagicMock()
    resp.success.return_value = success
    resp.code = code
    resp.msg = msg
    resp.get_log_id.return_value = log_id
    return resp


def _fake_feishu_client(*side_effects):
    create = mock.Mock()
    if side_effects:
        create.side_effect = list(side_effects)
    client = SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(
                message=SimpleNamespace(create=create)
            )
        )
    )
    return client, create


class TestFeishuSender(unittest.TestCase):
    """Unit tests for FeishuSender."""

    def test_send_returns_false_when_no_webhook_url(self):
        cfg = _config()
        sender = FeishuSender(cfg)
        result = sender.send_to_feishu("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"code": 0})
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)
        result = sender.send_to_feishu("hello")
        self.assertTrue(result)

    @mock.patch.object(FeishuSender, "_send_via_webhook", return_value=True)
    def test_send_strips_hidden_market_region_metadata_before_routing(self, mock_send):
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)

        result = sender.send_to_feishu("[dsa-market-region]: # (us)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(result)
        mock_send.assert_called_once_with("# 🎯 Market Review\n\nBody", timeout_seconds=None)

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_http_error_returns_false(self, mock_post):
        mock_post.return_value = _response(400)
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)
        result = sender.send_to_feishu("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.feishu_sender.time.time", return_value=1700000000)
    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_with_secret_and_keyword_builds_signed_payload(self, mock_post, _mock_time):
        mock_post.return_value = _response(200, {"code": 0})
        cfg = _config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_webhook_secret="secret-token",
            feishu_webhook_keyword="股票日报",
        )
        sender = FeishuSender(cfg)

        result = sender.send_to_feishu("hello")

        self.assertTrue(result)
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["timestamp"], "1700000000")
        expected_sign = base64.b64encode(
            hmac.new(
                b"1700000000\nsecret-token",
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        self.assertEqual(payload["sign"], expected_sign)
        self.assertEqual(
            payload["card"]["elements"][0]["text"]["content"],
            "股票日报\nhello",
        )

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_uses_legacy_feishu_report_formatter(self, mock_post):
        mock_post.return_value = _response(200, {"code": 0})
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)
        content = (
            "# 日报\n\n"
            "## 📊 分析结果摘要\n\n"
            "| 股票 | 信号 |\n"
            "| --- | --- |\n"
            "| 600519 | 强势 |\n\n"
            "[详情](https://example.com/report)"
        )

        result = sender.send_to_feishu(content)

        self.assertTrue(result)
        payload = mock_post.call_args.kwargs["json"]
        rendered = payload["card"]["elements"][0]["text"]["content"]
        self.assertIn("**日报**", rendered)
        self.assertIn("**📊 分析结果摘要**", rendered)
        self.assertIn("• 股票：600519 | 信号：强势", rendered)
        self.assertIn("[详情](https://example.com/report)", rendered)
        self.assertNotIn("| --- |", rendered)

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_error_response_returns_false(self, mock_post):
        mock_post.return_value = _response(200, {"code": 19024, "msg": "keyword not found"})
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)

        result = sender.send_to_feishu("hello")

        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, 2)

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_with_keyword_that_leaves_too_little_chunk_budget_returns_false(self, mock_post):
        cfg = _config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_webhook_keyword="abcd",
            feishu_max_bytes=60,
        )
        sender = FeishuSender(cfg)

        result = sender.send_to_feishu("x" * 100)

        self.assertFalse(result)
        mock_post.assert_not_called()

    # ------------------------------------------------------------------
    # App Bot mode tests
    # ------------------------------------------------------------------

    def test_app_bot_returns_false_when_no_app_credentials(self):
        """send_to_feishu returns False when app credentials are missing."""
        cfg = _config(feishu_chat_id="oc_chat")
        sender = FeishuSender(cfg)
        self.assertFalse(sender.send_to_feishu("hello"))

    def test_app_bot_returns_false_when_no_chat_id(self):
        """send_to_feishu returns False when feishu_chat_id is missing."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
        )
        sender = FeishuSender(cfg)
        self.assertFalse(sender.send_to_feishu("hello"))

    def test_app_bot_success_via_card(self):
        """send_to_feishu sends an interactive card via App Bot on success."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        dummy_client = object()
        with mock.patch.object(FeishuSender, "_ensure_app_client", return_value=dummy_client), \
             mock.patch.object(FeishuSender, "_app_send_raw", return_value=True) as mock_raw:
            result = sender.send_to_feishu("**hello** world")

        self.assertTrue(result)
        mock_raw.assert_called_once()
        self.assertIs(mock_raw.call_args[0][0], dummy_client)
        # call_args[0] = (client, msg_type, content_json)
        msg_type = mock_raw.call_args[0][1]
        content_json = mock_raw.call_args[0][2]
        self.assertEqual(msg_type, "interactive")
        self.assertIn("**hello**", content_json)

    def test_app_bot_card_fallback_to_text_on_formatted_content(self):
        """App Bot falls back to text when interactive card fails."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        with mock.patch.object(FeishuSender, "_ensure_app_client", return_value=object()), \
             mock.patch.object(FeishuSender, "_app_send_raw", side_effect=[False, True]) as mock_raw:
            result = sender.send_to_feishu("hello world")

        self.assertTrue(result)
        self.assertEqual(mock_raw.call_count, 2)
        # call_args_list[0][0] = (client, msg_type, content_json)
        self.assertEqual(mock_raw.call_args_list[0][0][1], "interactive")
        self.assertEqual(mock_raw.call_args_list[1][0][1], "text")

    def test_app_bot_card_first_success_no_fallback(self):
        """App Bot sends interactive card successfully and does not try text."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        with mock.patch.object(FeishuSender, "_ensure_app_client", return_value=object()), \
             mock.patch.object(FeishuSender, "_app_send_raw", return_value=True) as mock_raw:
            result = sender.send_to_feishu("**bold** text")

        self.assertTrue(result)
        mock_raw.assert_called_once()
        # call_args_list[0][0][1] = msg_type, [0][0][2] = content_json
        self.assertEqual(mock_raw.call_args_list[0][0][1], "interactive")
        self.assertIn("**bold**", mock_raw.call_args_list[0][0][2])

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    @mock.patch.object(FeishuSender, "_app_send_raw", return_value=True)
    def test_webhook_takes_precedence_over_app_bot(self, mock_app_raw, mock_webhook_post):
        """When both webhook URL and App Bot credentials are configured, webhook is used."""
        mock_webhook_post.return_value = _response(200, {"code": 0})
        cfg = _config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        result = sender.send_to_feishu("hello")

        self.assertTrue(result)
        mock_webhook_post.assert_called_once()
        mock_app_raw.assert_not_called()

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_webhook_does_not_require_sdk_when_app_bot_is_also_configured(self, mock_webhook_post):
        """Webhook precedence keeps SDK absence from breaking existing delivery."""
        mock_webhook_post.return_value = _response(200, {"code": 0})
        cfg = _config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)

        with mock.patch("src.notification_sender.feishu_sender.FEISHU_SDK_AVAILABLE", False), \
             mock.patch.object(FeishuSender, "_ensure_app_client", side_effect=AssertionError("SDK should not be used")):
            result = sender.send_to_feishu("hello")

        self.assertTrue(result)
        mock_webhook_post.assert_called_once()

    def test_app_bot_missing_sdk_logs_standard_requirements_install(self):
        """App Bot SDK absence fails closed with the standard project install hint."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)

        with mock.patch("src.notification_sender.feishu_sender.FEISHU_SDK_AVAILABLE", False), \
             self.assertLogs("src.notification_sender.feishu_sender", level="WARNING") as logs:
            result = sender.send_to_feishu("hello")

        self.assertFalse(result)
        install_hints = [
            line
            for line in logs.output
            if "pip install -r requirements.txt" in line
        ]
        self.assertEqual(install_hints, logs.output)
        self.assertEqual(len(install_hints), 1)

    def test_app_bot_chunking_long_content(self):
        """Long content is chunked for App Bot."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
            feishu_max_bytes=200,
        )
        sender = FeishuSender(cfg)

        with mock.patch.object(FeishuSender, "_ensure_app_client", return_value=object()), \
             mock.patch.object(FeishuSender, "_app_send_raw", return_value=False) as mock_raw, \
             mock.patch("src.notification_sender.feishu_sender.time.sleep") as mock_sleep:
            result = sender.send_to_feishu("A" * 500)

        self.assertFalse(result)  # All chunks fail
        self.assertGreater(mock_raw.call_count, 1)
        self.assertEqual(
            [item.args[0] for item in mock_sleep.call_args_list],
            [1, 1, 1],
        )

    @mock.patch.object(FeishuSender, "_app_send_raw", return_value=True)
    def test_app_bot_request_shape_interactive(self, mock_raw):
        """_app_send_once constructs interactive card payload with lark_md."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        result = sender._app_send_once(object(), "**bold** text")

        self.assertTrue(result)
        call = mock_raw.call_args
        self.assertEqual(call[0][1], "interactive")  # msg_type
        card = json.loads(call[0][2])
        self.assertEqual(card["header"]["title"]["content"], "股票智能分析报告")
        self.assertEqual(card["elements"][0]["text"]["tag"], "lark_md")
        self.assertIn("**bold**", card["elements"][0]["text"]["content"])

    @mock.patch.object(FeishuSender, "_app_send_raw")
    def test_app_bot_request_shape_text_fallback(self, mock_raw):
        """_app_send_once falls back to text payload when card fails."""
        mock_raw.side_effect = [False, True]
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        result = sender._app_send_once(object(), "plain text")

        self.assertTrue(result)
        self.assertEqual(mock_raw.call_count, 2)
        # Second call is text fallback
        second_call = mock_raw.call_args_list[1]
        self.assertEqual(second_call[0][1], "text")
        text_content = json.loads(second_call[0][2])
        self.assertIn("plain text", text_content["text"])

    @mock.patch("src.notification_sender.feishu_sender.uuid_mod.uuid4", return_value="uuid-open-id")
    def test_app_bot_request_includes_receive_id_type(self, _mock_uuid4):
        """_app_send_raw request builder passes receive_id_type and request body fields."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="ou_user",
            feishu_receive_id_type="open_id",
        )
        sender = FeishuSender(cfg)
        client, create = _fake_feishu_client(_sdk_response(True))

        result = sender._app_send_raw(client, "text", json.dumps({"text": "hi"}))

        self.assertTrue(result)
        create.assert_called_once()
        req = create.call_args[0][0]
        self.assertEqual(req.receive_id_type, "open_id")
        self.assertEqual(req.request_body.receive_id, "ou_user")
        self.assertEqual(req.request_body.msg_type, "text")
        self.assertEqual(json.loads(req.request_body.content), {"text": "hi"})
        self.assertEqual(req.request_body.uuid, "uuid-open-id")

    @mock.patch("src.notification_sender.feishu_sender.uuid_mod.uuid4")
    def test_app_bot_idempotency_uuid_per_call(self, mock_uuid4):
        """Each _app_send_raw invocation gets a fresh UUID."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        client, create = _fake_feishu_client(_sdk_response(True), _sdk_response(True))

        mock_uuid4.side_effect = ["aaaa-bbbb-cccc", "dddd-eeee-ffff"]
        sender._app_send_raw(client, "text", json.dumps({"text": "a"}))
        sender._app_send_raw(client, "text", json.dumps({"text": "b"}))

        self.assertEqual(create.call_count, 2)
        call1_req = create.call_args_list[0][0][0]
        call2_req = create.call_args_list[1][0][0]
        self.assertEqual(call1_req.request_body.uuid, "aaaa-bbbb-cccc")
        self.assertEqual(call2_req.request_body.uuid, "dddd-eeee-ffff")

    @mock.patch("src.notification_sender.feishu_sender.time.sleep")
    def test_app_bot_retries_sdk_response_failure(self, mock_sleep):
        """_app_send_raw retries failed SDK responses and stops after success."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        client, create = _fake_feishu_client(
            _sdk_response(False, code=999, msg="temporary"),
            _sdk_response(True),
        )

        result = sender._app_send_raw(client, "text", json.dumps({"text": "retry"}))

        self.assertTrue(result)
        self.assertEqual(create.call_count, 2)
        mock_sleep.assert_called_once()

    @mock.patch("src.notification_sender.feishu_sender.time.sleep")
    def test_app_bot_retries_sdk_exception(self, mock_sleep):
        """_app_send_raw retries exceptions raised by the SDK create call."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        client, create = _fake_feishu_client(RuntimeError("network"), _sdk_response(True))

        result = sender._app_send_raw(client, "text", json.dumps({"text": "retry"}))

        self.assertTrue(result)
        self.assertEqual(create.call_count, 2)
        mock_sleep.assert_called_once()

    @mock.patch("src.notification_sender.feishu_sender.time.sleep")
    def test_app_bot_first_success_does_not_retry(self, mock_sleep):
        """_app_send_raw does not retry after the first successful SDK response."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        client, create = _fake_feishu_client(_sdk_response(True))

        result = sender._app_send_raw(client, "text", json.dumps({"text": "once"}))

        self.assertTrue(result)
        create.assert_called_once()
        mock_sleep.assert_not_called()

    @mock.patch("src.notification_sender.feishu_sender.time.sleep")
    @mock.patch("src.notification_sender.feishu_sender.CreateMessageRequest.builder", side_effect=RuntimeError("bad builder"))
    def test_app_bot_builder_failure_does_not_retry(self, _mock_builder, mock_sleep):
        """Request builder failures are not treated as transient send failures."""
        cfg = _config(
            feishu_app_id="cli_app",
            feishu_app_secret="secret",
            feishu_chat_id="oc_chat",
        )
        sender = FeishuSender(cfg)
        client, create = _fake_feishu_client(_sdk_response(True))

        result = sender._app_send_raw(client, "text", json.dumps({"text": "bad"}))

        self.assertFalse(result)
        create.assert_not_called()
        mock_sleep.assert_not_called()

    # ------------------------------------------------------------------
    # send_feishu_file tests
    # ------------------------------------------------------------------

    @mock.patch("src.notification_sender.feishu_sender.Path")
    def test_send_feishu_file_returns_false_when_file_not_found(self, mock_path_cls):
        """send_feishu_file returns False when the file does not exist."""
        mock_path = mock_path_cls.return_value
        mock_path.is_file.return_value = False
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)
        result = sender.send_feishu_file("/nonexistent/report.md")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.feishu_sender.Path")
    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_feishu_file_webhook_reads_file_and_sends_content(self, mock_post, mock_path_cls):
        """Webhook fallback reads the file and sends its content as a message."""
        mock_post.return_value = _response(200, {"code": 0})
        mock_path = mock_path_cls.return_value
        mock_path.is_file.return_value = True
        mock_path.name = "report_20260705.md"
        mock_path.suffix = ".md"
        mock_path.read_text.return_value = "# Test Report\n\nHello"
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)
        result = sender.send_feishu_file("/tmp/report_20260705.md")
        self.assertTrue(result)
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        rendered = payload["card"]["elements"][0]["text"]["content"]
        self.assertIn("📄 Markdown 文件内容: report_20260705.md", rendered)
        self.assertIn("**Test Report**", rendered)

    @mock.patch("src.notification_sender.feishu_sender.Path")
    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_feishu_file_webhook_strips_hidden_metadata_from_file_content(self, mock_post, mock_path_cls):
        mock_post.return_value = _response(200, {"code": 0})
        mock_path = mock_path_cls.return_value
        mock_path.is_file.return_value = True
        mock_path.name = "report.md"
        mock_path.suffix = ".md"
        mock_path.read_text.return_value = "[dsa-market-region]: # (cn)\n\n# Test Report\n\nHello"
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)

        result = sender.send_feishu_file("/tmp/report.md")

        self.assertTrue(result)
        rendered = mock_post.call_args.kwargs["json"]["card"]["elements"][0]["text"]["content"]
        self.assertNotIn("[dsa-market-region]", rendered)
        self.assertIn("**Test Report**", rendered)

    @mock.patch("src.notification_sender.feishu_sender.Path")
    def test_send_feishu_file_webhook_unreadable_file_returns_false(self, mock_path_cls):
        """Webhook fallback returns False when the file cannot be read."""
        mock_path = mock_path_cls.return_value
        mock_path.is_file.return_value = True
        mock_path.read_text.side_effect = OSError("Permission denied")
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)
        result = sender.send_feishu_file("/tmp/protected.md")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.feishu_sender.Path")
    def test_send_feishu_file_app_bot_missing_chat_id_returns_false(self, mock_path_cls):
        """App Bot mode returns False when chat_id is not configured."""
        mock_path = mock_path_cls.return_value
        mock_path.is_file.return_value = True
        cfg = _config(feishu_app_id="cli_app", feishu_app_secret="secret")
        sender = FeishuSender(cfg)
        result = sender.send_feishu_file("/tmp/report.md")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.feishu_sender._CreateFileRequestBody", create=True)
    @mock.patch("src.notification_sender.feishu_sender._CreateFileRequest", create=True)
    @mock.patch("src.notification_sender.feishu_sender.FEISHU_FILE_SDK_AVAILABLE", True)
    @mock.patch("src.notification_sender.feishu_sender.Path")
    def test_send_feishu_file_app_bot_uploads_file_and_sends_message(self, *_):
        """App Bot uploads the file via SDK and sends a file message."""
        mock_path = mock.MagicMock()
        mock_path.is_file.return_value = True
        mock_path.name = "report.md"
        mock_path.suffix = ".md"
        mock_path.open.return_value.__enter__.return_value = mock.MagicMock()
        with mock.patch("src.notification_sender.feishu_sender.Path", return_value=mock_path):
            cfg = _config(
                feishu_app_id="cli_app",
                feishu_app_secret="secret",
                feishu_chat_id="oc_chat",
            )
            sender = FeishuSender(cfg)
            dummy_client = mock.MagicMock()
            file_resp = mock.MagicMock()
            file_resp.success.return_value = True
            file_resp.code = 0
            file_resp.msg = "ok"
            file_resp.data.file_key = "file_key_abc"
            dummy_client.im.v1.file.create.return_value = file_resp
            with mock.patch.object(FeishuSender, "_ensure_app_client", return_value=dummy_client), \
                 mock.patch.object(FeishuSender, "_app_send_raw", return_value=True) as mock_raw:
                result = sender.send_feishu_file("/tmp/report.md")
        self.assertTrue(result)
        dummy_client.im.v1.file.create.assert_called_once()
        mock_raw.assert_called_once()
        self.assertEqual(mock_raw.call_args[0][1], "file")
        content_json = json.loads(mock_raw.call_args[0][2])
        self.assertEqual(content_json["file_key"], "file_key_abc")

    @mock.patch("src.notification_sender.feishu_sender._CreateFileRequestBody", create=True)
    @mock.patch("src.notification_sender.feishu_sender._CreateFileRequest", create=True)
    @mock.patch("src.notification_sender.feishu_sender.FEISHU_FILE_SDK_AVAILABLE", True)
    @mock.patch("src.notification_sender.feishu_sender.tempfile.NamedTemporaryFile")
    def test_send_feishu_file_app_bot_strips_hidden_metadata_before_upload(self, mock_tmpfile, *_):
        mock_path = mock.MagicMock()
        mock_path.is_file.return_value = True
        mock_path.name = "report.md"
        mock_path.suffix = ".md"
        mock_path.read_text.return_value = "[dsa-market-region]: # (cn)\n\n# Test Report\n\nHello"

        temp_handle = mock.MagicMock()
        temp_handle.name = "/tmp/feishu_sanitized.md"
        temp_handle.__enter__.return_value = temp_handle
        temp_handle.__exit__.return_value = None
        mock_tmpfile.return_value = temp_handle

        sanitized_path = mock.MagicMock()
        sanitized_path.name = "feishu_sanitized.md"
        sanitized_path.suffix = ".md"
        sanitized_path.open.return_value.__enter__.return_value = mock.MagicMock()
        sanitized_path.unlink.return_value = None

        with mock.patch("src.notification_sender.feishu_sender.Path", side_effect=[mock_path, sanitized_path]):
            cfg = _config(
                feishu_app_id="cli_app",
                feishu_app_secret="secret",
                feishu_chat_id="oc_chat",
            )
            sender = FeishuSender(cfg)
            dummy_client = mock.MagicMock()
            file_resp = mock.MagicMock()
            file_resp.success.return_value = True
            file_resp.code = 0
            file_resp.msg = "ok"
            file_resp.data.file_key = "file_key_abc"
            dummy_client.im.v1.file.create.return_value = file_resp
            with mock.patch.object(FeishuSender, "_ensure_app_client", return_value=dummy_client), \
                 mock.patch.object(FeishuSender, "_app_send_raw", return_value=True):
                result = sender.send_feishu_file("/tmp/report.md")

        self.assertTrue(result)
        temp_handle.write.assert_called_once()
        written_content = temp_handle.write.call_args.args[0]
        self.assertNotIn("[dsa-market-region]", written_content)
        self.assertIn("# Test Report", written_content)
        sanitized_path.open.assert_called_once()
        sanitized_path.unlink.assert_called_once()

    @mock.patch("src.notification_sender.feishu_sender._CreateFileRequestBody", create=True)
    @mock.patch("src.notification_sender.feishu_sender._CreateFileRequest", create=True)
    @mock.patch("src.notification_sender.feishu_sender.FEISHU_FILE_SDK_AVAILABLE", True)
    def test_send_feishu_file_app_bot_upload_failure_returns_false(self, *_):
        """App Bot returns False when the file upload fails."""
        mock_path = mock.MagicMock()
        mock_path.is_file.return_value = True
        mock_path.name = "report.md"
        mock_path.suffix = ".md"
        mock_path.open.return_value.__enter__.return_value = mock.MagicMock()
        with mock.patch("src.notification_sender.feishu_sender.Path", return_value=mock_path):
            cfg = _config(
                feishu_app_id="cli_app",
                feishu_app_secret="secret",
                feishu_chat_id="oc_chat",
            )
            sender = FeishuSender(cfg)
            dummy_client = mock.MagicMock()
            file_resp = mock.MagicMock()
            file_resp.success.return_value = False
            file_resp.code = 999
            file_resp.msg = "upload failed"
            dummy_client.im.v1.file.create.return_value = file_resp
            with mock.patch.object(FeishuSender, "_ensure_app_client", return_value=dummy_client):
                result = sender.send_feishu_file("/tmp/report.md")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.feishu_sender._CreateFileRequestBody", create=True)
    @mock.patch("src.notification_sender.feishu_sender._CreateFileRequest", create=True)
    @mock.patch("src.notification_sender.feishu_sender.FEISHU_FILE_SDK_AVAILABLE", True)
    def test_send_feishu_file_app_bot_missing_file_key_returns_false(self, *_):
        """App Bot returns False when upload succeeds but file_key is missing."""
        mock_path = mock.MagicMock()
        mock_path.is_file.return_value = True
        mock_path.name = "report.md"
        mock_path.suffix = ".md"
        mock_path.open.return_value.__enter__.return_value = mock.MagicMock()
        with mock.patch("src.notification_sender.feishu_sender.Path", return_value=mock_path):
            cfg = _config(
                feishu_app_id="cli_app",
                feishu_app_secret="secret",
                feishu_chat_id="oc_chat",
            )
            sender = FeishuSender(cfg)
            dummy_client = mock.MagicMock()
            file_resp = mock.MagicMock()
            file_resp.success.return_value = True
            file_resp.data = None
            dummy_client.im.v1.file.create.return_value = file_resp
            with mock.patch.object(FeishuSender, "_ensure_app_client", return_value=dummy_client):
                result = sender.send_feishu_file("/tmp/report.md")
        self.assertFalse(result)

    def test_file_upload_sdk_classes_exist_non_mock(self):
        """Smoke: lark-oapi SDK actually exports CreateFileRequest/RequestBody."""
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody
        self.assertTrue(hasattr(CreateFileRequest, 'builder'))
        self.assertTrue(hasattr(CreateFileRequestBody, 'builder'))

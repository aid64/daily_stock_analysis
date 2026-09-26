# -*- coding: utf-8 -*-
"""U1 漏斗第一步：发送侧 message_id 留存（feishu_sender dispatch ledger）"""

import json
from unittest import mock

from src.notification_sender import feishu_sender
from src.notification_sender.feishu_sender import (
    FeishuSender,
    _dispatch_log_path,
)


def _config(**overrides):
    from src.config import Config
    return Config(stock_list=[], **overrides)


def _sdk_response(success: bool, message_id=None):
    resp = mock.MagicMock()
    resp.success.return_value = success
    resp.code = 0 if success else 230001
    resp.msg = "ok" if success else "err"
    resp.get_log_id.return_value = "log-id"
    if message_id is not None:
        resp.data = mock.MagicMock()
        resp.data.message_id = message_id
    else:
        resp.data = None
    return resp


def _fake_client(resp):
    create = mock.Mock(return_value=resp)
    client = mock.MagicMock()
    client.im.v1.message.create = create
    return client


def _sender(tmp_path, monkeypatch):
    monkeypatch.setattr(
        feishu_sender, "_dispatch_log_path",
        lambda: tmp_path / "feishu_dispatch.jsonl")
    sender = FeishuSender(_config(
        feishu_app_id="cli_test", feishu_app_secret="secret",
        feishu_chat_id="oc_test"))
    return sender


def _rows(tmp_path):
    path = tmp_path / "feishu_dispatch.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(
        encoding="utf-8").splitlines() if l.strip()]


class TestDispatchLedger:
    def test_success_records_message_id_and_logical_id(self, tmp_path, monkeypatch):
        sender = _sender(tmp_path, monkeypatch)
        sender._app_send_raw(
            _fake_client(_sdk_response(True, message_id="om_123")),
            "interactive", '{"config":{}}', logical_id="daily_operation_sheet:2026-09-26")
        rows = _rows(tmp_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["message_id"] == "om_123"
        assert row["logical_id"] == "daily_operation_sheet:2026-09-26"
        assert row["msg_type"] == "interactive"
        assert row["source"] == "app_bot"
        assert len(row["content_sha16"]) == 16
        assert "T" in row["at"]

    def test_success_without_message_id_still_records(self, tmp_path, monkeypatch):
        sender = _sender(tmp_path, monkeypatch)
        sender._app_send_raw(
            _fake_client(_sdk_response(True)), "text", '{"text":"x"}',
            logical_id=None)
        rows = _rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["message_id"] is None
        assert rows[0]["logical_id"] is None
        assert len(rows[0]["content_sha16"]) == 16

    def test_failure_records_nothing(self, tmp_path, monkeypatch):
        sender = _sender(tmp_path, monkeypatch)
        sender._app_send_raw(
            _fake_client(_sdk_response(False)), "text", '{"text":"x"}',
            logical_id="event_alert:000899:price_down")
        assert _rows(tmp_path) == []

    def test_send_to_feishu_threads_logical_id(self, tmp_path, monkeypatch):
        sender = _sender(tmp_path, monkeypatch)
        seen = {}

        def fake_app_send(content, *, logical_id=None):
            seen["logical_id"] = logical_id
            return True

        with mock.patch.object(sender, "_send_via_app_bot",
                               side_effect=fake_app_send):
            sender.send_to_feishu("hello", logical_id="daily_operation_sheet:d1")
        assert seen["logical_id"] == "daily_operation_sheet:d1"

    def test_chunked_send_records_per_chunk(self, tmp_path, monkeypatch):
        sender = _sender(tmp_path, monkeypatch)
        sender._feishu_max_bytes = 200  # 90 个 CJK 字符 ≈ 270 字节 → 2 片
        fake_client = _fake_client(_sdk_response(True, message_id="om_chunk"))
        with mock.patch.object(sender, "_ensure_app_client",
                               return_value=fake_client):
            sender._send_via_app_bot("长" * 90, logical_id="daily_operation_sheet:d2")
        rows = _rows(tmp_path)
        assert len(rows) >= 2
        assert all(r["logical_id"] == "daily_operation_sheet:d2" for r in rows)

    def test_webhook_path_records_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            feishu_sender, "_dispatch_log_path",
            lambda: tmp_path / "feishu_dispatch.jsonl")
        sender = FeishuSender(_config(
            feishu_webhook_url="https://feishu.example/hook"))
        with mock.patch.object(feishu_sender.requests, "post") as post:
            post.return_value = mock.MagicMock(status_code=200, text="ok")
            sender.send_to_feishu("hello", logical_id="daily_operation_sheet:d3")
        assert _rows(tmp_path) == []

    def test_ledger_write_failure_never_breaks_send(self, tmp_path, monkeypatch):
        sender = _sender(tmp_path, monkeypatch)
        # 让台账路径的父目录是一个"文件"→ mkdir 抛错 → _record_dispatch
        # 内部守卫吞掉（留痕是旁路），发送结果不受影响。
        blocker = tmp_path / "blocked"
        blocker.write_text("not a dir", encoding="utf-8")
        monkeypatch.setattr(
            feishu_sender, "_dispatch_log_path",
            lambda: blocker / "feishu_dispatch.jsonl")
        ok = sender._app_send_raw(
            _fake_client(_sdk_response(True, message_id="om_x")),
            "text", '{"text":"x"}', logical_id="L")
        assert ok is True

    def test_dispatch_log_path_is_data_dir(self):
        path = _dispatch_log_path()
        assert path.name == "feishu_dispatch.jsonl"
        assert path.parent.name == "data"

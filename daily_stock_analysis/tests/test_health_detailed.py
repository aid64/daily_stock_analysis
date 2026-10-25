# -*- coding: utf-8 -*-
"""Regression coverage for the detailed health endpoint contract."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from api.v1.endpoints import health as health_module


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        return None


class _FakeDatabaseManager:
    def get_session(self):
        return _FakeSession()


def _feishu_config():
    return SimpleNamespace(
        stock_list=["600519"],
        llm_channels=[{"name": "primary"}],
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
    )


def test_health_detailed_reports_ok_when_all_checks_pass():
    with (
        patch.object(health_module, "get_config", return_value=_feishu_config()),
        patch("src.storage.DatabaseManager.get_instance", return_value=_FakeDatabaseManager()),
        patch(
            "src.services.system_config_service.SystemConfigService.get_generation_backend_status",
            return_value={
                "primary": {"available": True, "backend_id": "litellm"},
                "fallback": None,
            },
        ),
    ):
        result = asyncio.run(health_module.health_check_detailed())

    assert result["status"] == "ok"
    assert {
        name: component["status"]
        for name, component in result["components"].items()
    } == {
        "config": "ok",
        "database": "ok",
        "llm_backend": "ok",
        "notifications": "ok",
        "data_source": "ok",
    }


def test_health_detailed_degrades_on_database_failure():
    class _BrokenDatabaseManager:
        def get_session(self):
            raise RuntimeError("db down")

    with (
        patch.object(health_module, "get_config", return_value=_feishu_config()),
        patch("src.storage.DatabaseManager.get_instance", return_value=_BrokenDatabaseManager()),
        patch(
            "src.services.system_config_service.SystemConfigService.get_generation_backend_status",
            return_value={
                "primary": {"available": True, "backend_id": "litellm"},
                "fallback": None,
            },
        ),
    ):
        result = asyncio.run(health_module.health_check_detailed())

    assert result["status"] == "degraded"
    assert result["components"]["database"]["status"] == "error"

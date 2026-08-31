# -*- coding: utf-8 -*-
"""诊断探针：镜像 SystemConfigServiceTestCase 的隔离方式，检测跨文件泄漏。

用法：py -m pytest tests/zz_probe_effective_map.py（置于任意前缀后运行）。
失败时输出 effective_map 中泄漏的 LITELLM_MODEL 实际值。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.config import Config
from src.core.config_manager import ConfigManager
from src.services.system_config_service import SystemConfigService


def test_probe_effective_map_is_clean():
    temp_dir = tempfile.mkdtemp()
    env_path = Path(temp_dir) / ".env"
    env_path.write_text("STOCK_LIST=600519,000001\n", encoding="utf-8")
    os.environ["ENV_FILE"] = str(env_path)
    Config.reset_instance()
    manager = ConfigManager(env_path=env_path)
    service = SystemConfigService(manager=manager)
    result = service.validate(
        items=[
            {"key": "LLM_CHANNELS", "value": "anspire"},
            {"key": "LLM_ANSPIRE_ENABLED", "value": "   "},
            {"key": "ANSPIRE_LLM_ENABLED", "value": "false"},
        ]
    )
    if result["issues"]:
        effective = service._build_setup_effective_config_map()
        leaked = {
            k: effective.get(k)
            for k in ("LITELLM_MODEL", "LITELLM_FALLBACK_MODELS", "LLM_CHANNELS",
                      "LLM_PRIMARY_MODELS", "LLM_PRIMARY_ENABLED", "DEEPSEEK_API_KEY")
        }
        raise AssertionError(f"leaked state: {result['issues']}\nmap: {leaked}")

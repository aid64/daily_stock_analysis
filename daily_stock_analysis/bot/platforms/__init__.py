# -*- coding: utf-8 -*-
"""
===================================
平台适配器模块
===================================

包含各平台的 Webhook 处理和消息解析逻辑。

P5：仅保留飞书（生产唯一使用平台），钉钉 Webhook/Stream 已移除。
飞书通过 Stream 模式接入（无需公网 IP，WebSocket 长连接）。
"""

from bot.platforms.base import BotPlatform

# 所有可用平台（Webhook 模式）。P5 后仅飞书 Stream 接入，Webhook 平台表为空。
ALL_PLATFORMS = {}

# 飞书 Stream 模式（可选）
try:
    from bot.platforms.feishu_stream import (
        FeishuStreamClient,
        FeishuStreamHandler,
        FeishuReplyClient,
        get_feishu_stream_client,
        start_feishu_stream_background,
        FEISHU_SDK_AVAILABLE,
    )
except ImportError:
    FEISHU_SDK_AVAILABLE = False
    FeishuStreamClient = None
    FeishuStreamHandler = None
    FeishuReplyClient = None
    get_feishu_stream_client = lambda: None
    start_feishu_stream_background = lambda: False

__all__ = [
    'BotPlatform',
    'ALL_PLATFORMS',
    # 飞书 Stream 模式
    'FeishuStreamClient',
    'FeishuStreamHandler',
    'FeishuReplyClient',
    'get_feishu_stream_client',
    'start_feishu_stream_background',
    'FEISHU_SDK_AVAILABLE',
]

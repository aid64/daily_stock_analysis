# -*- coding: utf-8 -*-
"""
===================================
机器人消息模型（向后兼容 re-export）
===================================

实际定义已迁移至 :mod:`src.schemas.bot_messages`，以便 src/ 层不再反向依赖 bot/。
本模块保留 re-export，避免破坏现有 ``from bot.models import BotMessage`` 调用方
（包括 bot/ 内部、tests/ 与可能的下游集成）。
"""

from src.schemas.bot_messages import (  # noqa: F401
    ChatType,
    Platform,
    BotMessage,
    BotResponse,
    WebhookResponse,
)

__all__ = [
    "ChatType",
    "Platform",
    "BotMessage",
    "BotResponse",
    "WebhookResponse",
]

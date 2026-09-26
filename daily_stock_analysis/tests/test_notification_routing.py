# -*- coding: utf-8 -*-
"""Tests for notification route channel parsing."""

from src.notification_routing import ROUTABLE_NOTIFICATION_CHANNELS, split_notification_route_channels


def test_feishu_is_routable_and_unknown_channels_are_rejected() -> None:
    valid, invalid = split_notification_route_channels(["feishu", "not-a-channel"])

    assert "feishu" in ROUTABLE_NOTIFICATION_CHANNELS
    assert valid == ["feishu"]
    assert invalid == ["not-a-channel"]

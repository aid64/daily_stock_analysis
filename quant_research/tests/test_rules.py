"""RuleBook（core.rules）单测：品种分类、日期生效规则、兼容接口

所有测试纯离线。
"""
from __future__ import annotations

import datetime

import pytest

from quant_research.core.rules import (
    asset_type,
    get_limit_pct,
    get_rules,
)


class TestAssetType:
    @pytest.mark.parametrize("symbol,expected", [
        ("110076.SH", "cb"),
        ("113050.SH", "cb"),
        ("118031.SH", "cb"),
        ("123145.SZ", "cb"),
        ("128136.SZ", "cb"),
        ("127040.SZ", "cb"),
        ("510300.SH", "etf"),
        ("511010.SH", "etf"),
        ("563300.SH", "etf"),
        ("588000.SH", "etf"),
        ("159915.SZ", "etf"),
        ("600519.SH", "stock"),
        ("000001.SZ", "stock"),
        ("300750.SZ", "stock"),
        ("688981.SH", "stock"),
        ("430047.BJ", "stock"),
    ])
    def test_classification(self, symbol, expected):
        assert asset_type(symbol) == expected


class TestLimitPct:
    def test_main_board(self):
        assert get_limit_pct("600519.SH") == 0.1
        assert get_limit_pct("000001.SZ") == 0.1

    def test_chinext_registration_reform_boundary(self):
        """创业板注册制 2020-08-24 前后 10%/20%（原实现历史失真的修正）"""
        before = datetime.date(2020, 8, 21)
        after = datetime.date(2020, 8, 25)
        assert get_limit_pct("300750.SZ", before) == 0.10
        assert get_limit_pct("300750.SZ", after) == 0.20
        assert get_limit_pct("301088.SZ", after) == 0.20

    def test_star_and_bse(self):
        assert get_limit_pct("688981.SH") == 0.2
        assert get_limit_pct("430047.BJ") == 0.3
        assert get_limit_pct("830799.BJ") == 0.3

    def test_gem_and_star_etf_twenty_percent(self):
        """创业板/科创板 ETF ±20%（原实现按 10% 处理的修正）"""
        assert get_limit_pct("159915.SZ") == 0.20
        assert get_limit_pct("588000.SH") == 0.20
        assert get_limit_pct("510300.SH") == 0.10

    def test_cb_limit_history(self):
        """转债 2022-08-01 前 None（无统一口径），之后 ±20%"""
        assert get_limit_pct("113050.SH", datetime.date(2022, 7, 29)) is None
        assert get_limit_pct("113050.SH", datetime.date(2022, 8, 2)) == 0.20
        assert get_limit_pct("128136.SZ") == 0.20


class TestRules:
    def test_stamp_duty_history(self):
        """印花税 2023-08-28 减半"""
        before = get_rules("600519.SH", datetime.date(2023, 8, 25))
        after = get_rules("600519.SH", datetime.date(2023, 8, 29))
        assert before.stamp_duty_sell == 0.001
        assert after.stamp_duty_sell == 0.0005

    def test_etf_cb_exempt_from_stamp_duty(self):
        assert get_rules("510300.SH").stamp_duty_sell == 0.0
        assert get_rules("113050.SH").stamp_duty_sell == 0.0

    def test_t_plus_1_by_asset_type(self):
        assert get_rules("600519.SH").t_plus_1 is True
        assert get_rules("510300.SH").t_plus_1 is True
        assert get_rules("113050.SH").t_plus_1 is False  # 转债 T+0

    def test_min_commission_default(self):
        r = get_rules("600519.SH")
        assert r.min_commission == 5.0
        assert r.commission_rate == 0.0001

    def test_date_none_returns_current(self):
        """date=None 返回现行规则（最后一条）"""
        assert get_rules("600519.SH", None).stamp_duty_sell == 0.0005
        assert get_limit_pct("300750.SZ", None) == 0.20

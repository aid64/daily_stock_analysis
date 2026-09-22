# -*- coding: utf-8 -*-
"""①-B 操作单生成器测试：三档输出 / 现金约束 / 状态机 / 渲染"""
from __future__ import annotations

from datetime import date

import pytest

from src.services import order_sheet_service as oss
from src.services.order_sheet_service import (
    generate_order_sheet,
    load_latest_sheet,
    mark_order_filled,
    mark_sheet_cancelled,
    render_text,
    save_order_sheet,
)

D = date(2026, 9, 7)


@pytest.fixture()
def sheets_dir(tmp_path, monkeypatch):
    d = tmp_path / "sheets"
    monkeypatch.setattr(oss, "SHEETS_DIR", d)
    return d


def _mk(current, target, prices, cash=100000.0, **kw):
    return generate_order_sheet(
        trade_date=D, current_positions=current,
        target_positions=target, t1_close_prices=prices,
        available_cash=cash, **kw)


class TestGenerate:
    def test_buy_and_sell_diff(self):
        sheet = _mk(
            current={"SH113062": 10, "SH118031": 10},
            target={"SH113062": 0, "SH118031": 10, "SZ123089": 10},
            prices={"SH113062": 128.0, "SH118031": 130.0, "SZ123089": 116.5},
        )
        directions = {o.symbol: o.direction for o in sheet.orders}
        assert directions == {"SH113062": "sell", "SZ123089": "buy"}
        sell = next(o for o in sheet.orders if o.direction == "sell")
        buy = next(o for o in sheet.orders if o.direction == "buy")
        assert sell.quantity == 10 and sell.limit_price == 128.0
        assert buy.quantity == 10 and buy.limit_price == 116.5
        # 转债放弃条件：买入开盘>116.5*1.02 放弃
        assert "118.830" in buy.abandon_rule
        # 持有：SH118031 差额 0 → hold
        assert any(h.symbol == "SH118031" for h in sheet.holds)

    def test_specific_price_not_range(self):
        """铁律：限价是具体数字，附带两条边界规则"""
        sheet = _mk(current={}, target={"SZ128093": 10},
                    prices={"SZ128093": 116.5})
        o = sheet.orders[0]
        assert o.limit_price == 116.5
        assert "开盘>118.830 放弃" in o.abandon_rule
        assert "14:50 未成交撤单" in o.cancel_rule
        assert "116.5-" not in o.abandon_rule  # 不是区间

    def test_hold_when_diff_below_lot(self):
        sheet = _mk(current={"SH600519": 100}, target={"SH600519": 130},
                    prices={"SH600519": 1500.0})
        assert not sheet.orders
        assert sheet.holds[0].reason.startswith("差额不足一手")

    def test_no_signal_no_action(self):
        sheet = _mk(current={}, target={}, prices={})
        assert not sheet.orders and not sheet.holds
        assert sheet.no_action_reason and "无信号" in sheet.no_action_reason

    def test_full_liquidation_when_target_empty(self):
        sheet = _mk(current={"SH113062": 10}, target={},
                    prices={"SH113062": 128.0})
        assert sheet.orders[0].direction == "sell"
        assert sheet.orders[0].quantity == 10

    def test_missing_price_goes_to_hold(self):
        sheet = _mk(current={"SH113062": 10}, target={"SH113062": 0},
                    prices={})
        assert not sheet.orders
        assert "缺 T-1 收盘价" in sheet.holds[0].reason

    def test_cash_constraint_greedy_allocation(self):
        """现金只够买满 1 只 → 价格低的买满，另一只进 holds 并注明"""
        sheet = _mk(
            current={},
            target={"SZ123089": 100, "SZ128093": 100},  # 各需 100 张
            prices={"SZ123089": 116.5, "SZ128093": 130.0},
            cash=12000.0,  # 只够 100 张 @116.5
        )
        buys = [o for o in sheet.orders if o.direction == "buy"]
        holds = [h for h in sheet.holds if "现金不足" in h.reason]
        assert len(buys) == 1 and len(holds) == 1
        assert buys[0].symbol == "SZ123089"  # 价格升序优先买满
        assert buys[0].quantity == 100
        assert "现金受限" not in buys[0].reason  # SZ123089 买满
        assert "现金不足" in holds[0].reason
        # 总买入不超预算
        assert sheet.total_buy_value <= 12000.0 + 0.01

    def test_sell_before_buy_ordering(self):
        """先卖后买：卖出回笼的资金视为可用现金（调用方计算后传入）"""
        sheet = _mk(
            current={"SH113062": 10},
            target={"SH113062": 0, "SZ123089": 10},
            prices={"SH113062": 128.0, "SZ123089": 116.5},
            cash=1280.0,  # ≈ 卖出回笼
        )
        assert [o.direction for o in sheet.orders] == ["sell", "buy"]

    def test_zero_cash_buys_go_to_holds(self):
        sheet = _mk(current={}, target={"SZ123089": 10},
                    prices={"SZ123089": 116.5}, cash=0.0)
        assert not sheet.orders
        assert "现金不足" in sheet.holds[0].reason


class TestLedger:
    def test_save_load_roundtrip(self, sheets_dir):
        sheet = _mk(current={"SH113062": 10}, target={"SH113062": 0},
                    prices={"SH113062": 128.0})
        save_order_sheet(sheet)
        loaded = load_latest_sheet("2026-09-07")
        assert loaded.sheet_id == sheet.sheet_id
        assert loaded.orders[0].limit_price == 128.0

    def test_state_machine(self, sheets_dir):
        sheet = _mk(current={"SH113062": 10, "SZ123089": 0},
                    target={"SH113062": 0, "SZ123089": 10},
                    prices={"SH113062": 128.0, "SZ123089": 116.5})
        save_order_sheet(sheet)
        assert sheet.status == "pending"

        mark_order_filled(sheet, "SH113062", 127.8)
        # 方案v2 批次A3：一腿足额、另一腿未动 → partially_filled（FIX 语义）
        assert sheet.status == "partially_filled"
        mark_order_filled(sheet, "SZ123089", 116.8)
        assert sheet.status == "filled"  # 全部成交

        loaded = load_latest_sheet("2026-09-07")
        assert loaded.status == "filled"
        assert loaded.fills["SH113062"]["actual_price"] == 127.8

    def test_cancel(self, sheets_dir):
        sheet = _mk(current={}, target={}, prices={})
        save_order_sheet(sheet)
        mark_sheet_cancelled(sheet, "用户暂停")
        assert load_latest_sheet("2026-09-07").status == "cancelled"

    def test_latest_picks_newest_day(self, sheets_dir):
        sheet = _mk(current={}, target={}, prices={})
        save_order_sheet(sheet)
        assert load_latest_sheet().sheet_id == sheet.sheet_id


class TestRender:
    def test_render_contains_price_and_rules(self):
        sheet = _mk(current={"SH113062": 10}, target={"SH113062": 0},
                    prices={"SH113062": 128.0})
        text = render_text(sheet)
        assert "@128 限价" in text
        assert "开盘<125.440 放弃" in text
        assert "理由" in text

    def test_render_no_action(self):
        sheet = _mk(current={}, target={}, prices={})
        assert "无信号" in render_text(sheet)


class TestFillDirectionGuard:
    """方向护栏：同 symbol 双腿时回填必须指定 direction，否则拒绝错腿回填。"""

    def _dual_leg_sheet(self):
        line_kw = dict(
            limit_price=100.0, abandon_rule="", cancel_rule="",
            est_value=100.0, reason="")
        return oss.OrderSheet(
            sheet_id="s1", trade_date="2026-09-07", created_at="",
            orders=[
                oss.OrderLine(direction="buy", symbol="SH113062",
                              quantity=100, **line_kw),
                oss.OrderLine(direction="sell", symbol="SH113062",
                              quantity=50, **line_kw),
            ])

    def test_dual_leg_without_direction_raises(self, sheets_dir):
        sheet = self._dual_leg_sheet()
        with pytest.raises(ValueError, match="双腿"):
            mark_order_filled(sheet, "SH113062", 100.0, 100)

    def test_dual_leg_with_direction_targets_correct_leg(self, sheets_dir):
        sheet = self._dual_leg_sheet()
        mark_order_filled(sheet, "SH113062", 100.0, 50, direction="sell")
        # 命中 sell 腿（计划 50）→ 足额；若误取 buy 腿（计划 100）则 partial
        assert sheet.fills["SH113062"]["complete"] is True
        assert sheet.fills["SH113062"]["planned_quantity"] == 50

    def test_single_leg_direction_none_still_works(self, sheets_dir):
        """向后兼容：单腿单不指定 direction 仍按原逻辑定位。"""
        sheet = _mk(current={"SH113062": 100}, target={"SH113062": 0},
                    prices={"SH113062": 128.0})
        mark_order_filled(sheet, "SH113062", 128.0)
        assert sheet.fills["SH113062"]["actual_price"] == 128.0

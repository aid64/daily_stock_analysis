# -*- coding: utf-8 -*-
"""买入命令测试（/买入）：用户确认才落单 + 各闸门拒绝且不抛栈。

对应改动 3(b)：买入侧最小闭环——命令只把"用户确认过的买单"追加进
当日已存在的操作单，绝不自动生成买单，绝不重建当日单据。
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from bot.commands import buy as buy_mod
from bot.commands.buy import BuyCommand, _do_buy
from bot.models import BotMessage, ChatType
from src.services import order_sheet_service as oss
from src.services import trading_halt_service
from src.services.order_sheet_service import (
    generate_order_sheet,
    load_latest_sheet,
    save_order_sheet,
)


@pytest.fixture()
def sheets_dir(tmp_path, monkeypatch):
    d = tmp_path / "sheets"
    monkeypatch.setattr(oss, "SHEETS_DIR", d)
    return d


@pytest.fixture(autouse=True)
def not_halted(monkeypatch):
    monkeypatch.setattr(trading_halt_service, "halt_status",
                        lambda: {"halted": False})


@pytest.fixture(autouse=True)
def no_ledger(monkeypatch):
    class _Ledger:
        def record_sheet(self, sheet):
            return True

    monkeypatch.setattr("src.services.simulation_ledger.SimulationLedger",
                        _Ledger)


def _snapshot(cash, held=None):
    return {
        "total_cash": cash,
        "accounts": [{"positions": [
            {"symbol": s, "quantity": q} for s, q in (held or [])]}],
    }


def _seed_today_sheet():
    sheet = generate_order_sheet(
        trade_date=date.today(), current_positions={}, target_positions={},
        t1_close_prices={}, available_cash=0.0)
    save_order_sheet(sheet)
    return sheet


def _msg(content="/买入 002475"):
    return BotMessage(
        platform="feishu", message_id="m1", user_id="u1", user_name="t",
        chat_id="c1", chat_type=ChatType.PRIVATE, content=content,
        raw_content=content, mentioned=False, timestamp=datetime.now())


def _patch(monkeypatch, *, cash, held=None, provenance="来自今日观察池候选"):
    monkeypatch.setattr(buy_mod, "_account_snapshot",
                        lambda: _snapshot(cash, held))
    monkeypatch.setattr(buy_mod, "_provenance", lambda symbol, held: provenance)


def test_buy_appends_order_to_existing_sheet(sheets_dir, monkeypatch):
    _seed_today_sheet()
    _patch(monkeypatch, cash=20000.0)

    out = _do_buy("002475", quantity=100, price=56.50)

    assert "买单已加入今日操作单" in out
    assert "002475" in out and "56.5" in out
    assert "来自今日观察池候选" in out
    reloaded = load_latest_sheet(date.today().isoformat())
    buys = [o for o in reloaded.orders if o.direction == "buy"]
    assert len(buys) == 1
    assert buys[0].quantity == 100
    assert buys[0].limit_price == 56.5
    # 追加而非重建：既有单据 identity 不变
    assert reloaded.sheet_id is not None


def test_buy_rejected_when_trading_halted(sheets_dir, monkeypatch):
    _seed_today_sheet()
    _patch(monkeypatch, cash=20000.0)
    monkeypatch.setattr(trading_halt_service, "halt_status",
                        lambda: {"halted": True, "reason": "回撤越线"})

    out = _do_buy("002475", quantity=100, price=56.50)

    assert "交易已暂停" in out
    assert "恢复交易" in out
    # 未落单
    reloaded = load_latest_sheet(date.today().isoformat())
    assert not [o for o in reloaded.orders if o.direction == "buy"]


def test_buy_rejected_when_no_sheet(sheets_dir, monkeypatch):
    _patch(monkeypatch, cash=20000.0)  # 不 seed 单据

    out = _do_buy("002475", quantity=100, price=56.50)

    assert "还没有操作单" in out


def test_buy_rejected_when_cash_not_positive(sheets_dir, monkeypatch):
    _seed_today_sheet()
    _patch(monkeypatch, cash=0.0)

    out = _do_buy("002475", quantity=100, price=56.50)

    assert "入金" in out
    reloaded = load_latest_sheet(date.today().isoformat())
    assert not [o for o in reloaded.orders if o.direction == "buy"]


def test_buy_rejected_over_single_order_cap(sheets_dir, monkeypatch):
    _seed_today_sheet()
    _patch(monkeypatch, cash=100000.0)

    out = _do_buy("002475", quantity=1000, price=56.50)  # 56500 > 20000

    assert "超单笔上限" in out
    reloaded = load_latest_sheet(date.today().isoformat())
    assert not [o for o in reloaded.orders if o.direction == "buy"]


def test_buy_rejected_when_quantity_not_a_lot(sheets_dir, monkeypatch):
    _seed_today_sheet()
    _patch(monkeypatch, cash=100000.0)

    out = _do_buy("002475", quantity=150, price=56.50)  # 股票整手=100

    assert "整手" in out
    reloaded = load_latest_sheet(date.today().isoformat())
    assert not [o for o in reloaded.orders if o.direction == "buy"]


def test_buy_auto_quantity_from_cash(sheets_dir, monkeypatch):
    _seed_today_sheet()
    _patch(monkeypatch, cash=6000.0)

    out = _do_buy("002475", quantity=None, price=56.50)

    assert "买入" in out
    reloaded = load_latest_sheet(date.today().isoformat())
    buys = [o for o in reloaded.orders if o.direction == "buy"]
    assert len(buys) == 1
    # 6000 / 56.5 = 106.2 → 整手 100
    assert buys[0].quantity == 100


def test_buy_command_never_raises(sheets_dir, monkeypatch):
    _seed_today_sheet()

    def _boom():
        raise RuntimeError("snapshot exploded")

    monkeypatch.setattr(buy_mod, "_account_snapshot", _boom)

    resp = BuyCommand().execute(_msg(), ["002475", "100", "56.50"])

    assert resp.text  # 有用户可读文案且未抛栈
    assert "失败" in resp.text


def test_buy_command_requires_args():
    resp = BuyCommand().execute(_msg("/买入"), [])
    assert "参数不足" in resp.text


# ── U6：手工买入与组合器同口径的风险提示（不阻断）────────────


def test_buy_risk_hint_over_limit_and_e2_defensive(sheets_dir, monkeypatch):
    _seed_today_sheet()
    # 持仓 002475 100股@54（¥5400）+ 现金 ¥12000 → 总值 ¥17400
    monkeypatch.setattr(buy_mod, "_account_snapshot", lambda: {
        "total_cash": 12000.0,
        "accounts": [{"positions": [
            {"symbol": "SZ002475", "quantity": 100, "last_price": 54.0}]}]})
    monkeypatch.setattr(buy_mod, "_provenance", lambda s, h: "测试")
    monkeypatch.setattr(
        "src.services.strategies.e2_timing.compute_e2_percentile",
        lambda: (0.875, "2026-09-26"))

    out = _do_buy("002475", quantity=100, price=56.50)

    assert "风险提示" in out
    # post-trade: (5400 + 5650) / 17400 = 63.5% > 25% → 带 mandate 标注
    assert "单票占比约 63.5%" in out and "超 mandate 单票参考上限 25%" in out
    assert "防守区" in out and "87.5%" in out
    # 提示不阻断：买单正常落单
    reloaded = load_latest_sheet(date.today().isoformat())
    assert any(o.direction == "buy" for o in reloaded.orders)


def test_buy_risk_hint_normal_no_alarm(sheets_dir, monkeypatch):
    _seed_today_sheet()
    monkeypatch.setattr(buy_mod, "_account_snapshot", lambda: {
        "total_cash": 40000.0, "accounts": [{"positions": []}]})
    monkeypatch.setattr(buy_mod, "_provenance", lambda s, h: "测试")
    monkeypatch.setattr(
        "src.services.strategies.e2_timing.compute_e2_percentile",
        lambda: (0.40, "2026-09-26"))

    out = _do_buy("002475", quantity=100, price=56.50)

    # 占比行常态展示（5650/40000=14.1%）但不带越限标注；E2 非防守不出现
    assert "单票占比约 14.1%" in out
    assert "超 mandate" not in out and "防守区" not in out


def test_buy_risk_hint_fail_open(sheets_dir, monkeypatch):
    """快照/E2 全部炸掉 → 提示静默消失，买入照常。"""
    _seed_today_sheet()
    # last_price 非数字 → 只砸提示层的占比计算（_held_symbols 不读价格）
    monkeypatch.setattr(buy_mod, "_account_snapshot", lambda: {
        "total_cash": 40000.0,
        "accounts": [{"positions": [
            {"symbol": "SZ002475", "quantity": 100, "last_price": "abc"}]}]})
    monkeypatch.setattr(buy_mod, "_provenance", lambda s, h: "测试")
    monkeypatch.setattr(
        "src.services.strategies.e2_timing.compute_e2_percentile",
        lambda: (_ for _ in ()).throw(RuntimeError("pit down")))

    out = _do_buy("002475", quantity=100, price=56.50)

    assert "买单已加入今日操作单" in out
    assert "风险提示" not in out

# -*- coding: utf-8 -*-
"""审视修复回归测试（2026-09-19，审计第 6/7/8 条）

6: WAIT 档（暂缓一日）此前落进全卖分支——文案"暂缓观察"与"卖出全部"矛盾
7: load_latest_sheet 按文件名（uuid hex）排序，同日多单"最新"是抽签
8: partially_filled 状态重跑 /操作单 会另存新单叠加（半仓流程的预期路径）
"""
from __future__ import annotations

import os
import tempfile
import time
from datetime import date
from pathlib import Path

import pytest

from src.services import order_sheet_service as oss
from src.services.order_sheet_service import (
    SHEET_STATUS_PARTIALLY_FILLED,
    generate_order_sheet,
    load_latest_sheet,
    save_order_sheet,
)


def _tmp_dir() -> Path:
    """本机 pytest tmp_path 存在权限问题，直接用 tempfile。"""
    return Path(tempfile.mkdtemp())


def _mk_sheet(sheet_id: str, trade_date: str):
    return generate_order_sheet(
        trade_date=date.fromisoformat(trade_date),
        current_positions={"000899": 100},
        target_positions={},
        t1_close_prices={"000899": 9.75},
        available_cash=0.0,
        sheet_id=sheet_id,
    )


class TestLatestSheetByMtime:
    """第 7 条：同日多单按修改时间选最新，不按文件名"""

    def test_mtime_wins_over_lexical_order(self, monkeypatch):
        tmp = _tmp_dir()
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp)
        day = "2026-09-19"
        # 名字字典序大的（旧实现会选中）反而是旧单
        old_sheet = _mk_sheet(f"{day}_zzzzzzzz", day)
        new_sheet = _mk_sheet(f"{day}_aaaaaaaa", day)
        save_order_sheet(old_sheet)
        save_order_sheet(new_sheet)

        day_dir = tmp / day
        stale = time.time() - 3600
        os.utime(day_dir / f"{old_sheet.sheet_id}.json", (stale, stale))

        latest = load_latest_sheet(day)
        assert latest is not None
        assert latest.sheet_id == f"{day}_aaaaaaaa"

    def test_no_sheets_returns_none(self, monkeypatch):
        monkeypatch.setattr(oss, "SHEETS_DIR", _tmp_dir() / "nothing")
        assert load_latest_sheet("2026-09-19") is None


class TestPartiallyFilledReuse:
    """第 8 条：部分成交当日单重跑 /操作单 必须沿用，不得另存新单"""

    def test_build_sheet_text_reuses_partially_filled(self, monkeypatch):
        from bot.commands import order_sheet as cmd

        today = date.today().isoformat()
        existing = _mk_sheet(f"{today}_reuse0001", today)
        existing.status = SHEET_STATUS_PARTIALLY_FILLED

        saved = []
        monkeypatch.setattr(
            "src.services.order_sheet_service.load_latest_sheet",
            lambda trade_date=None: existing)
        monkeypatch.setattr(
            "src.services.order_sheet_service.save_order_sheet",
            lambda s: saved.append(s) or s)
        monkeypatch.setattr(
            "src.services.portfolio_service.PortfolioService."
            "get_portfolio_snapshot",
            lambda self, **kw: {"total_cash": 100.0, "accounts": []})
        monkeypatch.setattr(
            "src.services.trading_halt_service.halt_status",
            lambda: {"halted": False})
        monkeypatch.setattr(
            "src.services.account_decision_service.load_price_rules",
            lambda: {})
        monkeypatch.setattr(
            "src.services.account_decision_service.build_account_decision",
            lambda **kw: None)
        monkeypatch.setattr(
            "src.services.account_decision_service.render_decision_package",
            lambda decision, sheet=None: "PKG")
        monkeypatch.setattr(cmd, "_cb_research_appendix", lambda: "")
        monkeypatch.setattr(cmd, "_refresh_snapshot_quietly", lambda: None)
        # P1.5：首笔引导不连真实库（隔离纪律）
        monkeypatch.setattr(cmd, "_first_fill_hint", lambda service: "")

        text = cmd._build_sheet_text()

        assert text.startswith("PKG")
        assert saved == []          # 关键断言：没有另存新单


class TestFirstFillHint:
    """P1.5 首笔引导（v4.2 发现#4）：零执行期间的回填操作指引。"""

    def test_hint_shown_when_no_trades_ever(self):
        from bot.commands.order_sheet import _first_fill_hint

        class _Svc:
            def list_trade_events(self, **kw):
                return {"total": 0, "trades": []}

        hint = _first_fill_hint(_Svc())
        assert "首笔回填指引" in hint
        assert "/对比" in hint

    def test_hint_hidden_after_first_trade(self):
        from bot.commands.order_sheet import _first_fill_hint

        class _Svc:
            def list_trade_events(self, **kw):
                return {"total": 5, "trades": [{}]}

        assert _first_fill_hint(_Svc()) == ""

    def test_hint_silent_on_query_failure(self):
        from bot.commands.order_sheet import _first_fill_hint

        class _Svc:
            def list_trade_events(self, **kw):
                raise RuntimeError("db unavailable")

        assert _first_fill_hint(_Svc()) == ""

# -*- coding: utf-8 -*-
"""G5 成交回填测试：bot 命令 + API + 操作单联动 + 真实账本"""
from __future__ import annotations

from datetime import date

import pytest

from bot.commands.fill import FillCommand, _do_fill
from src.services.order_sheet_service import (
    generate_order_sheet,
    load_latest_sheet,
    save_order_sheet,
)

D = date(2026, 9, 9)


def _msg():
    return type("M", (), {"platform": "test"})()


class TestFillCommand:
    def test_fill_marks_sheet_and_records_trade(self, tmp_path, monkeypatch,
                                                isolated_db):
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")

        # 生成一张有 002475 买入腿的操作单
        sheet = generate_order_sheet(
            trade_date=D, current_positions={},
            target_positions={"SZ002475": 100},
            t1_close_prices={"SZ002475": 56.5},
            available_cash=10000.0,
            sheet_id=f"{D.isoformat()}_fill01",
        )
        save_order_sheet(sheet)
        assert any(o.symbol == "SZ002475" and o.direction == "buy"
                   for o in sheet.orders)

        # 回填
        result = _do_fill("002475", "buy", 100, 56.50)
        assert "已回填" in result
        assert "filled" in result

        # 操作单状态变化
        loaded = load_latest_sheet(D.isoformat())
        assert "SZ002475" in loaded.fills
        assert loaded.fills["SZ002475"]["actual_price"] == 56.50

        # 真实账本有记录
        from src.services.portfolio_service import PortfolioService
        svc = PortfolioService()
        events = svc.list_trade_events(symbol="SZ002475", page=1, page_size=10)
        assert any(e["price"] == 56.50 for e in events["items"])

    def test_fill_without_sheet_still_records(self, tmp_path, monkeypatch,
                                              isolated_db):
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")
        result = _do_fill("000899", "buy", 100, 11.20)
        assert "已回填" in result
        assert "无操作单" in result  # 诚实说明

    def test_command_arg_validation(self):
        cmd = FillCommand()
        r1 = cmd.execute(_msg(), ["002475", "买入"])
        assert "参数不足" in r1.text
        r2 = cmd.execute(_msg(), ["002475", "加仓", "100", "56"])
        assert "方向必须" in r2.text
        r3 = cmd.execute(_msg(), ["002475", "买入", "abc", "56"])
        assert "必须是数字" in r3.text

    def test_canonical_symbol_handled(self, tmp_path, monkeypatch,
                                      isolated_db):
        """用户输入 002475 / SZ002475 都归一到 SZ002475。"""
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")
        r1 = _do_fill("002475", "buy", 100, 10.0)
        r2 = _do_fill("SZ002475", "buy", 100, 10.0)
        assert "SZ002475" in r1 and "SZ002475" in r2


class TestFillAPI:
    def test_post_endpoint(self, tmp_path, monkeypatch, isolated_db):
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")
        from fastapi.testclient import TestClient
        from api.app import app

        c = TestClient(app)
        r = c.post("/api/v1/assistant/order-sheets/fills", json={
            "symbol": "000899", "direction": "buy",
            "price": 11.16, "quantity": 100})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_invalid_body_400(self):
        from fastapi.testclient import TestClient
        from api.app import app

        c = TestClient(app)
        r = c.post("/api/v1/assistant/order-sheets/fills", json={
            "symbol": "", "direction": "x"})
        assert r.status_code == 400

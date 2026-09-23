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

D = date.today()  # _do_fill 查"今天"的操作单——测试单必须建在今天，否则跨日失效


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
        assert "足额成交 100/100" in result  # 方案v2 批次A：足额文案带量对比

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
        """用户输入 002475 / SZ002475 都归一到 SZ002475。

        方案v2 批次A：同日同标的同向同量同价的第二次输入视为重复回填，
        幂等拦截（这正是双记账修复的行为）。
        """
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")
        r1 = _do_fill("002475", "buy", 100, 10.0)
        r2 = _do_fill("SZ002475", "buy", 100, 10.0)
        assert "SZ002475" in r1
        assert "幂等拦截" in r2

    def test_bare_symbol_leg_matched(self, tmp_path, monkeypatch,
                                     isolated_db):
        """P1.5 验收发现的回归：裸码操作单腿必须能被回填匹配。

        股票决策链（账户决策包）的操作单 symbol 是持仓快照存储形态
        （裸码 002475），转债链是前缀形态（SH113062）。旧实现只做
        精确匹配，股票腿永远匹配不上——回填只记账不标单，股票操作单
        的标单功能自 09-15 上线以来从未生效。修复：按 canonical 归一
        匹配，标单与 fills 用腿的原始形态。
        """
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")

        # 裸码腿（股票决策链形态）
        sheet = generate_order_sheet(
            trade_date=D, current_positions={"002475": 200},
            target_positions={"002475": 100},
            t1_close_prices={"002475": 54.84},
            available_cash=1495.0,
            sheet_id=f"{D.isoformat()}_bare01",
        )
        save_order_sheet(sheet)
        assert any(o.symbol == "002475" and o.direction == "sell"
                   for o in sheet.orders)

        # 隔离库先建仓（卖出校验按事件流重放可用量）
        from datetime import timedelta
        from src.services.portfolio_analysis_context import (
            get_or_create_default_account)
        from src.services.portfolio_service import PortfolioService
        svc = PortfolioService()
        acct = get_or_create_default_account(svc)
        svc.record_trade(
            account_id=int(acct["account_id"]), symbol="SZ002475",
            trade_date=D - timedelta(days=1), side="buy",
            quantity=200, price=56.77, trade_uid="t-bare-setup")

        result = _do_fill("002475", "sell", 100, 54.50)
        assert "已回填" in result
        assert "足额成交 100/100" in result   # 不再是"当日操作单无该腿"

        loaded = load_latest_sheet(D.isoformat())
        assert "002475" in loaded.fills
        assert loaded.fills["002475"]["actual_price"] == 54.50


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

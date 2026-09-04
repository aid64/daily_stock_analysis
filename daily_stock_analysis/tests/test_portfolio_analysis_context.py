# -*- coding: utf-8 -*-
"""P0-A 持仓上下文打通测试：helper / bot 命令 / pipeline 注入

隔离策略与 tests/test_portfolio_service.py 一致：DATABASE_PATH 指向
tmp 文件 + DatabaseManager.reset_instance()，绝不触碰真实 DB。
"""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

import pytest

from bot.commands.portfolio import (
    PortfolioClearCommand,
    PortfolioCommand,
    PortfolioRecordCommand,
)
from src.services.portfolio_analysis_context import (
    build_all_position_contexts,
    build_position_context,
    get_or_create_default_account,
    list_positions_text,
)
from src.services.portfolio_service import PortfolioService


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "portfolio_test.db"
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    from src.config import Config
    from src.storage import DatabaseManager

    Config.reset_instance()
    DatabaseManager.reset_instance()
    yield db_path
    DatabaseManager.reset_instance()
    Config.reset_instance()
    monkeypatch.delenv("ENV_FILE", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)


def _service_with_position(symbol: str = "600519.SH", qty: float = 100.0,
                           price: float = 1820.0) -> PortfolioService:
    service = PortfolioService()
    account = get_or_create_default_account(service)
    assert account is not None and account["account_id"] is not None
    service.record_trade(
        account_id=int(account["account_id"]),
        symbol=symbol,
        trade_date=date.today(),
        side="buy",
        quantity=qty,
        price=price,
    )
    return service


def _fake_message():
    return type("M", (), {"platform": "test"})()


class TestHelpers:
    def test_default_account_bootstrap_and_position_context(self, isolated_db):
        service = _service_with_position()
        # 再调一次应返回同一账户（幂等，不重复创建）
        again = get_or_create_default_account(service)
        accounts = service.list_accounts()
        assert len(accounts) == 1
        assert again["account_id"] == accounts[0]["id"]

        context = build_position_context(service, symbol="600519.SH")
        assert context is not None
        assert context["symbol"] == "SH600519"  # 存储归一形态（裸码不猜市场）
        assert float(context["quantity"]) == 100.0
        assert float(context["avg_cost"]) == 1820.0
        assert context["account_name"]

    def test_build_position_context_missing_returns_none(self, isolated_db):
        service = _service_with_position()
        assert build_position_context(service, symbol="000001") is None
        assert build_position_context(service, symbol="") is None

    def test_build_all_position_contexts_maps_by_symbol(self, isolated_db):
        _service_with_position()
        _service_with_position(symbol="000001.SZ", qty=200.0, price=10.0)
        contexts = build_all_position_contexts()
        assert set(contexts) == {"SH600519", "SZ000001"}  # 存储归一形态
        assert float(contexts["SZ000001"]["quantity"]) == 200.0

    def test_list_positions_text_lists_holdings(self, isolated_db):
        _service_with_position()
        text = list_positions_text()
        assert "600519" in text and "100" in text and "1820" in text

    def test_list_positions_text_guides_when_empty(self, isolated_db):
        PortfolioService()  # 初始化空库
        text = list_positions_text()
        assert "无持仓" in text and "/持仓登记" in text


class TestBotCommands:
    def test_record_then_list_then_clear(self, isolated_db, monkeypatch):
        record = PortfolioRecordCommand()
        resp = record.execute(_fake_message(), ["600519", "买入", "100", "1820.00", "5"])
        assert "已登记" in resp.text and "600519" in resp.text

        listing = PortfolioCommand().execute(_fake_message(), [])
        assert "600519" in listing.text

        bad = record.execute(_fake_message(), ["600519", "加仓", "100", "10"])
        assert "方向必须" in bad.text

        short = record.execute(_fake_message(), ["600519", "买入"])
        assert "参数不足" in short.text

        clear = PortfolioClearCommand().execute(_fake_message(), [])
        assert "已清空" in clear.text
        after = PortfolioCommand().execute(_fake_message(), [])
        assert "无持仓" in after.text


class TestPipelineInjection:
    def test_pipeline_accepts_context_map(self):
        """pipeline 实例接受 portfolio_context_map 并按原样存储"""
        from src.config import Config
        from src.core.pipeline import StockAnalysisPipeline

        pipeline = StockAnalysisPipeline(
            config=Config(stock_list=["600519"]),
            portfolio_context_map={"600519.SH": {"symbol": "600519.SH",
                                                 "quantity": 100}},
        )
        assert pipeline.portfolio_context_map == {
            "600519.SH": {"symbol": "600519.SH", "quantity": 100}
        }
        assert pipeline.portfolio_context is None

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
    build_target_position_caps,
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


class TestTargetPositionCaps:
    """①-A 验收：三层仓位约束在典型持仓（5 转债 + 3 股票，10k 本金）下"""

    def _typical_portfolio(self):
        """现金 10000 + 5 转债(@110,10张) + 3 股票(@10,100股)"""
        service = PortfolioService()
        account = get_or_create_default_account(service)
        account_id = int(account["account_id"])
        service.record_cash_ledger(
            account_id=account_id, event_date=date.today(),
            direction="in", amount=10000.0, currency="CNY")
        cb_syms = ["SH113062", "SH118031", "SZ123089", "SH110053", "SZ127012"]
        stock_syms = ["SH600519", "SZ000001", "SH601318"]
        for sym in cb_syms:
            service.record_trade(account_id=account_id, symbol=sym,
                                 trade_date=date.today(), side="buy",
                                 quantity=10, price=110.0)
        for sym in stock_syms:
            service.record_trade(account_id=account_id, symbol=sym,
                                 trade_date=date.today(), side="buy",
                                 quantity=100, price=10.0)
        return service, account_id, cb_syms, stock_syms

    def test_three_layer_caps_on_typical_portfolio(self, isolated_db):
        service, account_id, cb_syms, stock_syms = self._typical_portfolio()
        caps = build_target_position_caps(
            account_id=account_id, include_realtime=False)

        assert set(caps) == set(cb_syms + stock_syms)
        equity = next(iter(caps.values()))["equity"]
        assert 9000 < equity <= 10000  # 现金+市值-费用

        # 第一层：无止损 → 单票上限路径（capped_by=no_stop_loss）
        assert all(c["capped_by"] == "no_stop_loss" for c in caps.values())
        # 单票 ≤ equity × 25%（max_position_pct 默认）
        assert all(c["target_value"] <= equity * 0.25 + 0.01 for c in caps.values())
        # 第三层默认 tier=watch（0.2）→ 目标显著低于上限
        assert all(c["layer3_weight"] == 0.2 for c in caps.values())
        # 离线无实时价 → 回退 avg_cost 并标记
        assert all(c["price_fallback"] for c in caps.values())

    def test_global_total_exposure_cap(self, isolated_db):
        """各标的独立 cap 之和超限时 → 全局二次裁剪（接线层增量）"""
        service, account_id, cb_syms, stock_syms = self._typical_portfolio()
        tiers = {s: "strong_buy" for s in cb_syms + stock_syms}  # weight 1.0
        caps = build_target_position_caps(
            account_id=account_id, opportunity_tiers=tiers,
            include_realtime=False)
        equity = next(iter(caps.values()))["equity"]
        total = sum(c["target_value"] for c in caps.values())
        assert total <= equity * 0.95 + 0.01
        # 触发全局裁剪时所有标的带标记（有持仓必然触顶：8 标的 watch=1.0 全顶格）
        assert all(c["capped_by"] == "global_total_exposure" for c in caps.values())

    def test_drawdown_scales_down_targets(self, isolated_db):
        service, account_id, *_ = self._typical_portfolio()
        tiers = {s: "strong_buy" for s in
                 ("SH113062", "SH118031", "SZ123089", "SH110053", "SZ127012",
                  "SH600519", "SZ000001", "SH601318")}
        base = build_target_position_caps(
            account_id=account_id, opportunity_tiers=tiers, include_realtime=False)
        dd = build_target_position_caps(
            account_id=account_id, opportunity_tiers=tiers,
            current_drawdown_pct=7.0, include_realtime=False)
        sym = "SH113062"
        assert dd[sym]["layer2_multiplier"] == 0.8  # 回撤 5-10% 档
        assert dd[sym]["target_value"] < base[sym]["target_value"]

    def test_stop_loss_activates_risk_budget_path(self, isolated_db):
        service, account_id, *_ = self._typical_portfolio()
        caps = build_target_position_caps(
            account_id=account_id,
            stop_losses={"SH113062": 99.0},  # entry 110 → per_unit_risk 11
            include_realtime=False)
        # risk budget = equity×2% ≈ 200 → qty = 200/11 ≈ 18 张 → 远小于单票上限
        cap = caps["SH113062"]
        assert cap["capped_by"] == "risk_budget"
        assert cap["target_quantity"] <= 18

    def test_empty_portfolio_returns_empty(self, isolated_db):
        PortfolioService()
        assert build_target_position_caps(include_realtime=False) == {}

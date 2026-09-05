# -*- coding: utf-8 -*-
"""③-A 模拟账本测试：费率对齐 QR PaperBroker / 记录幂等 / 真实对比"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.services.simulation_ledger import (
    SimulationLedger,
    calculate_costs,
    load_fees,
)
from src.services.order_sheet_service import generate_order_sheet

D = date(2026, 9, 7)


class TestFeeModel:
    def test_fee_numbers_align_with_qr_paperbroker(self):
        """与 QR PaperBroker 同公式：佣金 max(率,5)+印花税(卖)+过户费(双边)"""
        fees = {"commission": 0.0000845, "min_commission": 5.0,
                "stamp_duty": 0.0005, "transfer_fee": 0.00002}
        # 买入 10 张 @128：notional 1280 → 佣金 floor 5 + 过户 0.0256
        buy = calculate_costs(10, 128.0, "buy", fees)
        assert buy["commission"] == 5.0
        assert buy["stamp_duty"] == 0.0
        assert buy["transfer_fee"] == pytest.approx(0.0256)
        assert buy["total"] == pytest.approx(5.0256)
        assert buy["slippage"] == pytest.approx(1.28)  # 万10 单列
        # 卖出 10 张 @116.5：notional 1165 → 佣金 5 + 印花 0.5825 + 过户 0.0233
        sell = calculate_costs(10, 116.5, "sell", fees)
        assert sell["stamp_duty"] == pytest.approx(0.5825)
        assert sell["total"] == pytest.approx(5.0 + 0.5825 + 0.0233)

    def test_large_order_commission_proportional(self):
        fees = {"commission": 0.0000845, "min_commission": 5.0,
                "stamp_duty": 0.0005, "transfer_fee": 0.00002}
        buy = calculate_costs(2000, 100.0, "buy", fees)  # notional 20万
        assert buy["commission"] == pytest.approx(200000 * 0.0000845)
        assert buy["commission"] > 5.0  # 超过地板——费率规模效应

    def test_mandate_fees_override(self, tmp_path, monkeypatch):
        mandate = tmp_path / "mandate.yaml"
        mandate.write_text(
            "fees:\n  commission: 0.0002\n  min_commission: 10.0\n",
            encoding="utf-8")
        monkeypatch.setattr(
            "src.services.simulation_ledger._ROOT", tmp_path)
        fees = load_fees()
        assert fees["commission"] == 0.0002 and fees["min_commission"] == 10.0
        assert fees["stamp_duty"] == 0.0005  # 未声明的沿用基线


class TestLedger:
    @pytest.fixture()
    def ledger(self, tmp_path):
        return SimulationLedger(ledger_path=tmp_path / "ledger.json")

    def _sheet(self, tmp_path):
        return generate_order_sheet(
            trade_date=D,
            current_positions={"SH113062": 10},
            target_positions={"SH113062": 0, "SZ123089": 10},
            t1_close_prices={"SH113062": 128.0, "SZ123089": 116.5},
            available_cash=12000.0,
            sheet_id=f"{D.isoformat()}_test01",
        )

    def test_record_sheet_idempotent(self, ledger, tmp_path):
        sheet = self._sheet(tmp_path)
        first = ledger.record_sheet(sheet)
        assert len(first) == 2  # sell + buy
        second = ledger.record_sheet(sheet)
        assert second == []  # 同单重记幂等
        assert len(ledger._data["entries"]) == 2

    def test_compare_matched_and_unmatched(self, ledger, tmp_path,
                                           isolated_db):
        sheet = self._sheet(tmp_path)
        ledger.record_sheet(sheet)

        # 真实账本：先建仓（历史买入），再回填卖出 SH113062（匹配模拟）；
        # 123089 未回填（模拟想买但用户没执行）
        service = PortfolioServiceStub(isolated_db, trade_date=D - timedelta(days=30))
        service.record_trade("SH113062", "buy", 127.0)
        sell_day = PortfolioServiceStub(isolated_db, trade_date=D)
        sell_day.record_trade("SH113062", "sell", 127.8)

        result = ledger.compare_with_real(service)
        assert result["n_sim"] == 2
        assert result["n_matched"] == 1
        assert result["n_unmatched"] == 1
        matched = next(r for r in result["rows"] if r["matched"])
        assert matched["real_price"] == 127.8
        assert matched["sim_price"] == 128.0

    def test_render_text(self, ledger, tmp_path, isolated_db):
        sheet = self._sheet(tmp_path)
        ledger.record_sheet(sheet)
        service = PortfolioServiceStub(isolated_db, trade_date=D)
        text = ledger.render_comparison_text(
            ledger.compare_with_real(service))
        assert "模拟 2 笔" in text
        assert "未回填" in text


class PortfolioServiceStub:
    """最小真实账本 stub：走真实 PortfolioService + 隔离 DB

    回填日期 = 操作单日期（真实成交发生在操作单当日）。
    """

    def __init__(self, db_path: Path, trade_date: date = date.today()):
        from src.services.portfolio_analysis_context import (
            get_or_create_default_account)
        from src.services.portfolio_service import PortfolioService

        self._svc = PortfolioService()
        account = get_or_create_default_account(self._svc)
        self._account_id = int(account["account_id"])
        self._trade_date = trade_date

    def record_trade(self, symbol: str, side: str, price: float,
                     quantity: int = 10):
        self._svc.record_trade(
            account_id=self._account_id, symbol=symbol,
            trade_date=self._trade_date, side=side,
            quantity=quantity, price=price)

    def list_trade_events(self, **kw):
        kw.setdefault("account_id", self._account_id)
        return self._svc.list_trade_events(**kw)

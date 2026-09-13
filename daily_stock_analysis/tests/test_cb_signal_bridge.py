# -*- coding: utf-8 -*-
"""①-C 转债信号桥测试：契约读取/校验/降级 + /操作单 全链路（tmp 契约+隔离DB）"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from bot.commands.order_sheet import OrderSheetCommand
from src.services.cb_signal_bridge import (
    cb_plan_to_targets,
    load_cb_plan,
)
from src.services.portfolio_analysis_context import (
    get_or_create_default_account,
)
from src.services.portfolio_service import PortfolioService

PLAN = {
    "schema": "cb_current_plan_v1",
    # 日期动态化：新鲜度门槛（pit_end 距今 ≤7 天）会拒绝过期契约——
    # 硬编码日期会让测试在跨周后必然失败。
    "generated_at": f"{date.today().isoformat()}T08:00:00",
    "pit_end": date.today().isoformat(),
    "latest_rebalance": "2026-07-01",
    "capital_base": 10000.0,
    "targets": {
        "113062.SH": {"weight": 0.2, "t1_close": 128.0},
        "123089.SZ": {"weight": 0.2, "t1_close": 116.5},
        "113691.SH": {"weight": 0.2, "t1_close": 148.0},
    },
}


@pytest.fixture()
def plan_path(tmp_path: Path) -> Path:
    p = tmp_path / "current_plan.json"
    p.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "t.db"
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


class TestLoadPlan:
    def test_load_and_validate(self, plan_path):
        plan = load_cb_plan(plan_path)
        assert plan is not None
        assert plan["capital_base"] == 10000.0

    def test_missing_returns_none(self, tmp_path):
        assert load_cb_plan(tmp_path / "nope.json") is None

    def test_bad_schema_rejected(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({**PLAN, "schema": "v999"}), encoding="utf-8")
        assert load_cb_plan(p) is None

    def test_corrupt_json_rejected(self, tmp_path):
        p = tmp_path / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_cb_plan(p) is None

    def test_empty_targets_returns_none(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text(json.dumps({**PLAN, "targets": {}}), encoding="utf-8")
        assert load_cb_plan(p) is None


class TestTargets:
    def test_weight_to_lots(self, plan_path):
        plan = load_cb_plan(plan_path)
        targets = cb_plan_to_targets(plan)
        # 0.2×10000=2000 / 128 = 15.6 张 → floor 到 10 张（lot=10）
        assert targets["SH113062"] == 10  # 归一到存储形态
        # 0.2×10000=2000 / 116.5 = 17.2 → 10 张
        assert targets["SZ123089"] == 10
        assert sum(targets.values()) == 30

    def test_zero_weight_yields_zero(self, plan_path, tmp_path):
        plan = dict(PLAN)
        plan["targets"] = {**PLAN["targets"], "110053.SH": {"weight": 0.0,
                                                            "t1_close": 100.0}}
        p = tmp_path / "with_zero.json"
        p.write_text(json.dumps(plan), encoding="utf-8")
        targets = cb_plan_to_targets(load_cb_plan(p))
        assert targets["SH110053"] == 0  # 零权重 = 清出指令

    def test_invalid_entry_skipped(self, plan_path, tmp_path):
        plan = dict(PLAN)
        plan["targets"] = {**PLAN["targets"], "110091.SH": {"weight": 0.2,
                                                            "t1_close": 0}}
        p = tmp_path / "invalid.json"
        p.write_text(json.dumps(plan), encoding="utf-8")
        targets = cb_plan_to_targets(load_cb_plan(p))
        assert "110091.SH" not in targets


class TestOrderSheetCommand:
    def test_full_chain_generates_sheet(self, plan_path, isolated_db,
                                        monkeypatch, tmp_path):
        # 准备持仓：已持有 113062 10 张（目标 10 → 持有），
        # 123089 未持有（目标 10 → 买入），另持有一只信号外股票
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(
            "src.services.cb_signal_bridge.DEFAULT_PLAN_PATH", plan_path)
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")

        service = PortfolioService()
        account = get_or_create_default_account(service)
        aid = int(account["account_id"])
        service.record_cash_ledger(account_id=aid, event_date=date.today(),
                                   direction="in", amount=10000.0,
                                   currency="CNY")
        service.record_trade(account_id=aid, symbol="113062.SH",
                             trade_date=date.today(), side="buy",
                             quantity=10, price=127.0)
        service.record_trade(account_id=aid, symbol="SH600519",
                             trade_date=date.today(), side="buy",
                             quantity=100, price=10.0)

        text = OrderSheetCommand().execute(
            type("M", (), {"platform": "test"})(), []).text

        assert "今日操作单" in text
        assert "买入 SZ123089 @116.5 限价 · 10张" in text
        # 113062 已在目标内 → 持有；600519 信号外股票 → 个人持仓展示不下单
        assert "持有 SH113062" in text
        assert "个人持仓" in text
        assert "600519" in text
        assert "118.830 放弃" in text

    def test_plan_missing_degrades(self, isolated_db, monkeypatch, tmp_path):
        import src.services.cb_signal_bridge as bridge
        import src.services.order_sheet_service as oss
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")
        monkeypatch.setattr(bridge, "DEFAULT_PLAN_PATH",
                            tmp_path / "nope.json")
        text = OrderSheetCommand().execute(
            type("M", (), {"platform": "test"})(), []).text
        assert "信号源不可用" in text
        assert "--export-plan" in text

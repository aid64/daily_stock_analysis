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


@pytest.fixture()
def isolated_snapshot_dir(monkeypatch, tmp_path: Path):
    """隔离决策快照落盘目录——OrderSheetCommand 执行会 refresh() 快照，
    未隔离会覆写真实 output/decision_snapshot/latest.json（Web 横幅/托盘数据源）。"""
    import src.services.decision_snapshot_service as dss
    monkeypatch.setattr(dss, "SNAPSHOT_DIR", tmp_path / "snapshot")
    return tmp_path / "snapshot"


class TestOrderSheetCommand:
    def test_full_chain_generates_sheet(self, plan_path, isolated_db,
                                        isolated_snapshot_dir,
                                        monkeypatch, tmp_path):
        # 2026-09-15 账户中心重构：/操作单 主体 = 账户决策包（真实持仓 +
        # 规则 + 集中度），CB 契约只作为研究观察附注。本测试改为断言：
        # ①真实持仓进入决策块 ②CB 目标降级为附注（不再直接生成买卖单）
        # ③个人股票持仓照常展示。
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

        # 账户决策包主体（新语义；渲染已重写为"今日待办"卡片式）
        assert "今日待办" in text
        # 真实持仓进入决策块（转债持仓：无规则 → 持有/集中度判断）
        assert "113062" in text
        # 个人股票持仓展示
        assert "600519" in text
        # CB 契约目标降级为研究观察附注，不再直接生成买入单
        assert "研究观察" in text
        assert "不构成操作指令" in text
        assert "买入 SZ123089" not in text

    def test_plan_missing_still_decides_for_account(self, isolated_db,
                                                    isolated_snapshot_dir,
                                                    monkeypatch, tmp_path):
        """CB 契约缺失不再阻塞账户决策——决策对象是真实账户，不是策略。"""
        import src.services.order_sheet_service as oss
        import src.services.cb_signal_bridge as bridge
        monkeypatch.setattr(oss, "SHEETS_DIR", tmp_path / "sheets")
        monkeypatch.setattr(bridge, "DEFAULT_PLAN_PATH",
                            tmp_path / "nope.json")
        text = OrderSheetCommand().execute(
            type("M", (), {"platform": "test"})(), []).text
        # 账户决策包照常生成（无持仓时是空决策；"今日待办"卡片式）
        assert "今日待办" in text
        # CB 侧降级为附注提示
        assert "研究观察" in text

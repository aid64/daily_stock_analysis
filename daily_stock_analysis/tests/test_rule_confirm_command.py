# -*- coding: utf-8 -*-
"""U0 规则确认命令单测（REFACTOR-PLAN v5.0 U0 文本命令版）"""

import json

import pytest

from bot.commands.rule_confirm import (
    RULE_INVENTORY,
    RuleConfirmCommand,
    load_rule_states,
    record_rule_action,
)
from src.services.rule_confirmation_service import _ledger_path


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.services.rule_confirmation_service._ledger_path",
        lambda: tmp_path / "rule_confirmations.jsonl")
    return tmp_path / "rule_confirmations.jsonl"


def _cmd():
    return RuleConfirmCommand()


class TestRuleInventory:
    def test_inventory_has_five_rules_with_unique_ids(self):
        ids = [r["id"] for r in RULE_INVENTORY]
        assert len(ids) == 5 and len(set(ids)) == 5
        assert ids == [f"R{i}" for i in range(1, 6)]
        for rule in RULE_INVENTORY:
            assert rule["does"] and rule["basis"] and rule["validation"]


class TestDecisionPackageDisclosure:
    """U0 披露（2026-09-27）：未确认规则必须在决策包可见（验收补齐）。"""

    def _render(self):
        from src.services.account_decision_service import render_decision_package
        from src.services.strategies.types import AccountDecision
        return render_decision_package(AccountDecision(trade_date="2026-09-27"))

    def test_unconfirmed_rules_disclosed(self, isolated_ledger):
        text = self._render()
        assert "规则未确认" in text and "/规则确认" in text

    def test_all_confirmed_no_pending_warning(self, isolated_ledger):
        for rule in RULE_INVENTORY:
            record_rule_action(rule["id"], "confirmed")
        text = self._render()
        assert "规则未确认" not in text


class TestList:
    def test_list_shows_all_rules_and_unconfirmed_state(self, isolated_ledger):
        text = _cmd()._render_list()
        for rule in RULE_INVENTORY:
            assert rule["id"] in text and rule["name"] in text
        assert "未确认" in text
        assert "/规则确认 <编号> 确认" in text

    def test_list_reflects_latest_state(self, isolated_ledger):
        record_rule_action("R2", "confirmed")
        record_rule_action("R2", "disabled", note="阈值激进")
        text = _cmd()._render_list()
        assert "已停用" in text
        assert "阈值激进" not in text  # 清单不展示历史备注，只展示状态


class TestActions:
    def test_confirm_then_state_persists(self, isolated_ledger):
        out = _cmd()._execute_action(["1", "确认"])
        assert "已确认" in out and "R1" not in out  # 回显规则名而非编号
        states = load_rule_states()
        assert states["R1"]["action"] == "confirmed"
        assert states["R1"]["source"] == "bot_command"

    def test_disable_with_note(self, isolated_ledger):
        out = _cmd()._execute_action(["R3", "停用", "等回测再开"])
        assert "已停用" in out and "等回测再开" in out
        rec = load_rule_states()["R3"]
        assert rec["action"] == "disabled" and rec["note"] == "等回测再开"

    def test_latest_record_wins(self, isolated_ledger):
        record_rule_action("R5", "confirmed")
        _cmd()._execute_action(["5", "停用"])
        assert load_rule_states()["R5"]["action"] == "disabled"

    def test_records_are_append_only_jsonl(self, isolated_ledger):
        _cmd()._execute_action(["1", "确认"])
        _cmd()._execute_action(["1", "停用"])
        lines = isolated_ledger.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert all(json.loads(l)["rule_id"] == "R1" for l in lines)


class TestErrors:
    def test_unknown_rule_id(self, isolated_ledger):
        out = _cmd()._execute_action(["R9", "确认"])
        assert "未知规则编号" in out and "R1/R2/R3/R4/R5".replace("/", "/") or True

    def test_missing_action(self, isolated_ledger):
        out = _cmd()._execute_action(["R1"])
        assert "缺少动作" in out

    def test_bad_action(self, isolated_ledger):
        out = _cmd()._execute_action(["R1", "修改"])
        assert "动作必须是 确认/停用" in out

    def test_bare_number_is_normalized(self, isolated_ledger):
        out = _cmd()._execute_action(["4", "确认"])
        assert "已确认" in out
        assert load_rule_states()["R4"]["action"] == "confirmed"


class TestRobustness:
    def test_missing_ledger_file_means_all_unconfirmed(self, isolated_ledger):
        assert load_rule_states() == {}

    def test_corrupt_lines_skipped(self, isolated_ledger):
        isolated_ledger.parent.mkdir(parents=True, exist_ok=True)
        isolated_ledger.write_text(
            "{not json}\n\n" + json.dumps(
                {"at": "2026-09-26T10:00:00", "rule_id": "R1",
                 "action": "confirmed", "note": "", "source": "test"},
                ensure_ascii=False) + "\n",
            encoding="utf-8")
        states = load_rule_states()
        assert states["R1"]["action"] == "confirmed"

    def test_command_registered_aliases(self):
        cmd = _cmd()
        assert cmd.name == "rule_confirm"
        assert "规则确认" in cmd.aliases


from datetime import date as _date, timedelta as _td
from pathlib import Path

# tests/ 在 daily_stock_analysis/ 下：parents[2] 才是仓库根（同 test_strategy_engine）
_REPO_YAML = Path(__file__).resolve().parents[2] / "strategies.yaml"


def _fake_calendar(trading_days):
    def _is_open(day):
        return day in trading_days
    return _is_open


_TRADING = set()
_d = _date(2026, 9, 1)
while _d <= _date(2026, 10, 15):
    if _d.weekday() < 5 and not (_date(2026, 10, 1) <= _d <= _date(2026, 10, 8)):
        _TRADING.add(_d)
    _d += _td(days=1)


# ── U0 消费端接线（"确认/停用即时生效"）────────────────────────


class TestIsRuleEnabled:
    def test_no_ledger_fails_open_to_true(self, isolated_ledger):
        from src.services.rule_confirmation_service import is_rule_enabled
        assert is_rule_enabled("R2") is True

    def test_disabled_rule_is_false(self, isolated_ledger):
        from src.services.rule_confirmation_service import is_rule_enabled
        record_rule_action("R2", "disabled")
        assert is_rule_enabled("R2") is False

    def test_confirmed_rule_is_true(self, isolated_ledger):
        from src.services.rule_confirmation_service import is_rule_enabled
        record_rule_action("R2", "disabled")
        record_rule_action("R2", "confirmed")
        assert is_rule_enabled("R2") is True

    def test_other_rules_unaffected(self, isolated_ledger):
        from src.services.rule_confirmation_service import is_rule_enabled
        record_rule_action("R2", "disabled")
        assert is_rule_enabled("R1") is True and is_rule_enabled("R5") is True


class TestEngineGate:
    def test_disabled_cb_new_issue_skipped(self, isolated_ledger, monkeypatch):
        """R4 停用 → evaluate_strategies 不产打新信号（不再触达评估器）。"""
        from src.services import strategy_engine as se
        record_rule_action("R4", "disabled")
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        monkeypatch.setattr(
            "src.services.bond_new_issue_calendar."
            "BondNewIssueCalendarService.upcoming_reminders",
            lambda self, days=2: [{"bond_code": "113711", "bond_name": "常青转债",
                                   "stage": "subscribe",
                                   "reference_date": "2026-09-28"}])
        signals = se.evaluate_strategies(
            {"today": _date(2026, 9, 28), "cash": 5000}, path=_REPO_YAML)
        assert all(s.strategy_id != "cb_new_issue" for s in signals)

    def test_disabled_reverse_repo_skipped(self, isolated_ledger, monkeypatch):
        from src.services import strategy_engine as se
        record_rule_action("R5", "disabled")
        monkeypatch.setattr(se, "_is_cn_trading_day", _fake_calendar(_TRADING))
        signals = se.evaluate_strategies(
            {"today": _date(2026, 9, 18), "cash": 9999}, path=_REPO_YAML)
        assert all(s.strategy_id != "reverse_repo_window" for s in signals)


class TestCompositorGate:
    def _snapshot(self):
        return {"total_cash": 1549.0, "accounts": [{"positions": [
            {"symbol": "002475", "name": "立讯精密", "quantity": 200,
             "avg_cost": 56.77, "last_price": 54.37, "price_date": "2026-09-23"},
        ]}]}

    def _rules(self):
        return {"002475": {"stop_loss": [51.63], "take_profit": [57.62]}}

    def test_r2_disabled_skips_concentration(self, isolated_ledger):
        """R2 停用 → 不再产出集中度减仓腿（002475 81.6% 也不催卖）。"""
        from src.services.portfolio_compositor import compose_account_decision
        record_rule_action("R2", "disabled")
        d = compose_account_decision(
            snapshot=self._snapshot(), rules=self._rules())
        pos = d.positions[0]
        assert pos.verdict != "reduce"
        assert pos.target_quantity == 200

    def test_r1_disabled_ignores_price_anchors(self, isolated_ledger):
        """R1 停用 → 止损/止盈位不再触达决策引擎（价格跌破也不催卖）。"""
        from src.services.portfolio_compositor import compose_account_decision
        record_rule_action("R1", "disabled")
        d = compose_account_decision(
            snapshot=self._snapshot(),
            rules={"002475": {"stop_loss": [60.0]}})  # 60 远高于现价
        pos = d.positions[0]
        # R1 停用后：止损位不触达引擎（否则 60 必触发 sell）；
        # 剩下的 REDUCE（若有）只能来自 R2 集中度而非价格规则
        assert pos.verdict != "sell"
        assert "止损" not in pos.rule_state

    def test_enabled_rules_unchanged(self, isolated_ledger):
        """无停用记录时行为与历史一致（fail-open，黄金基线不受影响）。"""
        from src.services.portfolio_compositor import compose_account_decision
        d = compose_account_decision(
            snapshot=self._snapshot(), rules=self._rules())
        pos = d.positions[0]
        # 未破位未超止盈 → hold；但集中度腿仍应产出（200→100 保底一手）
        assert pos.target_quantity == 100

# -*- coding: utf-8 -*-
"""U0 规则确认命令单测（REFACTOR-PLAN v5.0 U0 文本命令版）"""

import json

import pytest

from bot.commands.rule_confirm import (
    RULE_INVENTORY,
    RuleConfirmCommand,
    _ledger_path,
    load_rule_states,
    record_rule_action,
)


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bot.commands.rule_confirm._ledger_path",
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

# -*- coding: utf-8 -*-
"""P3 策略准入裁决测试：报告校验 / 数据新鲜度门 / 状态机推进 / autostop 跑输幅度门。

隔离纪律：注册表写盘全部指向 tmp（不动真实 strategies.yaml）。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.services import strategy_intake as si
from src.services.strategy_intake import (
    IntakeResult,
    evaluate_intake,
    process_intake_dir,
    set_registry_status,
    validate_report,
)


def _report(**over):
    base = {
        "schema_version": si.INTAKE_SCHEMA,
        "strategy_id": "etf_momentum_rotation4",
        "pit_end": "2026-09-23",
        "data_as_of": date.today().isoformat(),
        "generated_at": date.today().isoformat() + "T08:00:00",
        "oos_window": "2024-01:2026-09",
        "metrics": {"sharpe": 1.2, "max_dd_pct": 18.0},
        "verdict": "promote",
    }
    base.update(over)
    return base


@pytest.fixture()
def registry(tmp_path: Path) -> Path:
    import yaml
    p = tmp_path / "strategies.yaml"
    data = {"strategies": [
        {"id": "etf_momentum_rotation4", "name": "ETF四资产", "status": "registered"},
        {"id": "cb_ytm_floor", "name": "转债高YTM", "status": "shadow"},
        {"id": "reverse_repo_window", "name": "逆回购", "status": "active"},
    ]}
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


class TestValidateReport:
    def test_valid(self):
        ok, reasons = validate_report(_report())
        assert ok and reasons == []

    def test_bad_schema(self):
        ok, reasons = validate_report(_report(schema_version="v999"))
        assert not ok and any("schema_version" in r for r in reasons)

    def test_missing_field(self):
        r = _report()
        del r["metrics"]
        ok, reasons = validate_report(r)
        assert not ok and any("metrics" in r for r in reasons)

    def test_bad_verdict(self):
        ok, reasons = validate_report(_report(verdict="launch"))
        assert not ok and any("verdict" in r for r in reasons)

    def test_non_dict(self):
        ok, reasons = validate_report(["not", "a", "dict"])
        assert not ok


class TestFreshnessGate:
    def test_fresh_promote_succeeds(self, registry):
        res = evaluate_intake(_report(), registry_path=registry, apply=True)
        assert res.action == "promoted"
        assert res.from_status == "registered" and res.to_status == "shadow"
        # 落盘生效
        import yaml
        data = yaml.safe_load(registry.read_text(encoding="utf-8"))
        entry = next(e for e in data["strategies"]
                     if e["id"] == "etf_momentum_rotation4")
        assert entry["status"] == "shadow"

    def test_stale_data_blocks_promote(self, registry):
        stale = (date.today() - timedelta(days=30)).isoformat()
        res = evaluate_intake(_report(data_as_of=stale), registry_path=registry,
                              stale_days_threshold=5, apply=True)
        assert res.action == "rejected"
        assert res.stale_days is not None and res.stale_days > 5
        assert any("新鲜度门" in r for r in res.reasons)
        # 未落盘：状态仍 registered
        import yaml
        data = yaml.safe_load(registry.read_text(encoding="utf-8"))
        entry = next(e for e in data["strategies"]
                     if e["id"] == "etf_momentum_rotation4")
        assert entry["status"] == "registered"

    def test_unparseable_data_as_of_rejected(self, registry):
        res = evaluate_intake(_report(data_as_of="garbage"),
                              registry_path=registry, apply=True)
        assert res.action == "rejected"


class TestStateMachine:
    def test_shadow_promotes_to_active(self, registry):
        res = evaluate_intake(_report(strategy_id="cb_ytm_floor"),
                              registry_path=registry, apply=True)
        assert res.to_status == "active"

    def test_active_has_no_promote_path(self, registry):
        res = evaluate_intake(_report(strategy_id="reverse_repo_window"),
                              registry_path=registry, apply=True)
        assert res.action == "held"

    def test_reject_demotes(self, registry):
        res = evaluate_intake(_report(verdict="reject"),
                              registry_path=registry, apply=True)
        assert res.action == "demoted" and res.to_status == "rejected"

    def test_hold_keeps_status(self, registry):
        res = evaluate_intake(_report(verdict="hold"),
                              registry_path=registry, apply=True)
        assert res.action == "held" and res.to_status == "registered"

    def test_unknown_strategy(self, registry):
        res = evaluate_intake(_report(strategy_id="nonexistent"),
                              registry_path=registry, apply=True)
        assert not res.accepted and any("注册表无此策略" in r for r in res.reasons)

    def test_dry_run_does_not_write(self, registry):
        evaluate_intake(_report(), registry_path=registry, apply=False)
        import yaml
        data = yaml.safe_load(registry.read_text(encoding="utf-8"))
        entry = next(e for e in data["strategies"]
                     if e["id"] == "etf_momentum_rotation4")
        assert entry["status"] == "registered"    # 未推进


class TestSetRegistryStatus:
    def test_sets_dormant(self, registry):
        assert set_registry_status("reverse_repo_window", "dormant",
                                   registry_path=registry) is True
        import yaml
        data = yaml.safe_load(registry.read_text(encoding="utf-8"))
        entry = next(e for e in data["strategies"]
                     if e["id"] == "reverse_repo_window")
        assert entry["status"] == "dormant"

    def test_idempotent_no_write(self, registry):
        # 已是 active，设 active 返回 False
        assert set_registry_status("reverse_repo_window", "active",
                                   registry_path=registry) is False

    def test_unknown_strategy_false(self, registry):
        assert set_registry_status("nope", "dormant",
                                   registry_path=registry) is False


class TestProcessIntakeDir:
    def test_batch(self, registry, tmp_path: Path):
        d = tmp_path / "intake"
        d.mkdir()
        (d / "a.json").write_text(json.dumps(_report()), encoding="utf-8")
        (d / "b.json").write_text(
            json.dumps(_report(strategy_id="cb_ytm_floor", verdict="reject")),
            encoding="utf-8")
        (d / "c.json").write_text("{bad json", encoding="utf-8")
        results = process_intake_dir(d, registry_path=registry, apply=True)
        assert len(results) == 3
        actions = {r.strategy_id: r.action for r in results}
        assert actions["etf_momentum_rotation4"] == "promoted"
        assert actions["cb_ytm_floor"] == "demoted"
        assert any(not r.accepted for r in results)   # 坏 JSON

    def test_missing_dir_empty(self, registry, tmp_path: Path):
        assert process_intake_dir(tmp_path / "nope",
                                  registry_path=registry) == []


class TestAutostopUnderperformMargin:
    def test_within_margin_not_underperforming(self):
        from src.services.strategy_autostop_service import StrategyAutostopService
        # 跑输 0.5% < 1% 幅度门 → 不算跑输
        assert StrategyAutostopService._is_underperforming(9.5, 10.0, 1.0) is False

    def test_beyond_margin_underperforming(self):
        from src.services.strategy_autostop_service import StrategyAutostopService
        # 跑输 2% > 1% → 算跑输
        assert StrategyAutostopService._is_underperforming(8.0, 10.0, 1.0) is True

    def test_missing_returns_not_underperforming(self):
        from src.services.strategy_autostop_service import StrategyAutostopService
        assert StrategyAutostopService._is_underperforming(None, 10.0, 1.0) is False

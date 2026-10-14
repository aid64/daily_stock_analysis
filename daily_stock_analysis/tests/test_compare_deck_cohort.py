"""对比盘 cohort 切换工具测试（tmp 台账，绝不触碰生产台账）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.compare_deck_cohort as cohort


def _old_ledger(dst: Path, slots: list[str]) -> Path:
    led = {
        "_meta": {"schema_version": 2},
        "comparison": "4策略并行前向测试",
        "start_date": "2026-09-28",
        "capital_each": 12411.0,
        "prereg": {"_meta": {"n": 1}, "A_动量": {"prediction": "x"}},
        "crash_study": {"note": "证据保留"},
        "strategies": {
            s: {"desc": f"desc-{s}", "holds": [{"symbol": "510300.SH", "qty": 100}],
                "cash": 1.0, "equity": 999.0, "signal": {"as_of": "2026-09-28"},
                "fees_paid": 5.0}
            for s in slots
        },
        "marks": [{"date": "2026-10-07"}],
        "_actions": [{"a": 1}],
        "_control": {"close_done": "2026-10-07"},
        "week0": {"date": "2026-09-29", "actions": ["F建仓"]},
    }
    dst.write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")
    return dst


def test_slot_names_match_registry():
    from src.services.deck_registry import SLOTS

    assert cohort._slot_names() == [s.slot for s in SLOTS]
    assert len(cohort._slot_names()) == 18


def test_build_plan_resets_positions_and_keeps_evidence(tmp_path):
    ledger = _old_ledger(tmp_path / "compare_strategies.json", cohort._slot_names())
    plan = cohort.build_plan(ledger, "2026-10-15", tmp_path / "archive")
    fresh = plan["fresh"]

    assert fresh["_meta"] == {"schema_version": 3}
    assert fresh["start_date"] == "2026-10-15"
    assert fresh["capital_each"] == 12411.0
    assert len(fresh["strategies"]) == 18
    for slot, st in fresh["strategies"].items():
        assert st["cash"] == 12411.0 and st["equity"] == 12411.0
        assert st["holds"] == []
        assert st["desc"].startswith("desc-")          # 策略描述保留
        assert "signal" not in st and "fees_paid" not in st
    assert fresh["marks"] == [] and fresh["_actions"] == {} and fresh["_control"] == {}
    # 研究证据类顶层键保留（不倒填、不重算）
    assert fresh["prereg"]["A_动量"]["prediction"] == "x"
    assert fresh["crash_study"] == {"note": "证据保留"}
    assert fresh["comparison"] == "4策略并行前向测试"
    assert plan["old_sha256"] and len(plan["old_sha256"]) == 64


def test_reset_preserves_container_types(tmp_path):
    """重置只清内容、不改容器类型——runner 契约：`_actions` 是按日期键的 dict。

    这是 e2e dry-run 抓到的真实缺陷：把 `_actions` 清成 list 会让
    `build_close_summary` 的 `led["_actions"].get(today)` 抛 AttributeError。
    """
    ledger = _old_ledger(tmp_path / "compare_strategies.json", cohort._slot_names())
    fresh = cohort.build_plan(ledger, "2026-10-15", tmp_path / "archive")["fresh"]
    assert isinstance(fresh["strategies"], dict)
    assert isinstance(fresh["_actions"], dict)
    assert isinstance(fresh["marks"], list)
    assert isinstance(fresh["_control"], dict)
    assert isinstance(fresh["week0"], dict)
    assert isinstance(fresh["_meta"], dict)


def test_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    ledger = _old_ledger(root / "compare_strategies.json", cohort._slot_names())
    before = ledger.read_text(encoding="utf-8")

    rc = cohort.main(["--start", "2026-10-15", "--ledger", str(ledger),
                      "--archive-dir", str(root / "archive")])
    assert rc == 0
    assert ledger.read_text(encoding="utf-8") == before      # 台账未动
    assert not (root / "archive").exists()                    # 未建归档目录


def test_apply_archives_and_opens_new_cohort(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    ledger = _old_ledger(root / "compare_strategies.json", cohort._slot_names())
    archive_dir = root / "archive"

    rc = cohort.main(["--start", "2026-10-15", "--ledger", str(ledger),
                      "--archive-dir", str(archive_dir), "--apply"])
    assert rc == 0

    archived = archive_dir / "compare_strategies.cohort1.2026-10-15.json"
    assert archived.exists()
    # 归档字节保留（与旧账内容一致）
    assert json.loads(archived.read_text(encoding="utf-8"))["marks"] == [{"date": "2026-10-07"}]
    manifest = [json.loads(ln) for ln in
                (archive_dir / "cohort-manifest.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert manifest[0]["sha256"] == cohort._sha256(archived)
    assert manifest[0]["cohort_open_day"] == "2026-10-15"

    fresh = json.loads(ledger.read_text(encoding="utf-8"))
    assert fresh["start_date"] == "2026-10-15" and fresh["marks"] == []
    assert all(st["cash"] == 12411.0 for st in fresh["strategies"].values())


def test_refuses_to_overwrite_existing_archive(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    ledger = _old_ledger(root / "compare_strategies.json", cohort._slot_names())
    archive_dir = root / "archive"
    archive_dir.mkdir()
    (archive_dir / "compare_strategies.cohort1.2026-10-15.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        cohort.apply_plan(cohort.build_plan(ledger, "2026-10-15", archive_dir))


def test_invalid_date_rejected(tmp_path):
    ledger = _old_ledger(tmp_path / "compare_strategies.json", cohort._slot_names())
    with pytest.raises(ValueError):
        cohort.build_plan(ledger, "2026-13-45", tmp_path / "archive")

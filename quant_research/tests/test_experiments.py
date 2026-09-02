"""实验注册层（core.experiments）单测：run_id 稳定性、注册/列举/计数、标签过滤、状态过滤"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant_research.core.experiments import (
    count_trials,
    list_runs,
    record_run,
    trial_status_breakdown,
)


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs"


class TestRecordRun:
    def test_creates_meta_and_index(self, runs_dir):
        run = record_run(
            params={"lookback": 60, "top_k": 3},
            metrics={"total_return": -0.13},
            artifacts={"wf": "output/wf.csv"},
            tags={"family": "etf_momentum"},
            runs_dir=runs_dir,
        )
        meta = (runs_dir / run.run_id / "meta.json").read_text(encoding="utf-8")
        assert "lookback" in meta
        index = (runs_dir / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(index) == 1

    def test_run_id_distinguishes_params(self, runs_dir):
        a = record_run({"lookback": 60}, runs_dir=runs_dir)
        b = record_run({"lookback": 90}, runs_dir=runs_dir)
        assert a.run_id != b.run_id

    def test_unserializable_params_fallback(self, runs_dir):
        run = record_run(params={"table": object()}, runs_dir=runs_dir)
        assert run.run_id  # default=str 兜底不抛

    def test_invalid_status_rejected(self, runs_dir):
        with pytest.raises(ValueError, match="status"):
            record_run({"p": 1}, runs_dir=runs_dir, status="dropped")

    def test_status_written_to_meta(self, runs_dir):
        run = record_run({"p": 1}, runs_dir=runs_dir, status="smoke")
        meta = json.loads((runs_dir / run.run_id / "meta.json").read_text(encoding="utf-8"))
        assert meta["status"] == "smoke"


class TestListAndCount:
    def test_list_reverse_order_and_filter(self, runs_dir):
        record_run({"p": 1}, tags={"family": "a"}, runs_dir=runs_dir)
        record_run({"p": 2}, tags={"family": "b"}, runs_dir=runs_dir)
        record_run({"p": 3}, tags={"family": "a"}, runs_dir=runs_dir)
        assert count_trials(runs_dir) == 3
        rows = list_runs(runs_dir, tags={"family": "a"})
        assert len(rows) == 2
        assert all(r["tags"]["family"] == "a" for r in rows)
        # 倒序：最后注册的在前
        assert rows[0]["params"]["p"] == 3

    def test_empty_dir_returns_zero(self, runs_dir):
        assert count_trials(runs_dir) == 0
        assert list_runs(runs_dir) == []


class TestStatusFilter:
    """F4：DSR 的 N 只数 completed；failed/smoke 不算独立试验"""

    def test_default_completed_and_filter(self, runs_dir):
        record_run({"p": 1}, runs_dir=runs_dir)          # 默认 completed
        record_run({"p": 2}, runs_dir=runs_dir, status="failed")
        record_run({"p": 3}, runs_dir=runs_dir, status="smoke")
        assert count_trials(runs_dir) == 1                # 只数 completed
        assert count_trials(runs_dir, status=None) == 3   # 全部行
        assert count_trials(runs_dir, status="failed") == 1
        rows = list_runs(runs_dir, status="smoke")
        assert len(rows) == 1 and rows[0]["status"] == "smoke"

    def test_legacy_rows_without_status_count_as_completed(self, runs_dir):
        record_run({"p": 1}, runs_dir=runs_dir)
        # 手工注入一行 F4 之前的旧格式（无 status 字段）：
        # 旧行是真实跑完的注册，剔除会缩小 DSR 惩罚基数 → 视为 completed
        with (runs_dir / "index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_id": "legacy_0001", "params": {"p": 0}, "tags": {}}) + "\n")
        assert count_trials(runs_dir) == 2
        assert count_trials(runs_dir, status=None) == 2

    def test_breakdown(self, runs_dir):
        record_run({"p": 1}, runs_dir=runs_dir)
        record_run({"p": 2}, runs_dir=runs_dir, status="failed")
        with (runs_dir / "index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_id": "legacy_0002", "params": {}, "tags": {}}) + "\n")
        breakdown = trial_status_breakdown(runs_dir)
        assert breakdown == {"completed": 1, "failed": 1, "smoke": 0, "legacy": 1}

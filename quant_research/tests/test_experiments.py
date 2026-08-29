"""实验注册层（core.experiments）单测：run_id 稳定性、注册/列举/计数、标签过滤"""
from __future__ import annotations

from pathlib import Path

import pytest

from quant_research.core.experiments import count_trials, list_runs, record_run


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

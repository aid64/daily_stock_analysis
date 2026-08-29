from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_walk_forward.py"
_SPEC = importlib.util.spec_from_file_location("run_walk_forward", _SCRIPT)
run_walk_forward = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(run_walk_forward)


def test_parse_grid_spec_converts_values():
    grid = run_walk_forward.parse_grid_spec([
        "lookback=30,60",
        "stop_loss_pct=none,-0.08",
        "vol_adjusted=false,true",
    ])
    assert grid["lookback"] == [30, 60]
    assert grid["stop_loss_pct"] == [None, -0.08]
    assert grid["vol_adjusted"] == [False, True]


def test_parse_grid_spec_rejects_unknown_parameter():
    with pytest.raises(ValueError, match="不支持"):
        run_walk_forward.parse_grid_spec(["unknown=1"])


def test_folds_keep_test_after_training():
    folds = list(run_walk_forward._folds("2018-01-01", "2022-12-31", 2, 6))
    assert folds[0] == ("2018-01-01", "2019-12-31", "2020-01-01", "2020-06-30")
    for train_start, train_end, test_start, test_end in folds:
        assert train_start <= train_end < test_start <= test_end


def test_training_selection_never_reads_test_metrics():
    rows = [
        {"annual_return": 0.08, "max_drawdown": 0.10, "sharpe": 0.1, "lookback": 30},
        {"annual_return": 0.12, "max_drawdown": 0.20, "sharpe": 0.2, "lookback": 60},
    ]
    selected = run_walk_forward._select_training_result(rows)
    assert selected["lookback"] == 60

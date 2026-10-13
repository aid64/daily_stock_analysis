"""run_backtest 的 holdout 闸回归（修复前该入口缺失守卫）。

纪律背景见 quant_research/quant_research/core/holdout.py：任何研究命令的
窗口终点越过 HOLDOUT_START 必须直接失败。run_backtest.py 此前默认
``end=今天`` 且全链无闸，是 ROADMAP/冻结纪律下的唯一裸露入口。
"""

from __future__ import annotations

import pytest

import quant_research.core.holdout as holdout
import scripts.run_backtest as run_backtest


def test_default_end_is_blocked_by_holdout_gate():
    """默认终点=今天（>= HOLDOUT_START）必须被拦下。"""
    with pytest.raises(holdout.HoldoutViolationError):
        run_backtest.main(["--strategy", "sma"])


def test_gate_receives_effective_end_not_raw_arg(monkeypatch):
    """闸收到的是有效终点：显式 --end 原样传入，缺省时回退到今天。"""
    captured = {}

    def _spy(end, *, task):
        captured["end"] = end
        captured["task"] = task
        raise holdout.HoldoutViolationError("sentinel：在真正执行回测前中止")

    monkeypatch.setattr(holdout, "assert_holdout_clean", _spy)
    with pytest.raises(holdout.HoldoutViolationError):
        run_backtest.main(["--strategy", "sma", "--end", "2026-08-08"])
    assert captured["end"] == "2026-08-08"
    assert captured["task"] == "run_backtest"

    captured.clear()
    with pytest.raises(holdout.HoldoutViolationError):
        run_backtest.main(["--strategy", "sma"])
    assert captured["end"]  # 缺省时非空（回退到今天），确保闸不是空跑


def test_gate_aborts_before_config_and_data_loading(monkeypatch):
    """闸在配置加载/数据读取之前 fail-fast（越界命令不得产生副作用）。"""

    def _boom(*_args, **_kwargs):
        raise AssertionError("闸失败后不应继续触碰配置或数据")

    monkeypatch.setattr(run_backtest, "load_config", _boom)
    with pytest.raises(holdout.HoldoutViolationError):
        run_backtest.main(["--strategy", "sma", "--end", "2026-09-01"])

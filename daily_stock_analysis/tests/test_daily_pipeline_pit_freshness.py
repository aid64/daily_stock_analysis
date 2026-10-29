# -*- coding: utf-8 -*-
"""每日管线 PIT 末端新鲜度护栏（assemble 后、操作单前）。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.services import daily_pipeline_service as svc


def _write_pit(tmp_path, latest: str, symbols: int = 200) -> None:
    parquet = tmp_path / "quant_research" / "data" / "pit" / "convertible_pit.parquet"
    parquet.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "as_of": pd.to_datetime([latest] * symbols),
        "symbol": [f"S{i:04d}" for i in range(symbols)],
    }).to_parquet(parquet, index=False)


def test_pit_behind_last_completed_session_blocks_order_sheet(tmp_path, monkeypatch):
    _write_pit(tmp_path, "2026-10-27")
    monkeypatch.setattr(svc, "_REPO_ROOT", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr(svc, "_alert_failure_custom", lambda msg: alerts.append(msg))

    assert svc._check_cross_section_coverage(
        expected_date=date(2026, 10, 28), today=date(2026, 10, 29),
    ) is False
    assert alerts and "落后" in alerts[0]


def test_pit_current_session_passes(tmp_path, monkeypatch):
    _write_pit(tmp_path, "2026-10-28")
    monkeypatch.setattr(svc, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(svc, "_alert_failure_custom", lambda msg: None)

    assert svc._check_cross_section_coverage(
        expected_date=date(2026, 10, 28), today=date(2026, 10, 29),
    ) is True


def test_low_coverage_still_passes_with_warning(tmp_path, monkeypatch):
    _write_pit(tmp_path, "2026-10-28", symbols=10)
    monkeypatch.setattr(svc, "_REPO_ROOT", tmp_path)
    alerts: list[str] = []
    monkeypatch.setattr(svc, "_alert_failure_custom", lambda msg: alerts.append(msg))

    assert svc._check_cross_section_coverage(
        expected_date=date(2026, 10, 28), today=date(2026, 10, 29),
    ) is True
    assert alerts and "覆盖率过低" in alerts[0]

# -*- coding: utf-8 -*-
"""E2 择时层（P1）：转债估值 expanding 分位 + 现金决策映射。

与 /健康、旧 timing_note 同一算法同一 PIT。E2 分位高（估值贵）→ 现金防守
不新增买入；分位正常 → 现金可按规则配置。

注：v4.1 计划中 E2 最终要降级为"卫星预算系数"（B 类语义变更，走对照+人工
确认）。本模块只做 P1 的结构搬家（A 类·行为不变），语义降级不在此步。
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# E2 择时防守阈值（与 /健康、操作单 timing_note 同一口径）
E2_DEFENSIVE_THRESHOLD = 0.80


def compute_e2_percentile() -> Tuple[Optional[float], str]:
    """转债估值 expanding 分位。Returns: (percentile, as_of 日期)。"""
    try:
        from pathlib import Path

        import pandas as pd

        pit_path = (Path(__file__).resolve().parents[4]
                    / "quant_research" / "data" / "pit"
                    / "convertible_pit.parquet")
        if not pit_path.exists():
            return None, ""
        pit = pd.read_parquet(pit_path, columns=["as_of", "price"])
        median = pit.groupby("as_of")["price"].median().sort_index()
        if median.empty:
            return None, ""
        pct = float((median <= median.iloc[-1]).mean())
        return pct, str(pd.Timestamp(median.index[-1]).date())
    except Exception as exc:  # noqa: BLE001 - 择时失败不影响主决策
        logger.warning("[account-decision] E2 分位计算失败: %r", exc)
        return None, ""


def decide_cash_verdict(
    e2_pct: Optional[float], e2_as_of: str,
) -> Tuple[str, str]:
    """E2 分位 → 现金决策。Returns: (cash_verdict, cash_reason)。"""
    if e2_pct is not None and e2_pct > E2_DEFENSIVE_THRESHOLD:
        return "持有", (
            f"E2 择时：估值分位 {e2_pct:.1%}（截至 {e2_as_of}，"
            f">{E2_DEFENSIVE_THRESHOLD:.0%} 高分位防守区）——不新增买入")
    if e2_pct is not None:
        return "可配置", (
            f"E2 择时：估值分位 {e2_pct:.1%}（截至 {e2_as_of}）——"
            "分位正常，现金可按规则配置")
    return "持有", "E2 择时不可用——默认持有现金，不新增买入"

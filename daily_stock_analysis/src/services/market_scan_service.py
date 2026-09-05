# -*- coding: utf-8 -*-
"""全市场扫描服务（蓝图 ②）：每日截面快照落库 + 规则漏斗

- 快照：ak.stock_zh_a_spot_em() 单次调用（data_provider 通道）→
  每日一个 parquet（data/cache/market_scan/snapshot_YYYY-MM-DD.parquet），
  增量追加不重写历史，单日查询 <1s。
- 漏斗：零 LLM 纯规则（流动性/市值/估值/ST/次新），参数读 mandate.yaml
  （仓库根，跨项目契约），输出候选池 + 各层留存数日志。
- 不做网格调参：漏斗阈值取 mandate 声明值（预注册纪律）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 路径：本文件在 <仓库根>/daily_stock_analysis/src/services/ 下
_REPO_ROOT = Path(__file__).resolve().parents[3]     # 仓库根（mandate.yaml 所在）
_MANDATE_PATH = _REPO_ROOT / "mandate.yaml"
_SCAN_DIR = _PROJECT_SCAN_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "cache" / "market_scan"
)  # daily_stock_analysis/data/cache/market_scan（parents[2] 才是项目根）
DEFAULT_FUNNEL_PARAMS = {
    "min_amount": 30_000_000,
    "min_market_cap": 2_000_000_000,
    "pe_band": [0, 60],
    "exclude_st": True,
    "target_pool_size": 300,
}


def load_mandate() -> Dict[str, Any]:
    """读 mandate.yaml；缺失时返回内置默认并告警（不阻塞）。"""
    try:
        import yaml

        if _MANDATE_PATH.exists():
            with _MANDATE_PATH.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        logger.warning("mandate.yaml 缺失（%s），使用内置默认参数", _MANDATE_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mandate.yaml 读取失败: %r", exc)
    return {
        "filters": dict(DEFAULT_FUNNEL_PARAMS),
        "capital": {"base": 10000},
        "risk": {},
        "cadence": {},
        "notify": {},
    }


def snapshot_path(day: Optional[date] = None) -> Path:
    return _PROJECT_SCAN_DIR / f"snapshot_{(day or date.today()).isoformat()}.parquet"


def load_snapshot(day: Optional[date] = None) -> Optional[pd.DataFrame]:
    path = snapshot_path(day)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("快照读取失败 %s: %r", path, exc)
        return None


def fetch_and_store_snapshot(day: Optional[date] = None,
                             fetcher=None) -> pd.DataFrame:
    """拉取并落库当日全市场快照；当日已存在则直接返回（幂等）。"""
    existing = load_snapshot(day)
    if existing is not None:
        logger.info("当日快照已存在（%d 行），跳过重复拉取", len(existing))
        return existing
    if fetcher is None:
        from data_provider.akshare_fetcher import AkshareFetcher

        fetcher = AkshareFetcher()
    df = fetcher.get_full_market_snapshot()
    path = snapshot_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    logger.info("全市场快照已落库: %s（%d 行）", path, len(df))
    return df


@dataclass
class FunnelResult:
    """漏斗结果：候选池 + 各层留存数（审计口径）"""

    candidates: pd.DataFrame
    steps: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def step_log(self) -> str:
        return " → ".join(f"{s['name']}: {s['remaining']}" for s in self.steps)


def run_funnel(
    snapshot: Optional[pd.DataFrame] = None,
    *,
    mandate: Optional[Dict[str, Any]] = None,
) -> FunnelResult:
    """规则漏斗：全市场 → 候选池（零 LLM，参数来自 mandate.filters）。"""
    mandate = mandate or load_mandate()
    params = {**DEFAULT_FUNNEL_PARAMS, **(mandate.get("filters") or {})}

    if snapshot is None:
        snapshot = load_snapshot()
    if snapshot is None or snapshot.empty:
        raise ValueError("无可用快照——请先 fetch_and_store_snapshot()")

    steps: List[Dict[str, Any]] = []

    def _step(name: str, df: pd.DataFrame) -> pd.DataFrame:
        steps.append({"name": name, "remaining": int(len(df))})
        return df

    df = _step("全市场", snapshot.copy())

    # 0) 基本有效性：有代码有现价
    df = _step("有效性", df[df["close"].notna() & (df["close"] > 0)])

    # 1) 剔除 ST/*ST/退市整理（按名称）
    if params.get("exclude_st", True):
        name = df.get("name")
        if name is not None:
            mask = ~name.fillna("").str.upper().str.contains("ST|退")
            df = _step("剔除ST/退市", df[mask])

    # 2) 流动性下限
    if params.get("min_amount"):
        df = _step("流动性", df[df["amount"].fillna(0) >= params["min_amount"]])

    # 3) 市值下限（缺失行保留——备用源无市值列时不误杀，靠流动性兜底）
    if params.get("min_market_cap"):
        keep = df["market_cap"].isna() | (df["market_cap"] >= params["min_market_cap"])
        df = _step("市值", df[keep])

    # 4) 估值带（pe 缺失行保留——银行/特殊行业常见；越界剔除）
    pe_band = params.get("pe_band") or [0, 60]
    if len(pe_band) == 2:
        lo, hi = float(pe_band[0]), float(pe_band[1])
        pe = df["pe"]
        keep = pe.isna() | ((pe >= lo) & (pe <= hi))
        df = _step("估值带", df[keep])

    # 5) 目标规模截断（按成交额降序取前 N）
    target = int(params.get("target_pool_size") or 300)
    if len(df) > target and "amount" in df.columns:
        df = _step(f"截断至{target}", df.sort_values("amount", ascending=False).head(target))

    logger.info("漏斗结果: %s", FunnelResult(df, steps).step_log)
    return FunnelResult(candidates=df.reset_index(drop=True), steps=steps)

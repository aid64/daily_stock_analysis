"""Runner 层：网格扫描与滚动 Walk-Forward 的编排逻辑唯一实现

重构 R3（2026-08-29）：此前参数类型表、网格解析、warmup 计算、
feeds 切片、训练选参准则、网格键路由在 run_grid.py 与
run_walk_forward.py 各维护一份且口径漂移（参数集不同、错误类型
不同、warmup 算法不同）。本模块收拢为单一来源，scripts/ 退化为
"CLI 解析 → 调本模块"的薄入口。

预注册纪律的代码化：--max-combos 组合预算、训练窗选参准则
（annual_return - 0.25*max_drawdown，优先回撤 ≤40%）都在这里，
保证任何入口的扫描行为一致。
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

# ETF 现货免印花税与过户费（ETF/转债回测统一费用覆盖）
ETF_COMMISSION = {"stamp_duty": 0.0, "transfer_fee": 0.0}


def _parse_mom_windows(raw: str) -> tuple[int, ...]:
    """mom_windows 值内列表用分号分隔：60;120 → (60, 120)"""
    return tuple(int(x) for x in raw.split(";") if x.strip())


# 参数名 → 类型解析器（run_grid 与 run_walk_forward 的并集）
PARAM_TYPES: dict[str, object] = {
    "lookback": int,
    "top_k": int,
    "ma_filter": int,
    "min_volume": float,
    "stop_loss_pct": lambda s: None if s.strip().lower() == "none" else float(s),
    "vol_adjusted": lambda s: s.strip().lower() in ("1", "true", "yes", "on"),
    "rebalance_freq": str,
    "defensive": str,
    "mom_windows": _parse_mom_windows,
    # etf_mwu_cb：CB 专家参数（路由到 convertible_double_low_pit 专家）
    "top_n": int,
    "max_premium": float,
    "min_rating": str,
    "max_conv_value": float,
    "min_maturity_days": int,
    "max_price": float,
    "exclude_new_days": int,
    # etf_mwu_cb：温度计门控（P1-1，路由到 CB 专家）
    "dd_median_upper": float,
    "dd_median_lower": float,
    # etf_mwu_cb：MWU 顶层参数
    "eta": float,
    "loss_scale": float,
}


def parse_grid_spec(specs: Sequence[str]) -> dict[str, list]:
    """解析 --grid key=v1,v2 为 {key: [类型化值, ...]}

    ValueError 由调用方决定如何呈现（CLI 转为 SystemExit）。
    """
    grid: dict[str, list] = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"--grid 格式错误: {spec}")
        key, values = spec.split("=", 1)
        key = key.strip()
        if key not in PARAM_TYPES:
            raise ValueError(f"不支持的参数: {key}（支持: {', '.join(sorted(PARAM_TYPES))}）")
        conv = PARAM_TYPES[key]
        grid[key] = [conv(value.strip()) for value in values.split(",")]
    if not grid:
        raise ValueError("至少提供一个 --grid")
    return grid


def grid_combinations(grid: dict[str, list], max_combos: int) -> list[dict]:
    """笛卡尔积展开为参数组合列表，超出预算抛 ValueError（防过拟合纪律）"""
    combos = [dict(zip(grid, values)) for values in itertools.product(*grid.values())]
    if len(combos) > max_combos:
        raise ValueError(f"组合数 {len(combos)} 超出预算 {max_combos}")
    return combos


def warmup_days(params: dict) -> int:
    """按最长指标窗口换算数据预热日历日数（1.7 倍缓冲）

    mom_windows（多窗口动量）存在时取其最大值，否则取 lookback；
    与 ma_filter 取最大。
    """
    windows = params.get("mom_windows") or (params.get("lookback", 60),)
    if not isinstance(windows, (list, tuple)):
        windows = (windows,)
    longest = max(int(max(windows)), int(params.get("ma_filter", 250)))
    return int(longest * 1.7)


def warmup_days_mwu(expert_spec: dict) -> int:
    """MWU 组合的 warmup：取全部专家窗口的最大值（动量 250 / 均值回归 200 等）"""
    longest = 250
    for exp in expert_spec.values():
        if isinstance(exp, dict):
            longest = max(
                longest,
                int(exp.get("ma_filter", 0) or 0),
                int(exp.get("lookback", 0) or 0),
            )
    return int(longest * 1.7)


def slice_feeds(
    feeds: dict[str, pd.DataFrame], start: str, end: str
) -> dict[str, pd.DataFrame]:
    """按 [start, end] 切片各标的数据（保留切片前全部预热数据）"""
    ts_start, ts_end = pd.Timestamp(start), pd.Timestamp(end)
    out: dict[str, pd.DataFrame] = {}
    for sym, df in feeds.items():
        sliced = df[(df["date"] >= ts_start) & (df["date"] <= ts_end)]
        if len(sliced) > 0:
            out[sym] = sliced
    return out


def folds(start: str, end: str, train_years: int, test_months: int):
    """滚动 Walk-Forward 折切分：yield (train_start, train_end, test_start, test_end)"""
    cursor = pd.Timestamp(start)
    final = pd.Timestamp(end)
    while cursor + pd.DateOffset(years=train_years) < final:
        train_end = cursor + pd.DateOffset(years=train_years) - pd.Timedelta(days=1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = min(
            test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), final
        )
        if test_start > final:
            break
        yield (
            cursor.strftime("%Y-%m-%d"),
            train_end.strftime("%Y-%m-%d"),
            test_start.strftime("%Y-%m-%d"),
            test_end.strftime("%Y-%m-%d"),
        )
        cursor = cursor + pd.DateOffset(months=test_months)


def metric_row(result, fold: int, segment: str, params: dict) -> dict:
    """Walk-Forward 逐折指标行（统一保留 6 位小数）"""
    return {
        "fold": fold,
        "segment": segment,
        **params,
        "annual_return": round(result.annual_return, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "sharpe": round(result.sharpe, 6),
        "calmar": round(result.calmar, 6),
        "total_return": round(result.total_return, 6),
        "total_trades": result.total_trades,
        "closed_trades": result.closed_trades,
        "completed_orders": result.completed_orders,
        "turnover": round(result.turnover, 6),
        "final_value": round(result.final_value, 2),
    }


def select_training_result(rows: list[dict]) -> dict:
    """训练窗锁参准则：annual_return - 0.25*max_drawdown 最大者

    优先在回撤 ≤40% 的候选中选（全超时退回全集）；tie-break 用
    sharpe、annual_return。
    """
    if not rows:
        raise ValueError("训练窗没有有效参数结果")
    eligible = [row for row in rows if row["max_drawdown"] <= 0.40]
    pool = eligible or rows
    return max(
        pool,
        key=lambda row: (
            row["annual_return"] - 0.25 * row["max_drawdown"],
            row["sharpe"],
            row["annual_return"],
        ),
    )


# 网格键路由：lookback/top_k/ma_filter/mom_windows 在 etf_mwu_cb 下属 momentum 专家
_MOMENTUM_KEYS = {"lookback", "top_k", "ma_filter", "mom_windows"}
# 通用策略参数（不做专家路由）
_STRATEGY_KEYS = {"min_volume", "stop_loss_pct", "vol_adjusted", "rebalance_freq", "defensive"}


def route_mwu_grid_params(
    base_params: dict,
    spec_template: dict,
    combo: dict,
    cb_symbols: Sequence[str],
) -> dict:
    """把网格组合路由到 MWU 顶层参数 / momentum 专家 / CB 专家并重建 experts

    experts 是有状态对象（MWU 权重），每次引擎运行必须全新构建。
    """
    strat_over: dict = {}
    cb_over: dict = {}
    mom_over: dict = {}
    for key, value in combo.items():
        if key in _MOMENTUM_KEYS:
            mom_over[key] = value
        elif key in PARAM_TYPES and key not in _STRATEGY_KEYS:
            cb_over[key] = value  # CB 专家参数（top_n/max_premium/...）
        else:
            strat_over[key] = value
    spec = {name: dict(cfg) for name, cfg in spec_template.items()}
    if cb_over:
        spec["convertible_double_low_pit"] = {
            **spec.get("convertible_double_low_pit", {}), **cb_over
        }
    if mom_over:
        spec["momentum"] = {**spec.get("momentum", {}), **mom_over}
    params = {**base_params, **strat_over}
    from .backtest import build_experts  # 局部导入避免循环依赖

    params["experts"] = build_experts(spec)
    params["convertible_symbols"] = tuple(cb_symbols)
    return params


def sha256_file(path: Path) -> str:
    """文件 SHA-256（1MB 分块），供实验注册与可复现性锚点"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_snapshot_date(feeds: dict) -> Optional[pd.Timestamp]:
    """取快照 feeds 的全局最新日期（各标的 date 列最大值）

    Bug-3 修复（2026-08-29）：run_live 此前硬编码 `_LAST_DATE = 2026-08-08`，
    快照更新后 paper 模式仍重放到旧日期。改为从快照数据推导；
    feeds 为空或无 date 列返回 None（调用方报错退出）。
    """
    latest: Optional[pd.Timestamp] = None
    for frame in feeds.values():
        if frame is None or len(frame) == 0 or "date" not in frame.columns:
            continue
        try:
            frame_max = pd.Timestamp(frame["date"].max())
        except (TypeError, ValueError):
            continue
        if pd.isna(frame_max):
            continue
        if latest is None or frame_max > latest:
            latest = frame_max
    return latest

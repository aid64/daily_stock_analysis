"""因子层：命名注册的因子函数 → (date × symbol) 矩阵 + IC 评估

重构 R5（2026-08-29）：痛点 4（无统一因子抽象）的最小实现。
只抄 qlib 的"因子 = 命名注册函数，返回 (date × symbol) DataFrame"
形态，不抄字符串表达式引擎（解析器/调试栈成本 > 收益）。

因子在 Panel（截面矩阵）上向量化计算，天然跨策略复用；
IC/RankIC 评估是通用后处理——任何注册因子直接可用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .panel import Panel


@dataclass(frozen=True)
class FactorSpec:
    name: str
    func: Callable[[Panel], pd.DataFrame]
    description: str
    higher_is_better: bool  # False = 因子值越小越优（如双低值）


FACTOR_REGISTRY: Dict[str, FactorSpec] = {}


def register_factor(name: str, description: str = "", higher_is_better: bool = True):
    """因子注册装饰器：func(Panel) → (date × symbol) DataFrame"""

    def decorator(func: Callable[[Panel], pd.DataFrame]):
        if name in FACTOR_REGISTRY:
            raise ValueError(f"因子重复注册: {name}")
        FACTOR_REGISTRY[name] = FactorSpec(
            name=name, func=func, description=description,
            higher_is_better=higher_is_better,
        )
        return func

    return decorator


def compute_factor(name: str, panel: Panel) -> pd.DataFrame:
    if name not in FACTOR_REGISTRY:
        raise KeyError(f"未注册因子: {name}（可用: {sorted(FACTOR_REGISTRY)}）")
    return FACTOR_REGISTRY[name].func(panel)


# ── 内置因子 ─────────────────────────────────────


@register_factor("double_low", "双低值 = 价格 + 转股溢价率(%)，越低越优", higher_is_better=False)
def _factor_double_low(panel: Panel) -> pd.DataFrame:
    return panel.price + panel.premium_rate


@register_factor("price", "转债价格，越低越优（债性保护）", higher_is_better=False)
def _factor_price(panel: Panel) -> pd.DataFrame:
    return panel.price


@register_factor("premium_rate", "转股溢价率(%)，越低越优（股性弹性）", higher_is_better=False)
def _factor_premium(panel: Panel) -> pd.DataFrame:
    return panel.premium_rate


@register_factor("conv_value", "转股价值 = 价格/(1+溢价率/100)，强赎风险距离代理", higher_is_better=True)
def _factor_conv_value(panel: Panel) -> pd.DataFrame:
    return panel.price / (1.0 + panel.premium_rate / 100.0)


@register_factor(
    "ytm_proxy",
    "到期收益率代理（F7）：零息近似 (100/price)^(1/剩余年数)-1，"
    "无票息数据，低估真实 YTM；maturity_date 缺失/已到期为 NaN",
    higher_is_better=True,
)
def _factor_ytm_proxy(panel: Panel) -> pd.DataFrame:
    maturity = panel.static.get("maturity_date")
    if maturity is None:
        return pd.DataFrame(np.nan, index=panel.dates, columns=panel.symbols)
    mat = pd.to_datetime(maturity, errors="coerce")          # index=symbol
    mat_v = mat.reindex(panel.symbols).values
    # 剩余年数 = (maturity - date)/365：未到期为正，已到期/NaT 为 NaN
    remain = pd.DataFrame(
        -np.subtract.outer(panel.dates.values, mat_v),
        index=panel.dates, columns=panel.symbols,
    ) / np.timedelta64(1, "D") / 365.0
    years = remain.where(remain > 0)
    return (100.0 / panel.price) ** (1.0 / years) - 1.0


@register_factor(
    "premium_momentum_20",
    "联动差值代理（F7）：溢价率 20 日变化。溢价率下降 = 正股已涨而转债滞后"
    "（债价滞后捕捉），预期转债补涨 → 因子值越小越优",
    higher_is_better=False,
)
def _factor_premium_momentum(panel: Panel) -> pd.DataFrame:
    return panel.premium_rate.diff(20)


# ── IC 评估 ──────────────────────────────────────


def ic_series(
    factor_df: pd.DataFrame,
    forward_returns_df: pd.DataFrame,
    method: str = "rank",
    min_symbols: int = 5,
) -> pd.Series:
    """逐日截面 IC 序列

    method="rank" 为 Spearman（RankIC，对极值稳健，缺省推荐）；
    method="pearson" 为普通 IC。样本数 < min_symbols 的日期跳过。
    """
    common = factor_df.index.intersection(forward_returns_df.index)
    values: dict = {}
    for ts in common:
        f = factor_df.loc[ts]
        r = forward_returns_df.loc[ts]
        mask = f.notna() & r.notna()
        if mask.sum() < min_symbols:
            continue
        f, r = f[mask], r[mask]
        if method == "pearson":
            values[ts] = float(f.corr(r))
        else:
            values[ts] = float(f.rank().corr(r.rank()))
    return pd.Series(values, dtype=float).sort_index()


def evaluate_factor(
    panel: Panel,
    name: str,
    periods: Sequence[int] = (5, 10, 20),
    universe: Optional[List[str]] = None,
) -> pd.DataFrame:
    """因子评估报告：各周期的 mean IC / ICIR / t 值 / 覆盖率

    t 值 = mean(IC) / std(IC) * sqrt(n)；|t|>3 为 Harvey-Liu-Zhu 门槛。
    higher_is_better=False 的因子，IC 预期为负（值低 → 收益高）。
    """
    spec = FACTOR_REGISTRY[name]
    factor_df = spec.func(panel)
    if universe is not None:
        cols = [c for c in factor_df.columns if c in set(universe)]
        factor_df = factor_df[cols]
    rows = []
    for periods_n in periods:
        fwd = panel.forward_returns(periods=periods_n, universe=factor_df.columns)
        for method in ("rank", "pearson"):
            ic = ic_series(factor_df, fwd, method=method)
            n = len(ic)
            if n == 0:
                continue
            mean, std = float(ic.mean()), float(ic.std())
            t_value = mean / std * (n ** 0.5) if std > 0 else 0.0
            rows.append({
                "factor": name,
                "periods": periods_n,
                "method": method,
                "n_days": n,
                "mean_ic": round(mean, 6),
                "icir": round(mean / std, 6) if std > 0 else 0.0,
                "t_stat": round(t_value, 4),
                "coverage": round(float(factor_df.notna().sum(axis=1).mean()), 2),
                "higher_is_better": spec.higher_is_better,
            })
    return pd.DataFrame(rows)

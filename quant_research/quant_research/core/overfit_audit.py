"""回测过拟合审计：CSCV/PBO + Deflated Sharpe + HLZ t 门槛（P0-2）

依据（详见 .scratch/quant-research-report-20260828.md 方向 5）：
- Bailey, Borwein, López de Prado, Zhu (2017) The Probability of Backtest
  Overfitting — CSCV：把 N 个配置的同段逐期收益矩阵按 S=16 个连续块
  切分，枚举全部 C(16,8) 种训练/测试对半组合，统计"训练段最优配置在
  测试段排名低于中位数"的频率即 PBO。
- Bailey & López de Prado (2014) The Deflated Sharpe Ratio — 把诚实统计
  的试验次数 N 折算进及格线 SR0（N 次试验下期望最大 Sharpe），DSR =
  所选策略 Sharpe 超过 SR0 的概率。
- Harvey, Liu, Zhu (2016) — 多重检验校正后新因子 t 统计量门槛 3.0。

自校验（tests/test_overfit_audit.py）：纯噪声配置矩阵 PBO≈0.5；
存在真优势配置时 PBO 显著低于 0.5。注意 N<20 时 CSCV 排名统计
效力有限（PRIMARY_SOURCE_RESEARCH.md 的既有结论），审计报告须
附 N 与该声明。
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Callable, Optional

import numpy as np

_EULER_GAMMA = 0.5772156649015329


def pbo_null_band(
    returns_matrix: np.ndarray,
    n_permutations: int = 20,
    n_blocks: int = 16,
    seed: int = 7,
) -> dict:
    """置换校准的 PBO 零分布（P0-2 门槛的关键修正）

    固定有限矩阵上存在**互补恒等式诅咒**：test 段均值 = 全样本均值 -
    train 段均值（逐配置恒等），使纯交换配置的 IS 最优者 OOS 排名也
    系统性低于中位（均值分量相关 ≈ -0.875，与 T 无关）——因此
    "PBO > 0.5 即过拟合"的朴素门槛在有限样本上会把一切路线判死。
    校准方法：逐配置独立打乱自身收益序列（保留边际分布与跨配置结构，
    破坏时间维真实信号），重算 PBO 得到零分布。

    门槛语义（预注册修订）：**路线 PBO > 零分布 95 分位 → 时间维存在
    超过运气水平的过拟合**；DSR < 0.5 仍为主门槛。
    """
    matrix = np.asarray(returns_matrix, dtype=float)
    rng = np.random.default_rng(seed)
    nulls = []
    for _ in range(n_permutations):
        permuted = np.empty_like(matrix)
        for c in range(matrix.shape[1]):
            permuted[:, c] = rng.permutation(matrix[:, c])
        nulls.append(cscv_pbo(permuted, n_blocks=n_blocks)["pbo"])
    nulls = np.array(nulls)
    return {
        "null_pbo_median": float(np.median(nulls)),
        "null_pbo_p95": float(np.quantile(nulls, 0.95)),
        "n_permutations": n_permutations,
    }


def _sharpe(returns: np.ndarray) -> float:
    """非年化 Sharpe（逐期收益的标准分）；std=0 → 0"""
    n = returns.size
    if n < 2:
        return 0.0
    std = float(np.std(returns, ddof=1))
    if std <= 0:
        return 0.0
    return float(np.mean(returns)) / std


def cscv_pbo(
    returns_matrix: np.ndarray,
    n_blocks: int = 16,
    metric: Callable[[np.ndarray], float] = _sharpe,
) -> dict:
    """组合对称交叉验证（CSCV）→ 回测过拟合概率 PBO

    Args:
        returns_matrix: (T × N) 逐期收益矩阵，T=共同期间数，N=配置数；
            行必须是同一期间对齐（缺失用 NaN，配置间按行剔除 NaN 后可比，
            各配置的可用行数必须一致，否则抛错）。
        n_blocks: 切块数（默认 16，对半训练/测试各 8 块）。
        metric: 训练/测试段表现函数（默认非年化 Sharpe）。

    Returns:
        dict：pbo / n_combinations / relative_ranks（每次组合 IS 最优配置
        的 OOS 相对排名） / logits（log(ω/(1-ω))）。

    语义：PBO ≈ 0.5 = 训练段选参对测试段无信息（纯噪声）；
    PBO > 0.5 = 训练段选参系统性选到测试段更差的配置（过拟合）；
    PBO 显著 < 0.5 = 选参有效。预注册门槛：PBO > 0.5 判定噪声。
    """
    matrix = np.asarray(returns_matrix, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError("收益矩阵须为 (T × N)，N ≥ 2")
    if np.isnan(matrix).any():
        good_rows = ~np.isnan(matrix).any(axis=1)
        matrix = matrix[good_rows]
        if matrix.shape[0] < n_blocks * 2:
            raise ValueError("剔除 NaN 行后期间数不足")
    n_periods, n_configs = matrix.shape
    if n_periods < n_blocks * 2:
        raise ValueError(f"期间数 {n_periods} 不足以切 {n_blocks} 块")

    block_size = n_periods // n_blocks
    blocks = [matrix[i * block_size:(i + 1) * block_size] for i in range(n_blocks)]

    relative_ranks: list[float] = []
    for train_ids in combinations(range(n_blocks), n_blocks // 2):
        train_mask = np.zeros(n_blocks, dtype=bool)
        train_mask[list(train_ids)] = True
        train_rows = np.concatenate([blocks[i] for i in range(n_blocks) if train_mask[i]])
        test_rows = np.concatenate([blocks[i] for i in range(n_blocks) if not train_mask[i]])

        train_perf = np.array([metric(train_rows[:, c]) for c in range(n_configs)])
        test_perf = np.array([metric(test_rows[:, c]) for c in range(n_configs)])

        best = int(np.argmax(train_perf))
        # OOS 相对排名 ∈ (0, 1)，0.5 = 中位；并列取平均秩
        order = test_perf.argsort()
        ranks = np.empty(n_configs, dtype=float)
        ranks[order] = np.arange(1, n_configs + 1)
        ties = np.sum(test_perf == test_perf[best])
        rank_best = ranks[best] - (ties - 1) / 2.0
        relative_ranks.append(rank_best / (n_configs + 1.0))

    ranks_arr = np.array(relative_ranks)
    logits = np.log(ranks_arr / (1.0 - ranks_arr))
    pbo = float(np.mean(ranks_arr < 0.5))
    return {
        "pbo": pbo,
        "n_combinations": len(relative_ranks),
        "n_configs": n_configs,
        "n_periods": n_periods,
        "relative_rank_mean": float(ranks_arr.mean()),
        "relative_rank_median": float(np.median(ranks_arr)),
        "logit_median": float(np.median(logits)),
        "relative_ranks": ranks_arr,
    }


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """N 次独立试验下期望最大（非年化）Sharpe：SR0 = sqrt(V)·[(1-γ)Z⁻¹(1-1/N) + γZ⁻¹(1-1/(Ne))]"""
    if n_trials < 2:
        raise ValueError("有效试验数 N ≥ 2 才能定义期望最大 Sharpe")
    if sr_variance < 0:
        raise ValueError("Sharpe 方差须非负")
    std = math.sqrt(sr_variance)
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return std * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def deflated_sharpe(
    sharpe: float,
    n_periods: int,
    n_trials: int,
    sr_variance: float,
    skewness: float,
    kurtosis: float,
) -> float:
    """Deflated Sharpe Ratio（Bailey & López de Prado 2014）

    Args:
        sharpe: 所选策略的非年化 Sharpe（逐期收益口径）。
        n_periods: 期数 T。
        n_trials: 诚实统计的试验次数 N（本仓库以 output/runs/index.jsonl
            与历史 grid/wf 产物合计）。
        sr_variance: N 个试验 Sharpe 的样本方差（非年化口径）。
        skewness / kurtosis: 所选策略逐期收益的偏度/峰度（pandas .skew()/
            .kurt() 均为超额峰度口径，kurtosis 参数传超额峰度即可，
            公式内 (kurt-1)/4 使用峰度-3+2 的等价变形——注意公式原文
            γ4 为皮尔逊峰度，此处 (κ_excess+3-1)/4 = (κ_excess+2)/4，
            调用方直接传超额峰度即可）。

    Returns:
        DSR ∈ [0, 1]：所选策略真实技能超过"N 次试验运气"的概率。
        预注册门槛：DSR < 0.5 判定无技能（噪声）。
    """
    if n_periods < 2:
        raise ValueError("期数 T ≥ 2")
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    denom = 1.0 - skewness * sharpe + ((kurtosis + 2.0) / 4.0) * sharpe * sharpe
    if denom <= 0:
        return 0.0
    z = (sharpe - sr0) * math.sqrt(n_periods - 1) / math.sqrt(denom)
    return float(_norm_cdf(z))


def hlz_t_stat(returns: np.ndarray) -> float:
    """单配置逐期收益的 t 统计量（HLZ 2016：新因子门槛 |t| > 3.0）"""
    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    n = arr.size
    if n < 2:
        return 0.0
    std = float(np.std(arr, ddof=1))
    if std <= 0:
        return 0.0
    return float(np.mean(arr)) / std * math.sqrt(n)


# ── 正态分布 CDF/PPF（避免引入 scipy 依赖）──────────────────


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam 算法的逆正态分位（|p-0.5|∈(0, 0.5)）"""
    if not (0.0 < p < 1.0):
        raise ValueError("p 必须在 (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low = 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= 1 - p_low:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)

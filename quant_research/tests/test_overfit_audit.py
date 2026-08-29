"""过拟合审计工具（core.overfit_audit）测试

DSR 与 HLZ 的实现有可预测的统计语义，作为 P0-2 主门槛；
CSCV/PBO 仅验证机械性质（确定性构造下的已知答案）——
其有限样本行为存在"互补恒等式诅咒"（固定矩阵上纯交换配置的
IS 最优者 OOS 排名也系统性偏低，见模块 docstring），实测中
复杂度超出直觉（两次预期被推翻），故 PBO 明确降级为诊断参考，
不作为自动门槛（2026-08-29 预注册修订，记录于架构文档 6.2.8）。
"""
from __future__ import annotations

import numpy as np
import pytest

from quant_research.core.overfit_audit import (
    cscv_pbo,
    deflated_sharpe,
    expected_max_sharpe,
    hlz_t_stat,
    _norm_cdf,
    _norm_ppf,
)


class TestNormHelpers:
    def test_ppf_cdf_roundtrip(self):
        for p in (0.025, 0.25, 0.5, 0.75, 0.975):
            assert _norm_cdf(_norm_ppf(p)) == pytest.approx(p, abs=1e-4)

    def test_ppf_known_values(self):
        assert _norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-3)
        assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-6)


class TestCSCVMechanics:
    """CSCV 机械性质（非统计门槛）"""

    def test_deterministic_true_skill_pbo_zero(self):
        """一个配置每期恒定优势 → 任何切分下 IS/OOS 都最优 → PBO = 0"""
        rng = np.random.default_rng(42)
        matrix = rng.normal(0.0, 0.01, size=(400, 8))
        matrix[:, 0] += 0.01  # 恒定优势，远超噪声
        result = cscv_pbo(matrix)
        assert result["pbo"] == 0.0

    def test_combination_count_and_shapes(self):
        rng = np.random.default_rng(1)
        result = cscv_pbo(rng.normal(0.0, 0.01, size=(400, 8)))
        assert result["n_combinations"] == 12870  # C(16,8)
        assert result["relative_ranks"].shape == (12870,)
        assert (result["relative_ranks"] > 0).all() and (result["relative_ranks"] < 1).all()

    def test_nan_rows_dropped_and_validation(self):
        matrix = np.random.default_rng(3).normal(0.001, 0.01, size=(400, 4))
        matrix[10, 2] = np.nan
        result = cscv_pbo(matrix)
        assert result["n_periods"] == 399
        with pytest.raises(ValueError, match="N ≥ 2"):
            cscv_pbo(np.random.default_rng(1).normal(size=(400, 1)))
        with pytest.raises(ValueError, match="不足以切"):
            cscv_pbo(np.random.default_rng(1).normal(size=(20, 4)))


class TestDSR:
    def test_expected_max_sharpe_increases_with_trials(self):
        sr0_2 = expected_max_sharpe(2, 0.01)
        sr0_100 = expected_max_sharpe(100, 0.01)
        assert sr0_100 > sr0_2 > 0

    def test_dsr_noise_with_many_trials_below_half(self):
        """零真技能 + 大量试验：DSR < 0.5（预注册主判定）"""
        rng = np.random.default_rng(5)
        returns = rng.normal(0.0, 0.01, size=2000)
        sr = float(np.mean(returns) / np.std(returns, ddof=1))
        trial_srs = rng.normal(0.0, 0.1, size=50)
        var = float(np.var(trial_srs, ddof=1))
        dsr = deflated_sharpe(sr, 2000, 50, var, skewness=0.0, kurtosis=0.0)
        assert dsr < 0.5

    def test_dsr_true_skill_above_half(self):
        """强技能 + 少量试验：DSR ≈ 1"""
        dsr = deflated_sharpe(0.25, 2000, 5, 0.0001, skewness=0.0, kurtosis=0.0)
        assert dsr > 0.99

    def test_dsr_requires_two_trials(self):
        with pytest.raises(ValueError, match="N ≥ 2"):
            expected_max_sharpe(1, 0.01)


class TestHLZ:
    def test_significant_and_insignificant(self):
        rng = np.random.default_rng(21)
        strong = rng.normal(0.0008, 0.01, size=2000)   # 期望 t ≈ 11
        weak = rng.normal(0.0, 0.01, size=2000)
        assert abs(hlz_t_stat(strong)) > 3.0
        assert abs(hlz_t_stat(weak)) < 3.0

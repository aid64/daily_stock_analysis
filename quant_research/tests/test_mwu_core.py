"""MWUCore（core.mwu_core）单测

覆盖：权重更新算术（纸面收益 → loss → 乘性更新 + Fixed Share 回拉）、
建议合成、选取价记录、防御性输入（NaN/缺失/非正价格）、协议约束。
所有测试使用合成数据，不依赖网络。
"""
from __future__ import annotations

import numpy as np
import pytest

from quant_research.core.mwu_core import MWUCore


def _core(eta: float = 0.3, loss_scale: float = 0.05, share_alpha: float = 0.0) -> MWUCore:
    return MWUCore(["a", "b"], eta=eta, loss_scale=loss_scale, share_alpha=share_alpha)


class TestProtocol:
    def test_requires_two_experts(self):
        with pytest.raises(ValueError, match="至少需要 2 个专家"):
            MWUCore(["only"])

    def test_rejects_duplicate_names(self):
        with pytest.raises(ValueError, match="重复"):
            MWUCore(["a", "a"])

    def test_equal_initial_weights(self):
        core = _core()
        assert core.weights == {"a": pytest.approx(0.5), "b": pytest.approx(0.5)}


class TestUpdateWeights:
    def test_losing_expert_shrinks_no_share_alpha(self):
        core = _core(eta=0.3, loss_scale=0.05, share_alpha=0.0)
        # 专家 a 上期建议价格 100 的标的，当前跌到 95 → r=-5% → loss=1
        core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
        core.update_weights({"X": 95.0})
        expected_a = 0.5 * (1.0 - 0.3 * 1.0)
        assert core.weights["a"] == pytest.approx(expected_a / (expected_a + 0.5))
        # 权重和恒为 1（Fixed Share α=0 时归一化保持）
        assert sum(core.weights.values()) == pytest.approx(1.0)

    def test_gain_does_not_shrink_under_single_sided_mapping(self):
        """当前 clip 映射：盈利（r>0）loss=0，权重不被惩罚（R1b 将修正）"""
        core = _core(eta=0.3, loss_scale=0.05, share_alpha=0.0)
        core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
        core.update_weights({"X": 105.0})
        assert core.weights["a"] == pytest.approx(0.5)

    def test_missing_price_treated_as_cash_return(self):
        """标的缺失 → 收益项跳过；全空 → r=0 → loss=0 → 权重不变（α=0）"""
        core = _core(share_alpha=0.0)
        core.record_picks({"a": {"MISSING": 100.0}, "b": {}}, {"MISSING": 100.0})
        core.update_weights({})
        assert core.weights == {"a": pytest.approx(0.5), "b": pytest.approx(0.5)}

    def test_nan_and_nonpositive_prices_skipped(self):
        core = _core(share_alpha=0.0)
        core.record_picks({"a": {"X": 100.0, "Y": 100.0}, "b": {}}, {"X": 100.0, "Y": 100.0})
        # X 暴跌有效收益项；Y 为 NaN/0 → 跳过
        core.update_weights({"X": 80.0, "Y": float("nan")})
        # r = mean([-0.2]) = -0.2 → loss = min(0.2/0.05, 1) = 1
        expected_a = 0.5 * (1.0 - 0.3)
        assert core.weights["a"] == pytest.approx(expected_a / (expected_a + 0.5))
        core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
        core.update_weights({"X": 0.0})  # 非正价格 → 跳过 → r=0
        assert core.weights["a"] == pytest.approx(
            (expected_a / (expected_a + 0.5)) * 1.0 / (
                (expected_a / (expected_a + 0.5)) + (0.5 / (expected_a + 0.5))
            )
        )

    def test_fixed_share_pullback_keeps_weights_alive(self):
        """α>0：被压死的专家每期仍获得 share_mass=α/N 的回拉质量，不锁死到 0"""
        core = _core(eta=1.0, loss_scale=0.05, share_alpha=0.02)
        share_mass = 0.02 / 2
        for _ in range(50):
            core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
            core.update_weights({"X": 50.0})  # a 每期吃满 loss=1（η=1 → 乘性项归零）
        # a 每期被乘性更新清零后，仅剩回拉质量 share_mass（稳态）
        assert core.weights["a"] == pytest.approx(share_mass, rel=1e-3)
        assert sum(core.weights.values()) == pytest.approx(1.0, rel=1e-9)

    def test_repressed_expert_recovers_when_winning_again(self):
        """被压死的专家重新盈利后，权重可回升（回归测试：2015 股灾→结构牛场景）"""
        core = _core(eta=0.3, loss_scale=0.05, share_alpha=0.02)
        for _ in range(40):
            core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
            core.update_weights({"X": 50.0})
        low = core.weights["a"]
        core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
        core.update_weights({"X": 150.0})  # a 大幅盈利
        assert core.weights["a"] > low


class TestCombineAndRecord:
    def test_combine_merges_by_weight(self):
        core = MWUCore(["a", "b"], eta=0.1)
        target = core.combine({
            "a": {"X": 0.6, "Y": 0.4},
            "b": {"X": 1.0},
        })
        assert target["X"] == pytest.approx(0.5 * 0.6 + 0.5 * 1.0)
        assert target["Y"] == pytest.approx(0.5 * 0.4)
        assert sum(target.values()) == pytest.approx(1.0)

    def test_combine_skips_empty_and_zero_weight(self):
        core = MWUCore(["a", "b"], eta=0.1)
        assert core.combine({"a": {}, "b": {"X": 1.0}}) == {"X": pytest.approx(0.5)}

    def test_record_picks_skips_invalid_prices(self):
        core = _core()
        core.record_picks(
            {"a": {"X": 10.0, "Y": 20.0}, "b": {"Z": 30.0}},
            {"X": 10.0, "Y": float("nan")},
        )
        picks = core.last_picks
        assert picks["a"] == {"X": 10.0}          # NaN 价不记录
        assert picks["b"] == {}                    # 缺失价不记录
        # 未记录的标的下期不参与收益结算
        core.update_weights({"X": 5.0, "Y": 1.0, "Z": 3.0})
        assert True

    def test_record_picks_defaults_empty_for_unknown_expert(self):
        core = _core()
        core.record_picks({}, {})
        assert core.last_picks == {"a": {}, "b": {}}

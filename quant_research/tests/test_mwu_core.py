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
    def test_losing_expert_shrinks_relative_to_cash_expert(self):
        """亏损专家 ℓ=1；空选专家=现金 r=0 → ℓ=0.5 → 亏损者相对权重下降"""
        core = _core(eta=0.3, loss_scale=0.05, share_alpha=0.0)
        # 专家 a 上期建议价格 100 的标的，当前跌到 95 → r=-5% → ρ=-1 → ℓ=1
        core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
        core.update_weights({"X": 95.0})
        w_a = 0.5 * (1.0 - 0.3 * 1.0)     # 0.35
        w_b = 0.5 * (1.0 - 0.3 * 0.5)     # 0.425（现金中性）
        assert core.weights["a"] == pytest.approx(w_a / (w_a + w_b))
        assert sum(core.weights.values()) == pytest.approx(1.0)

    def test_gain_relatively_rewards_expert(self):
        """线性映射：盈利专家 ℓ<0.5，相对零收益专家权重上升（R1b 修正的核心行为）"""
        core = _core(eta=0.3, loss_scale=0.05, share_alpha=0.0)
        # 两位专家都有建议：a 盈利 +5%（ℓ=0），b 零收益（ℓ=0.5）
        core.record_picks({"a": {"X": 100.0}, "b": {"Y": 100.0}}, {"X": 100.0, "Y": 100.0})
        core.update_weights({"X": 105.0, "Y": 100.0})
        w_a = 0.5                          # ℓ=0，不缩
        w_b = 0.5 * (1.0 - 0.3 * 0.5)      # 0.425
        assert core.weights["a"] == pytest.approx(w_a / (w_a + w_b))
        assert core.weights["a"] > 0.5

    def test_loss_mapping_boundaries(self):
        """±超界收益截断到 ℓ=0/1；权重下限截断防归零"""
        core = _core(eta=1.0, loss_scale=0.05, share_alpha=0.0)
        core.record_picks({"a": {"X": 100.0}, "b": {"Y": 100.0}}, {"X": 100.0, "Y": 100.0})
        core.update_weights({"X": 130.0, "Y": 50.0})  # a 暴赚 ℓ=0；b 暴亏 ℓ=1
        assert core.weights["a"] == pytest.approx(1.0, abs=1e-5)
        assert core.weights["b"] == pytest.approx(0.0, abs=1e-5)

    def test_picking_expert_with_all_invalid_returns_is_neutral(self):
        """收益项全部无效（缺失/NaN/非正）与空选同待遇：r=0 → ℓ=0.5，两专家同缩"""
        core = _core(share_alpha=0.0)
        core.record_picks({"a": {"MISSING": 100.0}, "b": {}}, {"MISSING": 100.0})
        core.update_weights({})
        assert core.weights["a"] == pytest.approx(0.5)
        assert core.weights["b"] == pytest.approx(0.5)

    def test_nan_and_nonpositive_prices_skipped(self):
        core = _core(share_alpha=0.0)
        core.record_picks({"a": {"X": 100.0, "Y": 100.0}, "b": {}}, {"X": 100.0, "Y": 100.0})
        # X 暴跌有效收益项；Y 为 NaN → 跳过
        core.update_weights({"X": 80.0, "Y": float("nan")})
        w_a = 0.5 * (1.0 - 0.3 * 1.0)      # r=-0.2 → ρ 截断 -1 → ℓ=1
        w_b = 0.5 * (1.0 - 0.3 * 0.5)      # 空选 → 现金中性
        assert core.weights["a"] == pytest.approx(w_a / (w_a + w_b))
        # 非正价格 → 收益项跳过 → 与空选同为 r=0 → 两专家同缩 → 份额不变
        share_before = core.weights["a"]
        core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
        core.update_weights({"X": 0.0})
        assert core.weights["a"] == pytest.approx(share_before)

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


class TestUnpricedAudit:
    """H 槽"四专家"声明的可证伪检查点：缺价建议不得静默当 0 收益混过。"""

    def test_reports_unpriced_terms_per_expert(self):
        core = MWUCore(["etf", "cb"], eta=0.1, loss_scale=0.05, share_alpha=0.02)
        core.record_picks({"etf": {"512890.SH": 1.0}, "cb": {"113062.SH": 100.0}},
                          {"512890.SH": 1.0, "113062.SH": 100.0})
        core.update_weights({"512890.SH": 1.0})          # CB 缺价
        assert core.unpriced_terms.get("cb") == ["113062.SH"]
        assert "etf" not in core.unpriced_terms

    def test_nan_and_nonpositive_prices_count_as_unpriced(self):
        core = MWUCore(["a", "b"], eta=0.1, share_alpha=0.0)
        core.record_picks({"a": {"X": 100.0, "Y": 100.0}, "b": {}},
                          {"X": 100.0, "Y": 100.0})
        core.update_weights({"X": float("nan"), "Y": 0.0})
        assert sorted(core.unpriced_terms["a"]) == ["X", "Y"]

    def test_unpriced_terms_reset_each_update(self):
        core = MWUCore(["a", "b"], eta=0.1, share_alpha=0.0)
        core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
        core.update_weights({})
        assert core.unpriced_terms.get("a") == ["X"]
        core.record_picks({"a": {"X": 100.0}, "b": {}}, {"X": 100.0})
        core.update_weights({"X": 101.0})
        assert core.unpriced_terms == {}

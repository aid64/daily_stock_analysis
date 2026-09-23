# -*- coding: utf-8 -*-
"""P2 统一输出契约测试：StrategyOutput + 适配层 + 注册表合一。

v4.2 发现#3 的清偿验证：P1 三模块异构输出（PositionDecision/原地改
list/Tuple）与 strategy_engine 展示型 StrategySignal 归并为单一
StrategyOutput 契约；strategies.yaml 单一注册（系统策略身份登记）。
"""
from __future__ import annotations

from src.services.strategies.adapters import (
    concentration_output, decisions_to_outputs, e2_timing_output,
    signal_to_output, user_rules_output)
from src.services.strategies.types import (
    PositionDecision, StrategyOutput, TIER_CASH_MANAGEMENT,
    TIER_EXIT_RULE, TIER_RISK_CONTROL, TIER_SATELLITE, VERDICT_HOLD,
    VERDICT_REDUCE, VERDICT_SELL)


def _dec(symbol, qty, verdict, target, price=50.0, weight=50.0, reason="r"):
    return PositionDecision(
        symbol=symbol, name=symbol, quantity=qty, avg_cost=0.0,
        last_price=price, price_date="2026-09-23", pnl=0.0, pnl_pct=0.0,
        weight_pct=weight, verdict=verdict, target_quantity=target,
        reason=reason, rule_state="state")


class TestStrategyOutputContract:
    def test_priority_chain(self):
        """优先级链：exit < risk < cash < satellite（数字小者优先）。"""
        out_exit = StrategyOutput(strategy_id="a", name="a",
                                  tier=TIER_EXIT_RULE, cadence="daily")
        out_risk = StrategyOutput(strategy_id="b", name="b",
                                  tier=TIER_RISK_CONTROL, cadence="daily")
        out_cash = StrategyOutput(strategy_id="c", name="c",
                                  tier=TIER_CASH_MANAGEMENT, cadence="daily")
        out_sat = StrategyOutput(strategy_id="d", name="d",
                                 tier=TIER_SATELLITE, cadence="daily")
        assert (out_exit.priority < out_risk.priority
                < out_cash.priority < out_sat.priority)
        assert out_exit.priority == 10 and out_sat.priority == 40

    def test_unknown_tier_falls_back_to_satellite(self):
        out = StrategyOutput(strategy_id="x", name="x",
                             tier="low_risk", cadence="daily")
        assert out.priority == 35          # low_risk 介于现金与卫星之间
        unknown = StrategyOutput(strategy_id="y", name="y",
                                 tier="???", cadence="daily")
        assert unknown.priority == 40      # 未知识别为最低优先级

    def test_to_dict_shape(self):
        out = StrategyOutput(
            strategy_id="s", name="n", tier=TIER_EXIT_RULE, cadence="daily",
            legs=[], cash_action="", reason="why",
            constraint_notes=["c1"], meta={"k": 1})
        d = out.to_dict()
        assert d["strategy_id"] == "s" and d["priority"] == 10
        assert d["legs"] == [] and d["constraint_notes"] == ["c1"]
        assert d["meta"] == {"k": 1}


class TestAdapters:
    def test_user_rules_only_sell_legs(self):
        """exit_rule 输出只含 SELL 腿；WAIT/HOLD 不产腿（今日不执行）。"""
        decisions = [
            _dec("000899", 100, VERDICT_SELL, 0),
            _dec("002475", 200, VERDICT_HOLD, 200),
        ]
        out = user_rules_output(decisions)
        assert out.tier == TIER_EXIT_RULE
        assert [l.symbol for l in out.legs] == ["000899"]
        assert out.legs[0].target_quantity == 0

    def test_concentration_only_reduce_legs(self):
        decisions = [
            _dec("002475", 200, VERDICT_REDUCE, 100,
                 reason="保底一手分批降低（非清仓）"),
            _dec("000899", 100, VERDICT_HOLD, 100),
        ]
        out = concentration_output(decisions)
        assert out.tier == TIER_RISK_CONTROL
        assert len(out.legs) == 1
        assert out.legs[0].target_quantity == 100
        assert "保底一手" in out.legs[0].constraint

    def test_e2_timing_defensive(self):
        out = e2_timing_output(0.82, "2026-09-22", "持有", "分位超 80%")
        assert out.tier == TIER_CASH_MANAGEMENT
        assert out.cash_action == "现金防守（不新增买入）"
        assert out.legs == []
        # 非防守期无现金操作
        out2 = e2_timing_output(0.5, "2026-09-22", "可配置", "分位正常")
        assert out2.cash_action == ""

    def test_signal_to_output_cash_action(self):
        from src.services.strategy_engine import StrategySignal

        sig = StrategySignal(
            strategy_id="reverse_repo_window", name="国债逆回购窗口",
            tier="cash_management", risk="zero",
            action="操作 GC001 逆回购 ¥1,000", reason="明日非交易日")
        out = signal_to_output(sig)
        assert out.cash_action == "操作 GC001 逆回购 ¥1,000"
        assert out.legs == []                  # 现金操作不产持仓腿
        assert out.priority == 30

    def test_decisions_to_outputs_full_assembly(self):
        decisions = [
            _dec("000899", 100, VERDICT_SELL, 0),
            _dec("002475", 200, VERDICT_REDUCE, 100),
        ]
        outs = decisions_to_outputs(
            decisions, e2_percentile=0.9, e2_as_of="2026-09-22",
            cash_verdict="持有", cash_reason="防守",
            engine_signals=[])
        ids = {o.strategy_id for o in outs}
        assert ids == {"user_rules", "concentration", "e2_timing"}
        # 引擎信号并入
        from src.services.strategy_engine import StrategySignal

        sig = StrategySignal(
            strategy_id="cb_new_issue", name="转债打新",
            tier="cash_management", risk="near_zero",
            action="按打新提醒操作", reason="零成本")
        outs2 = decisions_to_outputs(decisions, engine_signals=[sig])
        assert {o.strategy_id for o in outs2} == {
            "user_rules", "concentration", "e2_timing", "cb_new_issue"}


class TestRegistryUnification:
    def test_system_entries_skipped_by_engine(self):
        """注册表合一：系统条目（system: true）不进轻上下文评估，
        也不因缺 evaluator 告警跳过——它们由 compositor 侧适配。"""
        from src.services.strategy_engine import evaluate_strategies

        # 真实注册表含 3 个系统条目 + 2 个现金策略（active）
        signals = evaluate_strategies(
            {"today": None, "cash": 0})   # cash=0 → 逆回购不触发
        ids = {s.strategy_id for s in signals}
        assert "user_rules" not in ids and "concentration" not in ids
        assert "e2_timing" not in ids
        # cb_new_issue 依赖打新日历，失败降级也允许为空——只断言系统条目缺席
        assert all(not i.startswith("user_rules") for i in ids)

    def test_evaluate_strategy_outputs(self):
        from src.services.strategy_engine import evaluate_strategy_outputs

        outs = evaluate_strategy_outputs({"today": None, "cash": 0})
        assert all(isinstance(o, StrategyOutput) for o in outs)


class TestCompose:
    """P2 组合器：优先级归并 / 容忍带 / cadence 排他。"""

    def _output(self, sid, tier, legs, cadence="daily"):
        return StrategyOutput(strategy_id=sid, name=sid, tier=tier,
                              cadence=cadence, legs=legs)

    def test_priority_merge_exit_wins(self):
        from src.services.portfolio_compositor import compose
        from src.services.strategies.types import TargetLeg

        exit_out = self._output("user_rules", TIER_EXIT_RULE, [
            TargetLeg(symbol="002475", target_quantity=0)])
        risk_out = self._output("concentration", TIER_RISK_CONTROL, [
            TargetLeg(symbol="002475", target_quantity=100)])
        res = compose([risk_out, exit_out],
                      current_positions={"002475": 200},
                      prices={"002475": 54.84})
        # 止损（exit）优先于集中度（risk_control）——目标 0 生效
        assert res.targets["002475"] == 0
        assert res.winners["002475"] == "user_rules"
        assert res.conflicts == [{
            "symbol": "002475", "winner": "user_rules",
            "loser": "concentration", "loser_target": 100}]

    def test_tolerance_band_no_op_when_target_equals_current(self):
        from src.services.portfolio_compositor import compose
        from src.services.strategies.types import TargetLeg

        hold_out = self._output("s1", TIER_SATELLITE, [
            TargetLeg(symbol="000899", target_quantity=100)])
        res = compose([hold_out], current_positions={"000899": 100},
                      prices={"000899": 9.32})
        # 目标==当前 → 无调仓（不进 targets）
        assert "000899" not in res.targets

    def test_under_one_lot_note_kept(self):
        from src.services.portfolio_compositor import compose
        from src.services.strategies.types import TargetLeg

        out = self._output("s", TIER_RISK_CONTROL, [
            TargetLeg(symbol="000899", target_quantity=50)])
        res = compose([out], current_positions={"000899": 100},
                      prices={"000899": 9.32})
        # 差额 50 < 一手 100 → 记 note 但保留 target（披露用，order_sheet 过滤）
        assert res.targets["000899"] == 50
        assert any("不足一手" in n for n in res.notes)

    def test_cadence_exclusivity_rebalance_wins(self):
        from src.services.portfolio_compositor import compose
        from src.services.strategies.types import TargetLeg

        daily = self._output("daily_tweak", TIER_SATELLITE, [
            TargetLeg(symbol="510300", target_quantity=100)], cadence="daily")
        weekly = self._output("weekly_rebal", TIER_SATELLITE, [
            TargetLeg(symbol="510300", target_quantity=200)], cadence="weekly")
        res = compose([daily, weekly], current_positions={"510300": 0},
                      prices={"510300": 4.0})
        # 同日双频触发：再平衡优先，日频微调被排他
        assert res.targets["510300"] == 200
        assert any("cadence 排他" in n for n in res.notes)

    def test_golden_equivalence(self, monkeypatch):
        """黄金场景等价性：adapters→compose 的归并目标与现有编排路径
        产出的订单一致（immediate 破位下两只全卖 → target 0）。"""
        from src.services.account_decision_service import (
            build_account_decision, decision_to_sheet)
        from src.services.portfolio_compositor import compose
        from src.services.strategies.adapters import user_rules_output

        import src.services.account_decision_service as ads
        monkeypatch.setattr(ads, "compute_e2_percentile",
                            lambda: (0.875, "2026-09-11"))
        monkeypatch.setattr(
            ads, "_latest_ai_notes",
            lambda symbols, days=3: (
                {"000899": "观望（2026-09-14）", "002475": "减仓（2026-09-14）"},
                {"000899": "赣能股份", "002475": "立讯精密"}))
        from src.services.account_decision_service import (
            BreachDiagnosis, URGENCY_IMMEDIATE)
        monkeypatch.setattr(
            "src.services.strategies.user_rules.diagnose_breach",
            lambda **kw: BreachDiagnosis(
                depth_pct=10.5, consecutive_days=22, above_ma_count=0,
                ma_structure="均线纠缠", volume_note="放量破位",
                urgency=URGENCY_IMMEDIATE, suggestion="立即全部卖出"))

        rules = {"000899": {"stop_loss": [10.87, 10.65]},
                 "002475": {"stop_loss": [55.07, 53.93]}}
        snap = {"total_cash": 1549.0, "accounts": [{"positions": [
            {"symbol": "000899", "quantity": 100, "avg_cost": 11.16,
             "last_price": 9.75, "price_date": "2026-09-14"},
            {"symbol": "002475", "quantity": 200, "avg_cost": 56.77,
             "last_price": 52.78, "price_date": "2026-09-14"}]}]}
        decision = build_account_decision(snapshot=snap, rules=rules)
        sheet = decision_to_sheet(decision, available_cash=1549.0)

        # 新路径：适配 → compose
        outputs = [user_rules_output(decision.positions)]
        res = compose(outputs,
                      current_positions={"000899": 100, "002475": 200},
                      prices={"000899": 9.75, "002475": 52.78})
        assert res.targets == {"000899": 0, "002475": 0}
        # 现有路径：订单腿 sell 100 / sell 200（目标 0 的订单化）
        orders = {o.symbol: (o.direction, o.quantity) for o in sheet.orders}
        assert orders == {"000899": ("sell", 100), "002475": ("sell", 200)}


class TestBudgetGate:
    """P2-5 position_sizer 预算闸门（只约束买入腿）。"""

    def _sizer(self, max_pos_pct=25.0, max_total_pct=95.0):
        from src.services.position_sizer import (
            PositionSizer, PositionSizerConfig)
        return PositionSizer(PositionSizerConfig(
            max_position_pct=max_pos_pct,
            max_total_exposure_pct=max_total_pct))

    def _result(self, targets):
        from src.services.portfolio_compositor import ComposeResult
        return ComposeResult(targets=dict(targets))

    def test_sell_leg_untouched(self):
        """卖出/减仓不受预算约束（持有多少都能卖）。"""
        from src.services.portfolio_compositor import apply_budget_gate

        res = self._result({"002475": 0})   # 全卖
        apply_budget_gate(
            res, current_positions={"002475": 200},
            prices={"002475": 54.84}, account_equity=13000.0,
            sizer=self._sizer())
        assert res.targets["002475"] == 0   # 卖出目标不被裁剪

    def test_single_position_cap(self):
        """买入受单票上限裁剪：equity=10000、cap 25%=2500，@10 → ≤200 股。"""
        from src.services.portfolio_compositor import apply_budget_gate

        res = self._result({"600000": 1000})   # 想买到 1000 股
        apply_budget_gate(
            res, current_positions={}, prices={"600000": 10.0},
            account_equity=10000.0, sizer=self._sizer(max_pos_pct=25.0))
        assert res.targets["600000"] == 200    # 2500/10 → 200 股
        # 注：total 闸门可能进一步裁剪，但单票已先压到 200

    def test_total_exposure_cap(self):
        """总敞口上限：已持有吃掉大部分预算，新买入受剩余预算限制。"""
        from src.services.portfolio_compositor import apply_budget_gate

        # equity=10000，总敞口 95%=9500；已持 800 股@10=8000 → 余 1500
        res = self._result({"600001": 200})    # 想新买 200 股@10=2000 > 1500
        apply_budget_gate(
            res, current_positions={"600001": 0, "existing": 800},
            prices={"600001": 10.0, "existing": 10.0},
            account_equity=10000.0,
            sizer=self._sizer(max_pos_pct=95.0, max_total_pct=95.0))
        # 剩余预算 1500 → 只能买 100 股
        assert res.targets["600001"] == 100

    def test_no_buy_leg_noop(self):
        """当前账户无买入腿（观察池只读）→ 闸门空转，targets 不变。"""
        from src.services.portfolio_compositor import apply_budget_gate

        res = self._result({"002475": 0})   # 只有卖出
        before = dict(res.targets)
        apply_budget_gate(
            res, current_positions={"002475": 200},
            prices={"002475": 54.84}, account_equity=13000.0,
            sizer=self._sizer())
        assert res.targets == before

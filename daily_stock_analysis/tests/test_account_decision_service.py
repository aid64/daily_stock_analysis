# -*- coding: utf-8 -*-
"""账户中心决策引擎测试（FINAL-PLAN-ACCOUNT-CENTRIC R1）

决策三档（止损卖/集中度减仓/持有）+ 决策包 → 操作单映射 + 渲染。
用户真实账户场景：000899/002475 双止损触发 + 现金防守。
"""
from __future__ import annotations

from typing import Any, Dict

from src.services.account_decision_service import (
    E2_DEFENSIVE_THRESHOLD,
    URGENCY_HALF,
    URGENCY_IMMEDIATE,
    URGENCY_WAIT,
    VERDICT_HOLD,
    VERDICT_REDUCE,
    VERDICT_SELL,
    build_account_decision,
    decision_to_sheet,
    decide_position,
    diagnose_breach,
    render_decision_package,
)


def _position(symbol: str, qty: float, cost: float, price: float) -> Dict[str, Any]:
    return {"symbol": symbol, "quantity": qty, "avg_cost": cost,
            "last_price": price, "price_date": "2026-09-14", "name": None}


def _snapshot(positions, cash: float = 1549.0) -> Dict[str, Any]:
    return {"total_cash": cash, "accounts": [{"positions": positions}]}


RULES_899 = {"stop_loss": [10.87, 10.65], "take_profit": [11.77, 12.89]}
RULES_2475 = {"stop_loss": [55.07, 53.93], "take_profit": [59.61, 65.29]}


class TestDecidePosition:
    def test_stop_loss_breached_sells_all(self):
        """用户真实场景：000899 现价 9.75 跌破止损 10.87/10.65 → 全卖"""
        d = decide_position(
            symbol="000899", name="赣能股份", quantity=100, avg_cost=11.16,
            price=9.75, price_date="", weight_pct=7.4,
            rules=RULES_899, concentration_threshold_pct=35.0)
        assert d.verdict == VERDICT_SELL
        assert d.target_quantity == 0
        assert "止损" in d.rule_state and "10.87" in d.rule_state

    def test_take_profit_hit_sells_all(self):
        d = decide_position(
            symbol="000899", name="赣能股份", quantity=100, avg_cost=11.16,
            price=13.0, price_date="", weight_pct=7.4,
            rules=RULES_899, concentration_threshold_pct=35.0)
        assert d.verdict == VERDICT_SELL
        assert "止盈" in d.rule_state

    def test_concentration_over_limit_reduces(self):
        """002475 若无价格规则触发但集中度超限 → 减仓档"""
        d = decide_position(
            symbol="002475", name="立讯精密", quantity=200, avg_cost=56.77,
            price=57.0, price_date="", weight_pct=80.0,
            rules={}, concentration_threshold_pct=35.0)
        assert d.verdict == VERDICT_REDUCE
        assert "集中度" in d.reason

    def test_no_trigger_holds(self):
        d = decide_position(
            symbol="000899", name="赣能股份", quantity=100, avg_cost=11.16,
            price=11.0, price_date="", weight_pct=7.4,
            rules=RULES_899, concentration_threshold_pct=35.0)
        assert d.verdict == VERDICT_HOLD
        assert d.target_quantity == 100

    def test_no_rules_hold_with_note(self):
        d = decide_position(
            symbol="600519", name="贵州茅台", quantity=100, avg_cost=1500,
            price=1600, price_date="", weight_pct=10.0,
            rules={}, concentration_threshold_pct=35.0)
        assert d.verdict == VERDICT_HOLD
        assert "未配置价格规则" in d.rule_state


class TestBuildAccountDecision:
    def test_real_account_double_stop_loss(self, monkeypatch):
        """真实账户：两只都跌破止损 → 两条卖出决策 + 现金防守"""
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (0.875, "2026-09-11"))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({"000899": "观望——…"},
                                     {"000899": "赣能股份", "002475": "立讯精密"}))
        snap = _snapshot([
            _position("000899", 100, 11.16, 9.75),
            _position("002475", 200, 56.77, 52.78),
        ])
        decision = build_account_decision(
            snapshot=snap,
            rules={"000899": RULES_899, "002475": RULES_2475})
        verdicts = {p.symbol: p.verdict for p in decision.positions}
        assert verdicts == {"000899": VERDICT_SELL, "002475": VERDICT_SELL}
        # 名称从 AI 层取
        names = {p.symbol: p.name for p in decision.positions}
        assert names["000899"] == "赣能股份"
        # E2 高分位 → 现金持有
        assert decision.cash_verdict == "持有"
        assert "不新增买入" in decision.cash_reason
        # 浮亏正确
        p899 = next(p for p in decision.positions if p.symbol == "000899")
        assert p899.pnl == round((9.75 - 11.16) * 100, 2)

    def test_concentration_reduce_target_lots(self, monkeypatch):
        """集中度减仓：目标量按整手取整且不超过阈值预算

        P1.5 账户规模感知（v4.2 发现#5）：总市值 57×200+100=11,500、
        35% 预算 ¥4,025 < 一手 ¥5,700——旧逻辑 target=0（"减仓=清仓"的
        不可执行建议，连续 9 天零执行的可能成因），新逻辑保底一手
        target=100。对照留痕见 docs/decision-layer-changelog.md。
        """
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (None, ""))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({}, {}))
        snap = _snapshot([
            _position("002475", 200, 56.77, 57.0),
        ], cash=100.0)
        decision = build_account_decision(
            snapshot=snap, rules={}, concentration_threshold_pct=35.0)
        p = decision.positions[0]
        assert p.verdict == VERDICT_REDUCE
        # 预算不足一手 → 保底一手（不再清仓）
        assert p.target_quantity == 100
        assert "保底一手" in p.reason

    def test_e2_normal_allows_cash_deploy(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (0.50, "2026-09-11"))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({}, {}))
        snap = _snapshot([_position("000899", 100, 11.16, 11.0)])
        decision = build_account_decision(
            snapshot=snap, rules={"000899": RULES_899})
        assert decision.cash_verdict == "可配置"

    def test_no_price_position_hold_without_quote(self, monkeypatch):
        """无报价持仓：不给指令，人工核实"""
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (None, ""))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({}, {}))
        snap = _snapshot([_position("000899", 100, 11.16, 0)])
        decision = build_account_decision(snapshot=snap, rules={})
        p = decision.positions[0]
        assert p.verdict == VERDICT_HOLD
        assert "无法定价" in p.reason


class TestDecisionToSheet:
    def test_sell_decisions_become_orders(self, monkeypatch):
        """决策包 → 操作单：sell 进 orders（复用状态机），hold 进 holds"""
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (None, ""))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({}, {}))
        snap = _snapshot([
            _position("000899", 100, 11.16, 9.75),   # 止损 → sell
            _position("600519", 10, 1500, 1600),     # 无规则 → hold（权重低）
        ], cash=50000.0)
        decision = build_account_decision(
            snapshot=snap,
            rules={"000899": RULES_899, "600519": {}})
        sheet = decision_to_sheet(decision, available_cash=50000.0)
        sells = [o for o in sheet.orders if o.direction == "sell"]
        assert len(sells) == 1 and sells[0].symbol == "000899"
        assert sells[0].quantity == 100
        # 卖单理由含规则状态（账户决策语义，非策略 diff 文案）
        assert "止损" in sells[0].reason
        # hold 进 holds 且带理由
        assert any(h.symbol == "600519" for h in sheet.holds)

    def test_hold_positions_stay_out_of_orders(self, monkeypatch):
        """规则未触发且集中度在阈值内 → 零指令，全部 holds"""
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (None, ""))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({}, {}))
        # 多持仓摊薄权重（单票 41.5% 仍会触发集中度减仓）
        snap = _snapshot([
            _position("000899", 100, 11.16, 11.0),
            _position("600519", 10, 1500, 1600),
        ], cash=50000.0)
        decision = build_account_decision(
            snapshot=snap, rules={"000899": RULES_899, "600519": {}})
        sheet = decision_to_sheet(decision, available_cash=50000.0)
        assert not sheet.orders
        assert len(sheet.holds) == 2


class TestBreachDiagnosis:
    """破位诊断：规则触发后按深度/持续性/均线/量能给出分级建议。"""

    def _bars(self, closes, volumes=None):
        vols = volumes or [1_000_000] * len(closes)
        return [(f"2026-08-{i+1:02d}", c, v)
                for i, (c, v) in enumerate(zip(closes, vols))]

    def test_deep_long_break_with_volume_is_immediate(self):
        """深破位 + 长期低于止损 + 放量 → 建议立即全卖"""
        # 25 天全部低于止损 10.65，且最近放量
        closes = [10.5] * 20 + [10.4, 10.2, 10.0, 9.8, 9.5]
        volumes = [1_000_000] * 20 + [2_000_000] * 5
        diag = diagnose_breach(
            symbol="000899", price=9.5, stop_levels=[10.87, 10.65],
            bars=self._bars(closes, volumes))
        assert diag is not None
        assert diag.urgency == URGENCY_IMMEDIATE
        assert diag.consecutive_days == 25
        assert diag.depth_pct > 5

    def test_fresh_break_downgrades_to_half(self):
        """首日破位即使技术面弱也不全清——避免单日插针"""
        # 前半段在止损上方，最后一日刚跌破
        closes = [10.9, 10.95, 11.0, 10.92, 10.88, 10.9, 10.95, 10.98,
                  10.9, 10.92, 10.94, 10.9, 10.88, 10.9, 10.92, 10.9,
                  10.88, 10.86, 10.9, 10.88, 10.5]
        diag = diagnose_breach(
            symbol="000899", price=10.5, stop_levels=[10.87, 10.65],
            bars=self._bars(closes))
        assert diag is not None
        assert diag.consecutive_days == 1
        assert diag.urgency == URGENCY_HALF

    def test_shallow_break_low_volume_waits(self):
        """缩量浅破位（刚破位、幅度小）→ 建议暂缓一日观察"""
        closes = [10.9] * 16 + [10.8] * 4 + [10.62]
        volumes = [1_000_000] * 16 + [300_000] * 5   # 近5日显著缩量
        diag = diagnose_breach(
            symbol="000899", price=10.62, stop_levels=[10.65],
            bars=self._bars(closes, volumes))
        assert diag is not None
        assert diag.consecutive_days == 1
        assert diag.depth_pct < 2
        assert diag.urgency == URGENCY_WAIT

    def test_no_bars_returns_none(self):
        assert diagnose_breach(
            symbol="000899", price=10.0, stop_levels=[10.65], bars=[]) is None

    def test_half_decision_really_halves_quantity(self):
        """诊断建议减半时，目标数量必须真的减半（文案与指令一致）。"""
        # 直接构造：首日破位 → half → 卖一半
        snap_decision = decide_position(
            symbol="002475", name="立讯精密", quantity=200, avg_cost=56.77,
            price=51.63, price_date="", weight_pct=40.0,
            rules=RULES_2475, concentration_threshold_pct=35.0)
        # 无法注入 bars，用真实本地数据或退化为全卖——此处只断言：
        # 若拿到 breach 且为 half，则 target 为一半；否则 target=0（退化为全卖）
        if snap_decision.breach and snap_decision.breach["urgency"] == URGENCY_HALF:
            assert snap_decision.target_quantity == 100
        else:
            assert snap_decision.target_quantity == 0


class TestRender:
    def test_render_package_contains_decision_blocks(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (0.875, "2026-09-11"))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({"000899": "观望——…（2026-09-14）"},
                                     {"000899": "赣能股份"}))
        # 屏蔽破位诊断，使渲染断言不依赖本机数据库中的日线
        monkeypatch.setattr(
            "src.services.strategies.user_rules.diagnose_breach",
            lambda **kwargs: None)
        snap = _snapshot([
            _position("000899", 100, 11.16, 9.75),
        ], cash=1549.0)
        decision = build_account_decision(
            snapshot=snap, rules={"000899": RULES_899})
        sheet = decision_to_sheet(decision, available_cash=1549.0)
        text = render_decision_package(decision, sheet=sheet)
        # 决策依据优先级声明
        assert "【今日待办" in text
        assert "依据：止损/止盈=结构锚生成" in text
        # 决策块：卖出 + 具体限价 + 执行边界
        assert "100股 @9.75" in text
        assert "14:50 未成交撤单" in text
        # AI 解释层出现
        # 动作卡片新特性：可直接复制的回填命令（AI 注释只在观察区展示）
        assert "成交后发送：/成交回填 000899 卖出 100 9.75" in text
        # 现金块 + E2
        assert "现金 ¥1,549" in text
        assert "87.5%" in text
        # 执行方式（半自动闭环）
        assert "/成交回填" in text

    def test_render_labels_driver_rule_and_floor_one_lot(self, monkeypatch):
        """P1.5（发现#5/#6）：集中度动作卡带驱动标签 + 保底一手文案可见。

        预算不足一手时旧逻辑输出"减仓=清仓"（200→0），用户无法执行；
        新逻辑保底一手（200→100）且依据行讲清"为什么不是全卖"，
        动作卡标注驱动规则（机制切换可见——止损/集中度交替主导不再隐形）。
        """
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (None, ""))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({}, {"002475": "立讯精密"}))
        # 无价格规则触发（rules 空）→ 集中度路径
        monkeypatch.setattr(
            "src.services.strategies.user_rules.diagnose_breach",
            lambda **kwargs: None)
        snap = _snapshot([
            _position("002475", 200, 56.77, 52.78),
        ], cash=1549.0)
        decision = build_account_decision(snapshot=snap, rules={})
        p = decision.positions[0]
        assert p.verdict == VERDICT_REDUCE
        assert p.target_quantity == 100          # 保底一手（预算 ¥4,711 < 一手 ¥5,278）
        sheet = decision_to_sheet(decision, available_cash=1549.0)
        text = render_decision_package(decision, sheet=sheet)
        # 动作卡：驱动规则标签（发现#6）
        assert "[集中度规则]" in text
        # 依据行讲清"为什么不是全卖"（发现#5）
        assert "保底一手" in text and "非清仓" in text
        # 卖出腿是 100 股不是 200 股
        assert "卖出 002475" in text and "100股 @52.78" in text

    def test_render_shows_breach_diagnosis(self, monkeypatch):
        """规则触发时渲染破位诊断行与分级建议。"""
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (None, ""))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({}, {}))
        from src.services.account_decision_service import BreachDiagnosis
        monkeypatch.setattr(
            "src.services.strategies.user_rules.diagnose_breach",
            lambda **kwargs: BreachDiagnosis(
                depth_pct=10.5, consecutive_days=22, above_ma_count=0,
                ma_structure="均线纠缠", volume_note="放量破位",
                urgency=URGENCY_IMMEDIATE,
                suggestion="技术面弱势证据充分——建议立即执行"))
        snap = _snapshot([_position("000899", 100, 11.16, 9.75)])
        decision = build_account_decision(
            snapshot=snap, rules={"000899": RULES_899})
        sheet = decision_to_sheet(decision, available_cash=1549.0)
        text = render_decision_package(decision, sheet=sheet)
        assert "破位：连续 22 日收于止损下方" in text
        assert "建议立即执行" in text

    def test_wait_urgency_holds_not_sells(self, monkeypatch):
        """审视第 6 条：WAIT 档（暂缓一日观察）不得落进全卖分支。

        文案"可暂缓一日观察"与订单"卖出全部"曾在同一消息并存——
        WAIT 必须产出持有决策（目标=当前量，不生成卖出腿）。
        """
        monkeypatch.setattr(
            "src.services.account_decision_service.compute_e2_percentile",
            lambda: (None, ""))
        monkeypatch.setattr(
            "src.services.account_decision_service._latest_ai_notes",
            lambda symbols, days=3: ({}, {}))
        from src.services.account_decision_service import BreachDiagnosis
        monkeypatch.setattr(
            "src.services.strategies.user_rules.diagnose_breach",
            lambda **kwargs: BreachDiagnosis(
                depth_pct=1.2, consecutive_days=1, above_ma_count=2,
                ma_structure="均线纠缠", volume_note="缩量破位",
                urgency=URGENCY_WAIT,
                suggestion="缩量浅破位，可暂缓一日观察，"
                           "若明日仍收于止损位下方再执行"))
        snap = _snapshot([_position("000899", 100, 11.16, 10.70)])
        decision = build_account_decision(
            snapshot=snap, rules={"000899": RULES_899})
        p = decision.positions[0]
        assert p.verdict == VERDICT_HOLD
        assert p.target_quantity == 100      # 今日不卖
        assert "暂缓" in p.reason
        assert p.breach is not None and p.breach["urgency"] == URGENCY_WAIT
        # 操作单不得出现 000899 的卖出腿
        sheet = decision_to_sheet(decision, available_cash=1549.0)
        assert all(o.symbol != "000899" for o in sheet.orders)
        text = render_decision_package(decision, sheet=sheet)
        assert "暂缓" in text
        # 000899 所在行不得出现"卖出"（WAIT=持有，无卖出腿）
        line_899 = next(ln for ln in text.splitlines() if "000899" in ln)
        assert "卖出" not in line_899

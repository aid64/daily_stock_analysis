# -*- coding: utf-8 -*-
"""compare_deck_runner 核心交易原语单测（离线，无网络）。"""

import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "compare_deck_runner.py"
spec = importlib.util.spec_from_file_location("compare_deck_runner", RUNNER)
mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("compare_deck_runner", mod)
spec.loader.exec_module(mod)


# ---------------------------------------------------------------- trade_fee
def test_trade_fee_commission_floor():
    # 350 元成交, 万0.845=0.03元 → 触发最低 5 元
    assert mod.trade_fee(350, "BUY", is_stock=False) == 5.0
    # 10 万元成交, 万0.845=8.45 元 > 5
    assert mod.trade_fee(100000, "BUY", is_stock=False) == 8.45


def test_trade_fee_stock_sell_adds_stamp():
    base = mod.trade_fee(100000, "SELL", is_stock=True)
    assert base == 8.45 + 50.0  # 佣金 + 0.05% 印花
    assert mod.trade_fee(100000, "BUY", is_stock=True) == 8.45  # 买入无印花


# ---------------------------------------------------------------- apply_orders
def _slot(cash=12411.0, holds=None):
    return {"cash": cash, "holds": list(holds or [])}


def test_apply_orders_sell_before_buy_and_cash_conservation():
    slot = _slot(cash=100.0, holds=[{"symbol": "512890.SH", "name": "红利低波ETF",
                                     "qty": 100, "price": 1.18, "notional": 118.0}])
    mod.apply_orders(
        slot,
        sells=[{"symbol": "512890.SH", "qty": 100, "price": 50.0}],   # 回款 5000-5
        buys=[{"symbol": "510300.SH", "qty": 100, "price": 4.416}],   # 需 446.6
        px={"512890.SH": {"price": 50.0}, "510300.SH": {"price": 4.416}},
    )
    assert len(slot["holds"]) == 1
    assert slot["holds"][0]["symbol"] == "510300.SH"
    total = slot["cash"] + slot["holds"][0]["notional"]
    assert total <= 12411.0 + 0.01


def test_apply_orders_unaffordable_buy_skipped_after_sell():
    slot = _slot(cash=100.0, holds=[])
    # 卖 118 元回款 113, 买 446.6 买不起 → 买入拒绝, 持仓空
    rejects = mod.apply_orders(
        slot,
        sells=[{"symbol": "512890.SH", "qty": 100, "price": 1.18}],
        buys=[{"symbol": "510300.SH", "qty": 100, "price": 4.416}],
        px={"512890.SH": {"price": 1.18}, "510300.SH": {"price": 4.416}},
    )
    assert slot["holds"] == []
    assert any("insufficient_cash" in r for r in rejects)


def test_apply_orders_buy_skipped_when_cash_insufficient():
    slot = _slot(cash=50.0)
    rejects = mod.apply_orders(slot, sells=[],
                               buys=[{"symbol": "510300.SH", "qty": 100, "price": 4.416}],
                               px={"510300.SH": {"price": 4.416}})
    assert slot["holds"] == []
    assert slot["cash"] == 50.0  # 未动
    assert any("insufficient_cash" in r for r in rejects)


def test_apply_orders_replaces_position():
    slot = _slot(holds=[{"symbol": "600000.SH", "qty": 1, "price": 1, "notional": 1}])
    mod.apply_orders(slot, sells=[{"symbol": "600000.SH", "qty": 1, "price": 1.0}], buys=[],
                     px={"600000.SH": {"price": 1.0}})
    assert slot["holds"] == []


# ------------------------------------------------------- apply_orders 新约束
def test_apply_orders_trading_gate_blocks_all(monkeypatch):
    """反例: _TRADING_OK=False 仍买入100。适配器下闸关闭→该批全部拒绝。"""
    monkeypatch.setattr(mod, "_TRADING_OK", False)
    slot = _slot(cash=12411.0)
    rejects = mod.apply_orders(slot, [],
                               [{"symbol": "510300.SH", "qty": 100, "price": 4.5}],
                               {"510300.SH": {"price": 4.5}})
    assert slot["holds"] == [] and slot["cash"] == 12411.0
    assert any("session_closed" in r for r in rejects)
    monkeypatch.setattr(mod, "_TRADING_OK", True)


def test_apply_orders_phantom_sell_rejected():
    """反例: 未持有证券凭空卖出增加现金。"""
    slot = _slot(cash=12411.0)
    rejects = mod.apply_orders(
        slot,
        sells=[{"symbol": "600000.SH", "qty": 100, "price": 10.0, "is_stock": True}],
        buys=[], px={"600000.SH": {"price": 10.0}})
    assert slot["cash"] == 12411.0 and slot["holds"] == []
    assert any("no_position" in r for r in rejects)


def test_apply_orders_zero_price_rejected():
    """反例: 价格0也能买入。"""
    slot = _slot(cash=12411.0)
    rejects = mod.apply_orders(
        slot, [],
        buys=[{"symbol": "600000.SH", "qty": 100, "price": 0.0, "is_stock": True}],
        px={"600000.SH": {"price": 0.0}})
    assert slot["cash"] == 12411.0 and slot["holds"] == []
    assert any("price_invalid" in r for r in rejects)


def test_apply_orders_missing_quote_rejected_hold_retained():
    """反例: 无报价不得按意图价/成本价成交。"""
    slot = _slot(cash=100.0, holds=[{"symbol": "512890.SH", "name": "红利低波",
                                     "qty": 100, "price": 1.18, "notional": 118.0}])
    rejects = mod.apply_orders(slot,
                               sells=[{"symbol": "512890.SH", "qty": 100, "price": 1.5}],
                               buys=[], px={})
    assert slot["holds"] and slot["holds"][0]["symbol"] == "512890.SH"
    assert any("no_quote" in r for r in rejects)


def test_apply_orders_same_day_retry_idempotent():
    """反例: 相同输入重试 100→200→300。同槽同日同键只成交一次。"""
    slot = _slot(cash=12411.0)
    order = [{"symbol": "510300.SH", "qty": 100, "price": 4.5}]
    px = {"510300.SH": {"price": 4.5}}
    mod.apply_orders(slot, [], order, px)
    cash1, holds1 = slot["cash"], list(slot["holds"])
    rejects = mod.apply_orders(slot, [], order, px)
    assert slot["cash"] == cash1 and slot["holds"] == holds1
    assert any("duplicate_order" in r for r in rejects)


def test_apply_orders_preserves_names_and_row_shape():
    slot = _slot(cash=12411.0, holds=[{"symbol": "512890.SH", "name": "红利低波ETF",
                                       "qty": 100, "price": 1.18, "notional": 118.0}])
    mod.apply_orders(slot, [],
                     buys=[{"symbol": "510500.SH", "name": "中证500ETF", "qty": 100, "price": 6.0}],
                     px={"510500.SH": {"price": 6.0}})
    row = [h for h in slot["holds"] if h["symbol"] == "510500.SH"][0]
    assert row["name"] == "中证500ETF" and row["qty"] == 100
    assert row["notional"] == 600.0
    assert row["held_since"] == mod.EX_DATE.isoformat()   # 当日买入批次带 held_since


# ---------------------------------------------------------------- 转债目标换算
def test_cb_target_lots_are_multiples_of_10():
    # weight 0.25 × 12411 = 3102.75 元; t1_close 126.297 → 3102.75/(126.297*10)=2.45手 → 20张
    lots = int(0.25 * 12411 // (126.297 * 10)) * 10
    assert lots == 20 and lots % 10 == 0


def test_is_trading_session_boundaries():
    from datetime import datetime
    assert mod.is_trading_session(datetime(2026, 9, 29, 9, 30))
    assert mod.is_trading_session(datetime(2026, 9, 29, 11, 30))
    assert not mod.is_trading_session(datetime(2026, 9, 29, 11, 31))  # 午休
    assert not mod.is_trading_session(datetime(2026, 9, 29, 12, 30))  # 午休
    assert mod.is_trading_session(datetime(2026, 9, 29, 13, 0))
    assert mod.is_trading_session(datetime(2026, 9, 29, 15, 10))  # 收盘处理窗
    assert not mod.is_trading_session(datetime(2026, 9, 29, 15, 11))
    assert not mod.is_trading_session(datetime(2026, 9, 26, 10, 0))  # 周六


# ---------------------------------------------------------------- 事件价差规则
def test_event_buy_rule_spread_thresholds():
    # 规则: 现价 < 要约价×(1-最小价差) 才买; 非ST 2%, ST 5%
    offer = 18.0
    assert 17.5 < offer * 0.98          # 非ST: 现价17.5, 价差2.8% → 买
    assert not (17.9 < offer * 0.98)    # 非ST: 现价17.9, 价差0.6% → 否决
    assert 17.0 < offer * 0.95          # ST: 现价17.0, 价差5.6% → 买
    assert not (17.2 < offer * 0.95)    # ST: 现价17.2, 价差4.4% → 否决
    assert not (22.44 < offer * 0.95)   # *ST威领实况: 市价>要约价24.7% → 否决


# ---------------------------------------------------------------- parse_price_from_text
def test_parse_price_tender_offer():
    text = "综合考虑，收购人确定本次要约收购的要约价格为 18.00 元/股。"
    assert mod.parse_price_from_text(text) == 18.0


def test_parse_price_cash_option():
    text = "本次合并中异议股东的现金选择权价格为 12.50 元/股。"
    assert mod.parse_price_from_text(text) == 12.5


def test_parse_price_none_when_absent():
    assert mod.parse_price_from_text("本期无有关价格安排。") is None


# ---------------------------------------------------------------- _chunks
def test_chunks_splits_by_batch_size():
    assert list(mod._chunks(list(range(7)), 5)) == [[0, 1, 2, 3, 4], [5, 6]]
    assert list(mod._chunks([], 5)) == []


# ---------------------------------------------------------------- build_close_summary
def _fake_ledger():
    """18 槽全量 fake 台账（含 Y 逆回购与控制键），供 summary/e2e 等测试离线复用。"""
    return {"start_date": "2026-09-28", "capital_each": 12411.0,
            "marks": [], "_control": {}, "_actions": {},
            "strategies": {
        "A_动量": {"holds": [{"symbol": "512890.SH", "name": "红利低波ETF", "qty": 5200, "price": 1.18}], "cash": 6270.0, "equity": 12406.0},
        "B_轮动": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "E_可转债双低": {"holds": [{"symbol": "113062.SH", "name": "常银转债", "qty": 20, "price": 125.356}], "cash": 2291.74, "equity": 12391.0,
                        "credit_watch": {"min_price": 116.46}},
        "F_小市值20": {"holds": [{"symbol": "SZ301578", "name": "辰奕智能", "qty": 100, "price": 23.02}], "cash": 16.0, "equity": 12376.0},
        "F2_小市值5只神帖对照": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "X_事件套利": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "G_MWU融合": {"holds": [{"symbol": "511010.SH", "name": "国债ETF(防御)", "qty": 50, "price": 140.736}], "cash": 1242.6, "equity": 12396.0},
        "H_MWU_CB融合": {"holds": [{"symbol": "511010.SH", "name": "国债ETF(防御)", "qty": 50, "price": 140.0}], "cash": 411.0, "equity": 12411.0},
        "I_PEAD业绩预告": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "K_项目选股": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "L_北向择时": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "O_行业轮动": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "M_筹码集中度": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "N_技术形态对照": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "J_短期反转": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "C_沪深300持有": {"holds": [{"symbol": "510300.SH", "name": "沪深300ETF", "qty": 2800, "price": 4.416}], "cash": 41.2, "equity": 12406.0},
        "D_红利低波持有": {"holds": [{"symbol": "512890.SH", "name": "红利低波ETF", "qty": 10500, "price": 1.18}], "cash": 16.0, "equity": 12406.0},
        "Y_确定性收益": {"holds": [{"symbol": "SH204001", "name": "GC001逆回购", "qty": 120, "price": 100.0,
                                    "notional": 12000.0, "fixed_value": True,
                                    "maturity_date": "2099-01-01"}],   # 远期到期防分支触发
                        "cash": 411.0, "equity": 12411.0},
    }}


def test_build_close_summary_contains_actions_positions_risk(monkeypatch):
    monkeypatch.setattr(mod, "_is_month_last_trading_day", lambda: False)  # 隔离日历网络
    led = _fake_ledger()
    led["_actions"] = {mod.EX_DATE.isoformat(): ["A_动量 调仓→['510300.SH']"]}
    px = {"512890.SH": {"price": 1.20}, "113062.SH": {"price": 126.0}, "SZ301578": {"price": 23.98}, "510300.SH": {"price": 4.416}}
    out = mod.build_close_summary(led, px)
    assert "▎排名" in out and "▎今日操作" in out and "▎持仓明细" in out and "▎风险" in out
    assert "A_动量 调仓→['510300.SH']" in out
    assert "红利低波ETF 5200股@1.18→1.2" in out          # 成本→现价
    assert "(+1.7%)" in out                              # (1.20/1.18-1)=1.69%
    assert "常银转债 20张" in out                        # 转债单位"张"
    assert "辰奕智能 100股@23.02→23.98(+4.2%)" in out
    assert "B_轮动: 空仓" in out
    assert "信用最低116.46" in out


def test_build_close_summary_flat_when_no_actions(monkeypatch):
    monkeypatch.setattr(mod, "_is_month_last_trading_day", lambda: False)  # 隔离日历网络
    out = mod.build_close_summary(_fake_ledger(), {})
    assert "▎今日操作\n· 无调仓" in out


# ---------------------------------------------------------------- mwu_target_orders
def test_mwu_target_orders_lot_sizes_and_diff():
    # 511010 债券 ETF 也是 100 份/手, 其余 100 股
    target = {"511010.SH": 0.66, "512480.SH": 0.34}
    prices = {"511010.SH": 140.0, "512480.SH": 1.0}
    sells, buys = mod.mwu_target_orders(target, {}, prices, capital=50000)
    # 0.66*50000=33000 / 14000 → 2手=200份; 0.34*50000=17000 / 100 → 17000股
    assert {"symbol": "511010.SH", "qty": 200, "price": 140.0} in buys
    assert {"symbol": "512480.SH", "qty": 17000, "price": 1.0} in buys
    assert sells == []


def test_mwu_target_orders_partial_trim_and_exit():
    # 部分加仓按整手向下取整；目标剔除全卖（卖出允许零股）
    target = {"511010.SH": 0.66}
    prices = {"511010.SH": 140.0, "512480.SH": 1.0}
    sells, buys = mod.mwu_target_orders(target, {"511010.SH": 80, "512480.SH": 1000},
                                        prices, capital=50000)
    assert {"symbol": "511010.SH", "qty": 100, "price": 140.0} in buys  # 80→180（整手）
    assert {"symbol": "512480.SH", "qty": 1000, "price": 1.0} in sells  # 剔除
    assert not any(o["symbol"] == "511010.SH" for o in sells)


def test_mwu_target_orders_skips_missing_price():
    sells, buys = mod.mwu_target_orders({"512480.SH": 1.0}, {}, {})
    assert sells == [] and buys == []


def test_mwu_target_orders_cb_lot_is_ten():
    """H 槽目标可含转债：必须按 10 张/手，不能按 ETF 的 100 股/手。"""
    sells, buys = mod.mwu_target_orders({"113631.SH": 0.5}, {}, {"113631.SH": 100.0},
                                        capital=10000.0)
    assert sells == []
    assert {"symbol": "113631.SH", "qty": 50, "price": 100.0} in buys


# ---------------------------------------------------------------- momentum_target_from_engine
def test_momentum_engine_defensive_mode_full_defensive():
    data = {"defensive_mode": True, "ranked": [], "top_k": 3}
    assert mod.momentum_target_from_engine(data) == {"511010.SH": 1.0}


def test_momentum_engine_ranked_equal_weight():
    data = {"defensive_mode": False, "top_k": 3,
            "ranked": [{"symbol": "512890.SH", "score": 0.08}, {"symbol": "511880.SH", "score": 0.002}]}
    assert mod.momentum_target_from_engine(data) == {"512890.SH": 0.5, "511880.SH": 0.5}


def test_momentum_engine_empty_ranked_yields_empty():
    assert mod.momentum_target_from_engine({"defensive_mode": False, "ranked": [], "top_k": 3}) == {}


# ---------------------------------------------------------------- apply_orders 部分减仓
def test_apply_orders_partial_sell_keeps_remaining():
    slot = _slot(cash=0.0, holds=[{"symbol": "512890.SH", "name": "红利低波ETF", "qty": 5200, "price": 1.18, "notional": 6136.0}])
    mod.apply_orders(slot, sells=[{"symbol": "512890.SH", "qty": 100, "price": 1.196}], buys=[],
                     px={"512890.SH": {"price": 1.196}})
    assert len(slot["holds"]) == 1
    assert slot["holds"][0]["qty"] == 5100            # 扣量保留, 不清空
    assert slot["holds"][0]["price"] == 1.18          # 成本价不变
    assert abs(slot["cash"] - (100 * 1.196 - 5.0)) < 0.01


def test_apply_orders_full_sell_removes():
    slot = _slot(cash=0.0, holds=[{"symbol": "600000.SH", "name": "浦发银行", "qty": 100, "price": 2.0, "notional": 200.0}])
    mod.apply_orders(slot, sells=[{"symbol": "600000.SH", "qty": 100, "price": 2.0}], buys=[],
                     px={"600000.SH": {"price": 2.0}})
    assert slot["holds"] == []
    assert abs(slot["cash"] - (200.0 - 5.0 - 0.1)) < 0.01   # 佣金5 + 股票印花0.1


def test_apply_orders_sell_without_quote_rejected():
    slot = _slot(cash=0.0, holds=[{"symbol": "512890.SH", "name": "红利低波ETF", "qty": 5200, "price": 1.18, "notional": 6136.0}])
    rejects = mod.apply_orders(slot, sells=[{"symbol": "512890.SH", "qty": 100, "price": 1.196}], buys=[], px={})
    assert slot["holds"][0]["qty"] == 5200            # 缺报价不成交
    assert any("no_quote" in r for r in rejects)


# ---------------------------------------------------------------- pe_percentile / erp_decision
def test_pe_percentile_window_and_sanity():
    hist = [20.0 - i * 0.001 for i in range(3000)]  # 缓慢下降的10年PE
    assert mod.pe_percentile(hist, 19.9) > 90        # 高PE → 高分位
    assert mod.pe_percentile(hist, 9.0) < 10         # 低PE → 低分位
    assert mod.pe_percentile([], 15.0) == -1.0       # 样本不足


def test_erp_decision_hysteresis():
    assert mod.erp_decision(85, "normal") == "enter_defensive"
    assert mod.erp_decision(70, "normal") == "hold"          # 中性区不动
    assert mod.erp_decision(50, "defensive") == "exit_defensive"
    assert mod.erp_decision(65, "defensive") == "hold"       # 滞回: 未到60不退出
    assert mod.erp_decision(-1, "normal") == "no_data"


# ---------------------------------------------------------------- select_with_buffer
def test_select_with_buffer_keeps_and_fills():
    ranked = [f"S{i:02d}" for i in range(30)]
    holds = ["S25", "S03"]                    # S25 排名26(≤40保留), S03 排名4
    keep, new = mod.select_with_buffer(holds, ranked, buffer=40, slots=5)
    assert keep == ["S25", "S03"]             # 按holds顺序保留
    assert new == ["S00", "S01", "S02"]       # 空位按排名补新(跳过已保留)
    assert len(keep) + len(new) == 5


def test_select_with_buffer_drops_out_of_buffer():
    ranked = [f"S{i:02d}" for i in range(30)]
    holds = ["S28"]                           # 排名29 > buffer 10 → 不保留
    keep, new = mod.select_with_buffer(holds, ranked, buffer=10, slots=5)
    assert keep == []
    assert new == ranked[:5]


# ---------------------------------------------------------------- slot_drawdown
def test_slot_drawdown_peak_to_current():
    marks = [{"equity": {"A": 100}}, {"equity": {"A": 120}}, {"equity": {"A": 105}}]
    assert mod.slot_drawdown(marks, "A", 105) == 12.5   # (105/120-1)
    assert mod.slot_drawdown(marks, "A", 130) == 0.0    # 新高无回撤
    assert mod.slot_drawdown([], "A", 100) == 0.0


# ---------------------------------------------------------------- 端到端月度大考回归（固化 10-08 预演）
def test_end_to_end_monthly_rebalance(tmp_path, monkeypatch):
    """固化 10-08 预演: fake 台账 + mock 交易所时间/行情/引擎子进程, 验证月度调仓全链不崩。
    全程离线——不读真实台账、不触任何网络（日历/快照/公告/转债数据源均 mock）。"""
    import json as _json
    from datetime import date as _date, datetime as _dt
    from types import SimpleNamespace

    # fake 台账副本到 tmp（不碰真台账）
    led = _fake_ledger()
    for k in ("rebalance_done", "close_done", "erp_last_check"):
        led.setdefault("_control", {}).pop(k, None)
    test_led = tmp_path / "ledger.json"
    test_led.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", test_led)

    # mock 时间与交易日历（15:00 撮合窗内: 集合竞价收盘前, 成交闸放行）
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 8))
    monkeypatch.setattr(mod, "exchange_now", lambda: _dt(2026, 10, 8, 15, 0))
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod, "_is_month_last_trading_day", lambda: False)
    monkeypatch.setattr(mod, "_is_month_first_trading_day", lambda: True)   # 月度块执行
    monkeypatch.setattr(mod, "_is_week_first_trading_day", lambda: True)    # 周度块执行(保 calls>=1)
    monkeypatch.setattr(mod, "notify", lambda *a, **k: False)
    # mock 行情（全部 1.00, 无涨跌）
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 1.0, "pct": 0.0, "source": "mock"} for c in codes})
    monkeypatch.setattr(mod, "tencent_quotes",
                        lambda codes: {c.replace("sh", "").replace("sz", ""):
                                       {"price": 100.0, "pct": 0.0, "name": "mock"} for c in codes})
    # mock 引擎子进程输出（etf_momentum/etf_mwu）
    engine_out = _json.dumps({"as_of": "2026-10-08", "ranked": [], "top_k": 3, "defensive_mode": True,
                              "target": {"511010.SH": 0.66}, "expert_weights": {}})
    calls = {"n": 0}
    class FakeProc:
        stdout = engine_out
    def fake_run(*a, **k):
        calls["n"] += 1
        return FakeProc()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    # mock 小市值选股数据
    fake_recs = [{"ts_code": "301578.SZ", "name": "辰奕智能", "circ_mv": 100, "close": 1.0},
                 {"ts_code": "000820.SZ", "name": "神雾节能", "circ_mv": 101, "close": 1.0}]
    monkeypatch.setattr(mod, "smallcap_universe", lambda *a, **k: fake_recs)
    # ERP/E2/E止盈 mock(单测已覆盖 erp_decision/e2 决策)
    monkeypatch.setattr(mod, "erp_gate", lambda led, px, dry: [])
    monkeypatch.setattr(mod, "e2_gate", lambda led, px, dry: [])
    monkeypatch.setattr(mod, "e_take_profit_pass", lambda led, px, dry: [])
    # 隔离公告流/快照重建/转债数据源（均为真实网络调用点）
    monkeypatch.setattr(mod, "event_round", lambda led, dry: [])
    monkeypatch.setattr(mod, "watchlist_round", lambda led, dry: [])
    monkeypatch.setattr(mod, "maybe_refresh_snapshot", lambda dry: "")
    monkeypatch.setattr(mod, "cb_rebalance", lambda led, px, dry: [])
    monkeypatch.setattr(mod, "ts_client",
                        lambda: SimpleNamespace(call=lambda *a, **k: None))
    monkeypatch.setattr(mod, "fetch_recent_announcements", lambda *a, **k: [])  # risk_round 信用公告流
    monkeypatch.setattr(mod, "pead_universe", lambda *a, **k: [])
    monkeypatch.setattr(mod, "buywatch_universe", lambda *a, **k: [])
    monkeypatch.setattr(mod, "reversal_universe", lambda *a, **k: [])
    monkeypatch.setattr(mod, "holder_concentration_universe", lambda *a, **k: [])
    monkeypatch.setattr(mod, "industry_momentum_universe", lambda *a, **k: [])
    monkeypatch.setattr(mod, "sequoia_ready", lambda: False)
    monkeypatch.setattr(mod, "north_rebalance", lambda led, px, dry: ["L_北向(dry)"])
    monkeypatch.setattr(mod, "daily_dividend_pass", lambda led, px, dry: [])

    out = mod.run_intraday(dry=False)  # 完整真实链: intraday 尾部自动触发 close(与 10-08 真实路径一致)

    new_led = _json.loads(mod.LEDGER.read_text(encoding="utf-8"))
    assert new_led["_control"]["close_done"] == "2026-10-08"
    assert new_led["_control"]["rebalance_done"].get("2026-10") == "all"
    last = new_led["marks"][-1]
    assert last["date"] == "2026-10-08"
    # F 槽: 缓冲带保留旧持仓(rank≤40) + 补新 → 不会崩
    assert "F_小市值20" in last["equity"]
    assert "G_MWU融合" in last["equity"]
    # 引擎被调用 ≥1 次(周度 A/G + 月度 H 均走引擎子进程)
    assert calls["n"] >= 1
    # Y 槽面值模型不被污染
    assert new_led["strategies"]["Y_确定性收益"]["equity"] >= 12000
    assert "月度再平衡" in out or "无调仓" in out


# ------------------------------------------------- 月度注册表(子任务幂等/失败重试/顺延)
def _monthly_env(tmp_path, monkeypatch, fills_time, fail=()):
    """月度块聚焦环境: fake 台账 + 全策略/数据源 mock, 返回 (台账路径, 各策略调用计数)。"""
    import json as _json
    from datetime import date as _date

    led = _fake_ledger()
    led["_control"].pop("rebalance_done", None)
    p = tmp_path / "monthly.json"
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", p)
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 8))
    monkeypatch.setattr(mod, "exchange_now", lambda: fills_time)
    monkeypatch.setattr(mod, "_session_now", fills_time, raising=False)
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod, "_is_month_last_trading_day", lambda: False)
    monkeypatch.setattr(mod, "_is_week_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "_is_month_first_trading_day", lambda: True)
    monkeypatch.setattr(mod, "maybe_refresh_snapshot", lambda *a, **k: "")
    monkeypatch.setattr(mod, "slot_prices",
                        lambda led: {"510300.SH": {"price": 4.0}, "512890.SH": {"price": 1.0}})
    monkeypatch.setattr(mod, "notify", lambda *a, **k: False)
    for fn in ("repo_maturity_pass", "daily_dividend_pass", "erp_gate", "e2_gate",
               "e_take_profit_pass", "north_rebalance"):
        monkeypatch.setattr(mod, fn, lambda *a, **k: [])
    strat = ("smallcap_rebalance", "cb_rebalance", "mwu_rebalance", "pead_rebalance",
             "reversal_rebalance", "buywatch_rebalance", "holder_rebalance",
             "industry_rebalance", "sequoia_scan")
    calls = {fn: 0 for fn in strat}

    def _seam(fn):
        def f(*a, **k):
            calls[fn] += 1
            return ["【异常】fixture source failed"] if fn in fail else []
        return f

    for fn in strat:
        monkeypatch.setattr(mod, fn, _seam(fn))
    return p, calls


def test_monthly_registry_failure_not_all_and_retry_next_day(tmp_path, monkeypatch):
    """失败子任务不得登记 "all"; 次日列表门只补做失败项, 不重跑已成功项。"""
    import json as _json

    p, calls = _monthly_env(tmp_path, monkeypatch, datetime(2026, 10, 8, 10, 0),
                            fail=("smallcap_rebalance",))
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    done = saved["_control"]["rebalance_done"]["2026-10"]
    assert done != "all" and isinstance(done, list)
    assert "smallcap20" not in done and "smallcap5" not in done
    assert {"cb", "mwu_cb", "pead", "reversal", "buywatch",
            "holder", "industry", "sequoia"} <= set(done)

    # 次日(非月首交易日): 列表门放行, 只补做失败子任务
    monkeypatch.setattr(mod, "_is_month_first_trading_day", lambda: False)
    retried = {"n": 0}

    def _ok_smallcap(*a, **k):
        retried["n"] += 1
        return []
    monkeypatch.setattr(mod, "smallcap_rebalance", _ok_smallcap)
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["rebalance_done"]["2026-10"] == "all"
    assert retried["n"] == 2            # 两个小市值子任务补做
    assert calls["cb_rebalance"] == 1   # 已成功项不重跑(仅首轮 1 次)


def test_open_runs_due_registries_without_marks_or_close_done(tmp_path, monkeypatch):
    """开盘窗执行月度注册表，但不写 marks/close_done；收盘轮仍负责估值。"""
    import json as _json

    p, calls = _monthly_env(tmp_path, monkeypatch, datetime(2026, 10, 8, 9, 31))
    led = _json.loads(p.read_text(encoding="utf-8"))
    led["_control"]["bootstrap_done"] = {"once": "all"}
    led["_control"]["rebalance_done"] = {"2026-10": []}
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "_is_month_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "erp_scale_pass", lambda *a, **k: [])
    monkeypatch.setattr(mod, "_open_fills_ok", lambda now=None: True)
    monkeypatch.setattr(mod, "maybe_refresh_snapshot", lambda dry: "快照已刷新(至 2026-10-23)")

    out = mod.run_open(False)

    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert "开盘建仓" in out
    assert "快照已刷新" in out
    assert saved["_control"]["rebalance_done"]["2026-10"] == "all"
    assert sum(calls.values()) > 0
    assert saved["marks"] == []
    assert saved["_control"].get("close_done") is None


def test_open_outside_window_is_noop(tmp_path, monkeypatch):
    import json as _json

    p, calls = _monthly_env(tmp_path, monkeypatch, datetime(2026, 10, 8, 10, 30))
    before = p.read_text(encoding="utf-8")
    monkeypatch.setattr(mod, "_open_fills_ok", lambda now=None: False)

    out = mod.run_open(False)

    assert "非开盘建仓窗" in out
    assert p.read_text(encoding="utf-8") == before
    assert sum(calls.values()) == 0


def test_open_is_idempotent_after_registries_complete(tmp_path, monkeypatch):
    import json as _json

    p, calls = _monthly_env(tmp_path, monkeypatch, datetime(2026, 10, 8, 9, 31))
    led = _json.loads(p.read_text(encoding="utf-8"))
    led["_control"]["bootstrap_done"] = {"once": "all"}
    led["_control"]["rebalance_done"] = {"2026-10": []}
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "_is_month_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "erp_scale_pass", lambda *a, **k: [])
    monkeypatch.setattr(mod, "_open_fills_ok", lambda now=None: True)

    mod.run_open(False)
    first = _json.loads(p.read_text(encoding="utf-8"))
    first_calls = sum(calls.values())
    second_out = mod.run_open(False)
    second = _json.loads(p.read_text(encoding="utf-8"))

    assert "无待建仓" in second_out or "无调仓" in second_out
    assert sum(calls.values()) == first_calls
    assert second["_control"]["rebalance_done"]["2026-10"] == "all"
    assert second["marks"] == first["marks"]


def test_open_defers_engine_slots_on_period_first_day(tmp_path, monkeypatch):
    """周首/月首开盘无当日 bar：A/G/H 的引擎目标必须留到收盘，不能登记成功。"""
    import json as _json

    p, calls = _monthly_env(tmp_path, monkeypatch, datetime(2026, 10, 8, 9, 31))
    led = _json.loads(p.read_text(encoding="utf-8"))
    led["_control"]["bootstrap_done"] = {"once": "all"}
    led["_control"]["rebalance_done"] = {}
    led["_control"]["week_rebalance_done"] = {}
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    wcalls = {"momentum": 0, "rotation": 0}

    def _momentum(*a, **k):
        wcalls["momentum"] += 1
        return []

    def _rotation(*a, **k):
        wcalls["rotation"] += 1
        return []

    monkeypatch.setattr(mod, "_is_week_first_trading_day", lambda: True)
    monkeypatch.setattr(mod, "momentum_signal", _momentum)
    monkeypatch.setattr(mod, "rotation_signal", _rotation)
    monkeypatch.setattr(mod, "erp_scale_pass", lambda *a, **k: [])
    monkeypatch.setattr(mod, "_open_fills_ok", lambda now=None: True)

    out = mod.run_open(False)

    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert "引擎派生槽" in out and "顺延" in out
    assert calls["mwu_rebalance"] == 0          # G/H both deferred
    assert wcalls["momentum"] == 0              # A deferred
    assert wcalls["rotation"] == 1              # B is snapshot-based, safe at open
    assert calls["cb_rebalance"] == 1           # E does not need today's bar
    assert saved["_control"]["rebalance_done"]["2026-10"] != "all"
    assert "mwu_cb" not in saved["_control"]["rebalance_done"]["2026-10"]
    assert saved["_control"]["week_rebalance_done"]["2026-W41"] == ["rotation"]


def test_monthly_registry_deferred_outside_fills_window(tmp_path, monkeypatch):
    """非撮合时段(15:05 补结算轮): 只顺延不执行; 空列表占位且不覆盖已有部分完成记录。"""
    import json as _json

    p, calls = _monthly_env(tmp_path, monkeypatch, datetime(2026, 10, 8, 15, 5))
    out = mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["rebalance_done"]["2026-10"] == []   # 顺延占位
    assert sum(calls.values()) == 0                               # 不执行任何子任务
    assert "顺延" in out
    assert saved["marks"][-1]["date"] == "2026-10-08"             # 结算不受顺延影响

    # 已有部分完成记录不被顺延覆盖
    led = _json.loads(p.read_text(encoding="utf-8"))
    led["_control"]["rebalance_done"]["2026-10"] = ["cb"]
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["rebalance_done"]["2026-10"] == ["cb"]
    assert sum(calls.values()) == 0


# ------------------------------------------------- 周度注册表(与月度同构/消息据实)
def _weekly_env(tmp_path, monkeypatch, fills_time, fail=(), defer=()):
    """周度块聚焦环境: fake 台账 + 周频三槽 mock, 返回 (台账路径, 各策略调用计数)。"""
    import json as _json
    from datetime import date as _date

    led = _fake_ledger()
    led["_control"].pop("week_rebalance_done", None)
    p = tmp_path / "weekly.json"
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", p)
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 19))   # 周一 = 周首交易日
    monkeypatch.setattr(mod, "exchange_now", lambda: fills_time)
    monkeypatch.setattr(mod, "_session_now", fills_time, raising=False)
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod, "_is_month_last_trading_day", lambda: False)
    monkeypatch.setattr(mod, "_is_month_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "_is_week_first_trading_day", lambda: True)
    monkeypatch.setattr(mod, "maybe_refresh_snapshot", lambda *a, **k: "")
    monkeypatch.setattr(mod, "slot_prices",
                        lambda led: {"510300.SH": {"price": 4.0}, "512890.SH": {"price": 1.0}})
    monkeypatch.setattr(mod, "notify", lambda *a, **k: False)
    monkeypatch.setattr(mod, "monthly_report", lambda *a, **k: None)
    for fn in ("repo_maturity_pass", "daily_dividend_pass", "erp_gate", "e2_gate",
               "e_take_profit_pass", "north_rebalance", "erp_scale_pass"):
        monkeypatch.setattr(mod, fn, lambda *a, **k: [])
    calls = {"momentum": 0, "rotation": 0, "mwu": 0}

    def _seam(name):
        def f(*a, **k):
            calls[name] += 1
            if name in fail:
                return ["【异常】fixture engine failed"]
            if name in defer:
                return ["【顺延】fixture deferred"]
            return [f"{name} 调仓→fixture"]
        return f

    monkeypatch.setattr(mod, "momentum_signal", _seam("momentum"))
    monkeypatch.setattr(mod, "rotation_signal", _seam("rotation"))
    monkeypatch.setattr(mod, "mwu_rebalance", _seam("mwu"))
    return p, calls


def test_weekly_registry_defers_outside_fills_window_then_completes(tmp_path, monkeypatch):
    """周频窗口只有"周首交易日"一个: 非撮合时段须登记待续做, 并在**同周后续交易日**补齐。

    旧实现为裸调用 + 只记 log: 错过 15:00 整点(如机器补跑轮)即整周丢失且无重试,
    与月频注册表/总闸的"次日续做"语义不一致。
    """
    import json as _json
    from datetime import date as _date

    p, calls = _weekly_env(tmp_path, monkeypatch, datetime(2026, 10, 19, 22, 5))  # 补跑轮: 撮合窗已关
    out = mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["week_rebalance_done"]["2026-W43"] == []   # 顺延占位, 未执行
    assert sum(calls.values()) == 0
    assert "顺延" in out

    # 同周次日: 撮合窗内 → 三个子任务各执行一次并登记 "all"
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 20))
    monkeypatch.setattr(mod, "_is_week_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 20, 14, 40))
    monkeypatch.setattr(mod, "_session_now", datetime(2026, 10, 20, 14, 40), raising=False)
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["week_rebalance_done"]["2026-W43"] == "all"
    assert calls == {"momentum": 1, "rotation": 1, "mwu": 1}

    # 幂等: 同周再跑一轮不重跑已成功子任务
    mod.run_close(False)
    assert calls == {"momentum": 1, "rotation": 1, "mwu": 1}


def test_weekly_registry_failure_not_all_and_retried_same_week(tmp_path, monkeypatch):
    """失败子任务不得登记 "all"; 同周续做只补失败项。"""
    import json as _json
    from datetime import date as _date

    p, calls = _weekly_env(tmp_path, monkeypatch, datetime(2026, 10, 19, 14, 40),
                           fail=("rotation",))
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    done = saved["_control"]["week_rebalance_done"]["2026-W43"]
    assert done != "all" and done == ["momentum", "mwu"]

    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 21))
    monkeypatch.setattr(mod, "_is_week_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 21, 10, 0))
    monkeypatch.setattr(mod, "_session_now", datetime(2026, 10, 21, 10, 0), raising=False)

    def _ok_rotation(*a, **k):        # 补做轮: 失败项恢复可用
        calls["rotation"] += 1
        return []
    monkeypatch.setattr(mod, "rotation_signal", _ok_rotation)
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["week_rebalance_done"]["2026-W43"] == "all"
    assert calls == {"momentum": 1, "rotation": 2, "mwu": 1}   # 只补 rotation


def test_weekly_registry_defers_when_subtask_reports_deferral(tmp_path, monkeypatch):
    """子任务自报【顺延】不得登记为成功；同周后续交易日必须重试。"""
    import json as _json
    from datetime import date as _date

    p, calls = _weekly_env(tmp_path, monkeypatch, datetime(2026, 10, 19, 14, 40),
                           defer=("rotation",))
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    done = saved["_control"]["week_rebalance_done"]["2026-W43"]
    assert done != "all" and done == ["momentum", "mwu"]
    assert calls == {"momentum": 1, "rotation": 1, "mwu": 1}

    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 20))
    monkeypatch.setattr(mod, "_is_week_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 20, 14, 40))
    monkeypatch.setattr(mod, "_session_now", datetime(2026, 10, 20, 14, 40), raising=False)
    def _retry_rotation(*a, **k):
        calls["rotation"] += 1
        return ["rotation 调仓→fixture"]
    monkeypatch.setattr(mod, "rotation_signal", _retry_rotation)
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["week_rebalance_done"]["2026-W43"] == "all"
    assert calls == {"momentum": 1, "rotation": 2, "mwu": 1}


def test_weekly_slot_message_is_honest_about_non_fill(monkeypatch):
    """未实际成交不得声称"调仓→"（仓库硬约束）。以 B_轮动 三种路径为例。"""
    import pandas as pd
    monkeypatch.setattr(mod, "_session_now", None, raising=False)

    def _feeds(n=30):
        out = {}
        for sym in ("518880.SH", "159915.SZ", "510300.SH"):
            rows = [{"date": pd.Timestamp("2026-09-01") + pd.Timedelta(days=i),
                     "close": 4.0, "open": 4.0, "high": 4.0, "low": 4.0,
                     "volume": 1e6, "amount": 2e8} for i in range(n)]
            rows[-1]["close"] = 4.0
            out[sym] = pd.DataFrame(rows)
        out["510300.SH"].loc[n - 1, "close"] = 4.8      # 唯一正动量 → 必选 510300
        return out

    monkeypatch.setattr(mod, "_load_snapshot", _feeds)
    px = {"510300.SH": {"price": 4.8}}

    # 1) 撮合窗已关: 不成交, 不得说"调仓→"
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 19, 22, 5))
    monkeypatch.setattr(mod, "apply_orders", lambda *a, **k: [])
    msg = mod.rotation_signal(_fake_ledger(), px, dry=False)[0]
    assert msg.startswith("【顺延】")            # 注册表据此前缀判定"未完成"
    assert "调仓→" not in msg and "未成交" in msg
    assert "待续做" in msg

    # 2) 撮合窗内但被拒: 标注部分未成交
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 19, 14, 40))
    monkeypatch.setattr(mod, "apply_orders",
                        lambda *a, **k: ["BUY 510300.SH x2500 拒绝[SESSION_CLOSED] x"])
    msg = mod.rotation_signal(_fake_ledger(), px, dry=False)[0]
    assert "调仓→" in msg and "部分未成交" in msg

    # 3) 撮合窗内且全成交: 维持原有"调仓→"表述
    monkeypatch.setattr(mod, "apply_orders", lambda *a, **k: [])
    msg = mod.rotation_signal(_fake_ledger(), px, dry=False)[0]
    assert "调仓→" in msg and "未成交" not in msg


# ------------------------------------------------- 基准一次性建仓(买入持有 C/D)
def _boot_env(tmp_path, monkeypatch, fills_time):
    """基准建仓聚焦环境: fake 台账(清空 C/D) + 全链 mock, 返回台账路径。"""
    import json as _json
    from datetime import date as _date

    led = _fake_ledger()
    for n in ("C_沪深300持有", "D_红利低波持有"):
        led["strategies"][n] = {"holds": [], "cash": 12411.0, "equity": 12411.0}
    led["_control"].pop("bootstrap_done", None)
    p = tmp_path / "boot.json"
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", p)
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 20))
    monkeypatch.setattr(mod, "exchange_now", lambda: fills_time)
    monkeypatch.setattr(mod, "_session_now", fills_time, raising=False)
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod, "_is_month_last_trading_day", lambda: False)
    monkeypatch.setattr(mod, "_is_month_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "_is_week_first_trading_day", lambda: False)
    monkeypatch.setattr(mod, "maybe_refresh_snapshot", lambda *a, **k: "")
    monkeypatch.setattr(mod, "slot_prices",
                        lambda led: {"510300.SH": {"price": 4.4}, "512890.SH": {"price": 1.18}})
    monkeypatch.setattr(mod, "notify", lambda *a, **k: False)
    monkeypatch.setattr(mod, "monthly_report", lambda *a, **k: None)
    for fn in ("repo_maturity_pass", "daily_dividend_pass", "erp_gate", "e2_gate",
               "e_take_profit_pass", "north_rebalance", "erp_scale_pass"):
        monkeypatch.setattr(mod, fn, lambda *a, **k: [])
    return p


def test_benchmark_bootstrap_builds_c_and_d_once(tmp_path, monkeypatch):
    """买入持有基准 C/D 必须被建仓（无周/月/事件注册表覆盖），且仅一次（幂等）。

    新 cohort 开账清空持仓后，C 恒为现金 → "主动槽跑输 C 即降级"的评价纪律失效。
    """
    import json as _json

    p = _boot_env(tmp_path, monkeypatch, datetime(2026, 10, 20, 10, 0))   # 撮合窗内
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["bootstrap_done"]["once"] == "all"
    c, d = saved["strategies"]["C_沪深300持有"], saved["strategies"]["D_红利低波持有"]
    assert c["holds"] and c["holds"][0]["symbol"] == "510300.SH"
    assert d["holds"] and d["holds"][0]["symbol"] == "512890.SH"
    assert c["cash"] < 12411.0 and d["cash"] < 12411.0          # 现金已投入
    qty_c = c["holds"][0]["qty"]

    # 幂等: 再跑一轮不重复建仓
    mod.run_close(False)
    saved2 = _json.loads(p.read_text(encoding="utf-8"))
    assert saved2["strategies"]["C_沪深300持有"]["holds"][0]["qty"] == qty_c
    assert saved2["strategies"]["C_沪深300持有"]["cash"] == c["cash"]


def test_benchmark_bootstrap_defers_outside_fills_window(tmp_path, monkeypatch):
    """非撮合时段（如机器补跑轮 22:00）只顺延不建仓，后续撮合窗续做。"""
    import json as _json

    p = _boot_env(tmp_path, monkeypatch, datetime(2026, 10, 20, 22, 0))
    out = mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["bootstrap_done"]["once"] == []      # 顺延占位
    assert saved["strategies"]["C_沪深300持有"]["holds"] == []
    assert "顺延" in out


# ---------------------------------------------------------------- ERP 缩放执行侧
def test_erp_scale_pass_defensive_halves_and_marks():
    """防御态首日: 主动槽 qty//2 卖出并标记; cb 槽不在作用域; 二次调用幂等。"""
    led = _fake_ledger()
    led["_control"]["erp_state"] = "defensive"
    px = {"512890.SH": {"price": 1.18}, "SZ301578": {"price": 23.02}, "511010.SH": {"price": 140.0}}
    msgs = mod.erp_scale_pass(led, px, False)
    assert led["strategies"]["A_动量"]["holds"][0]["qty"] == 2600
    assert led["strategies"]["F_小市值20"]["holds"][0]["qty"] == 50
    assert led["strategies"]["G_MWU融合"]["holds"][0]["qty"] == 25
    # cb 槽(E/H)不受 PE 分位总闸, 持仓不变
    assert led["strategies"]["E_可转债双低"]["holds"][0]["qty"] == 20
    assert led["strategies"]["H_MWU_CB融合"]["holds"][0]["qty"] == 50
    assert any("主动槽减半" in m for m in msgs)
    assert led["strategies"]["A_动量"].get("erp_note")
    # 全部 11 个主动槽登记(空仓槽视为已缩放); E/H/L/C/D/X/Y 不在
    assert set(led["_control"]["erp_scaled"]) == set(mod._erp_scale_slots())
    assert "E_可转债双低" not in led["_control"]["erp_scaled"]
    # 幂等: 已标记槽不再减半
    assert mod.erp_scale_pass(led, px, False) == []
    assert led["strategies"]["A_动量"]["holds"][0]["qty"] == 2600


def test_erp_scale_pass_reject_defers_to_next_day():
    """T+1 拒单: 不标记+消息注明待次日补做; 次日可卖后补做成功。"""
    led = _fake_ledger()
    led["_control"]["erp_state"] = "defensive"
    for s in ("A_动量", "F_小市值20"):   # 聚焦 G: 清掉其他可卖持仓
        led["strategies"][s]["holds"] = []
    led["strategies"]["G_MWU融合"]["holds"] = [
        {"symbol": "511010.SH", "name": "国债ETF", "qty": 200, "price": 140.0,
         "held_since": mod.EX_DATE.isoformat()}]              # 当日买入: T+1 不可卖
    px = {"511010.SH": {"price": 140.0}}
    msgs = mod.erp_scale_pass(led, px, False)
    assert led["strategies"]["G_MWU融合"]["holds"][0]["qty"] == 200     # 未减仓
    assert "G_MWU融合" not in led["_control"]["erp_scaled"]             # 未标记
    assert any("待次日补做" in m for m in msgs)
    # 次日(旧批次可卖): 补做成功
    led["strategies"]["G_MWU融合"]["holds"][0]["held_since"] = "2000-01-01"
    msgs2 = mod.erp_scale_pass(led, px, False)
    assert led["strategies"]["G_MWU融合"]["holds"][0]["qty"] == 100
    assert led["_control"]["erp_scaled"]["G_MWU融合"] == mod.EX_DATE.isoformat()
    assert any("主动槽减半" in m for m in msgs2)


def test_erp_scale_pass_normal_noop():
    """非防御态: 空操作, 不建 erp_scaled 登记也不动持仓。"""
    led = _fake_ledger()
    assert mod.erp_scale_pass(led, {}, False) == []
    assert "erp_scaled" not in led["_control"]
    assert led["strategies"]["A_动量"]["holds"][0]["qty"] == 5200


def test_erp_rescale_after_monthly_rebuild(tmp_path, monkeypatch):
    """月度重建成功清 erp_scaled 标记 → 同轮 erp_scale_pass 重新应用 ×0.5(重建后重缩放)。"""
    import json as _json

    p, calls = _monthly_env(tmp_path, monkeypatch, datetime(2026, 10, 8, 10, 0))
    led = _json.loads(p.read_text(encoding="utf-8"))
    led["_control"]["erp_state"] = "defensive"
    led["_control"]["erp_scaled"] = {"F_小市值20": "2026-09-30"}
    led["strategies"]["F_小市值20"]["holds"] = [
        {"symbol": "SZ301578", "name": "辰奕智能", "qty": 100, "price": 23.0,
         "held_since": "2026-09-30"}]
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "slot_prices", lambda led: {
        "SZ301578": {"price": 23.0}, "510300.SH": {"price": 4.0}, "512890.SH": {"price": 1.0}})
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["strategies"]["F_小市值20"]["holds"][0]["qty"] == 50                   # 重建清标记后重新减半
    assert saved["_control"]["erp_scaled"]["F_小市值20"] == "2026-10-08"               # 重新登记


# ---------------------------------------------------------------- e2_gate_decision
def test_e2_drawdown_response():
    """新机制: 峰值回撤≥5% 减半, 收窄至<2% 恢复。"""
    # 模拟序列: 峰值 140 → 回撤至 130(跌 7.1%) → 减半
    hist = [{"date": "2026-09-01", "median": 130.0}, {"date": "2026-09-08", "median": 140.0},
            {"date": "2026-09-15", "median": 130.0}]
    peak = max(h["median"] for h in hist)
    cur = 130.0
    dd = (cur / peak - 1)
    assert dd <= -0.05  # 回撤 7.1% → 触发减半
    # 恢复: 中位数回升至 138(回撤仅 1.4%) → 恢复
    cur2 = 138.0
    dd2 = (cur2 / peak - 1)
    assert dd2 >= -0.02  # 回撤 <2% → 恢复满仓


# ---------------------------------------------------------------- E2 穿透月度调仓(顺序冲突修复)
def test_cb_target_lots_e2_defensive_halves():
    targets = {"113062.SH": {"weight": 0.25, "t1_close": 126.297},
               "113631.SH": {"weight": 0.25, "t1_close": 128.839}}
    px = {"113062.SH": {"price": 126.0}, "113631.SH": {"price": 129.0}}
    full = mod.cb_target_lots(targets, px, e2_defensive=False)
    half = mod.cb_target_lots(targets, px, e2_defensive=True)
    # 满仓: 0.25*12411/(126*10)=2手=20张; 减半: 10张
    assert full["113062.SH"] == 20
    assert half["113062.SH"] == 10
    assert all(v % 10 == 0 for v in half.values())


# ---------------------------------------------------------------- e_take_profit_pass
def _e_ledger(holds, cash=2291.74):
    """最小 fake 台账: 仅 E 槽（e_take_profit_pass 只访问该槽）。"""
    return {"marks": [], "_actions": {}, "_control": {},
            "strategies": {"E_可转债双低": {"cash": cash, "holds": holds}}}


def test_e_take_profit_sells_at_130(tmp_path, monkeypatch):
    """E 单券止盈: 现价≥130 卖出, <130 不动。"""
    import json as _json
    from datetime import date as _date
    led = _e_ledger([
        {"symbol": "113062.SH", "name": "常银转债", "qty": 20, "price": 125.0},
        {"symbol": "113691.SH", "name": "和邦转债", "qty": 20, "price": 134.0}])
    test_led = tmp_path / "ledger.json"
    test_led.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", test_led)
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 8))
    monkeypatch.setattr(mod, "notify", lambda *a, **k: False)
    px = {"113062.SH": {"price": 126.0, "pct": 0.0}, "113691.SH": {"price": 134.0, "pct": 0.0}}
    mod.e_take_profit_pass(mod.load_ledger(), px, dry=False)
    new_led = mod.load_ledger()
    holds = {h["symbol"]: h["qty"] for h in new_led["strategies"]["E_可转债双低"]["holds"]}
    assert "113691.SH" not in holds  # ≥130 卖出
    assert holds.get("113062.SH") == 20  # <130 不动


def test_e_take_profit_noop_below_threshold(tmp_path, monkeypatch):
    import json as _json
    from datetime import date as _date
    led = _e_ledger([
        {"symbol": "113062.SH", "name": "常银转债", "qty": 20, "price": 125.0}])
    test_led = tmp_path / "ledger.json"
    test_led.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", test_led)
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 8))
    px = {"113062.SH": {"price": 126.0, "pct": 0.0}}
    mod.e_take_profit_pass(mod.load_ledger(), px, dry=False)
    new_led = mod.load_ledger()
    assert len(new_led["strategies"]["E_可转债双低"]["holds"]) == 1  # 未卖出


# ---------------------------------------------------------------- N 槽 sequoia_ready
def _sequoia_db(path, rows, latest):
    """构造最小 vendor 库: stock_daily(date) 指定行数与最新日期。"""
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE stock_daily (date TEXT, close REAL)")
    con.executemany("INSERT INTO stock_daily VALUES (?, 1.0)",
                    [((latest if i == rows - 1 else "2000-01-01"),) for i in range(rows)])
    con.commit()
    con.close()


def test_sequoia_ready_true_when_rows_and_freshness_met(tmp_path, monkeypatch):
    """N 槽闸门: 行数≥阈值且最新bar在10天内 → 就绪。"""
    from datetime import date as _date
    db = tmp_path / "sequoia_v2.db"
    _sequoia_db(db, rows=3, latest=_date.today().isoformat())
    monkeypatch.setattr(mod, "_SEQUOIA_DB", db)
    monkeypatch.setattr(mod, "_SEQUOIA_MIN_ROWS", 3)
    assert mod.sequoia_ready() is True


def test_sequoia_ready_false_when_rows_insufficient(tmp_path, monkeypatch):
    """行数不足阈值 → 未就绪。"""
    from datetime import date as _date
    db = tmp_path / "sequoia_v2.db"
    _sequoia_db(db, rows=2, latest=_date.today().isoformat())
    monkeypatch.setattr(mod, "_SEQUOIA_DB", db)
    monkeypatch.setattr(mod, "_SEQUOIA_MIN_ROWS", 3)
    assert mod.sequoia_ready() is False


def test_sequoia_ready_false_when_db_missing(tmp_path, monkeypatch):
    """库文件缺失/不可读 → 未就绪（不抛异常）。"""
    monkeypatch.setattr(mod, "_SEQUOIA_DB", tmp_path / "nonexistent.db")
    monkeypatch.setattr(mod, "_SEQUOIA_MIN_ROWS", 3)
    assert mod.sequoia_ready() is False


# ---------------------------------------------------------------- 独占锁属主检查
def test_acquire_lock_four_branches(tmp_path, monkeypatch):
    """① 无锁可创建 ② 属主存活不抢(不论时长) ③ 属主已死清除重试 ④ 内容不可解析保守不抢。"""
    import json as _json
    import os as _os
    lock = tmp_path / "run.lock"
    monkeypatch.setattr(mod, "_LOCK_PATH", lock)

    assert mod.acquire_lock() is True                    # ① 创建并登记属主 pid
    assert _json.loads(lock.read_text(encoding="utf-8"))["pid"] == _os.getpid()
    mod.release_lock()

    lock.write_text(_json.dumps({"pid": _os.getpid(), "started": "now"}), encoding="utf-8")
    assert mod.acquire_lock() is False                   # ② 属主=本进程存活 → 不抢
    mod.release_lock()

    lock.write_text(_json.dumps({"pid": 99999999, "started": "now"}), encoding="utf-8")
    assert mod.acquire_lock() is True                    # ③ 属主已退出 → 残留清除重试
    mod.release_lock()

    lock.write_text("live owner", encoding="utf-8")
    assert mod.acquire_lock() is False                   # ④ 不可解析 → 保守不抢(需人工清理)
    mod.release_lock()


# ---------------------------------------------------------------- 台账备份保护
def test_save_ledger_does_not_destroy_backup_when_main_corrupt(tmp_path, monkeypatch):
    """反例: 主文件 BROKEN、.bak 完好时，save 不得把坏主文件覆盖到唯一好备份。"""
    import json as _json
    good = {"_meta": {"schema_version": 3}, "strategies": {},
            "marks": [{"date": "2026-09-29"}]}
    bak = tmp_path / "ledger.json.bak"
    bak.write_text(_json.dumps(good, ensure_ascii=False), encoding="utf-8")
    main = tmp_path / "ledger.json"
    main.write_text("BROKEN", encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", main)
    mod.save_ledger({"strategies": {}, "marks": []})
    assert _json.loads(bak.read_text(encoding="utf-8")) == good          # 好备份原样保留
    assert _json.loads(main.read_text(encoding="utf-8"))["_meta"]["schema_version"] == 3  # 主文件已重写


def test_save_ledger_rotates_backup_when_main_valid(tmp_path, monkeypatch):
    """正常路径仍滚动备份：好主文件先复制到 .bak 再原子写新内容。"""
    import json as _json
    main = tmp_path / "ledger.json"
    main.write_text(_json.dumps({"_meta": {"schema_version": 3}, "prev": True}),
                    encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", main)
    mod.save_ledger({"strategies": {}})
    bak = _json.loads((tmp_path / "ledger.json.bak").read_text(encoding="utf-8"))
    assert bak.get("prev") is True


# ------------------------------------------------- 日历/时钟（任务06，纯函数）
def test_is_month_last_trading_day_mid_month_not_end(monkeypatch):
    """月初/月中交易日不得被误判为月末（旧实现查询终点=EX_DATE 自身的自证缺陷）。"""
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod._is_month_last_trading_day, "_cache", None, raising=False)
    assert mod._is_month_last_trading_day() is False


def test_is_month_last_trading_day_true_at_month_end(monkeypatch):
    """月末之后当月无交易日时才判定为月末。"""
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 30))
    monkeypatch.setattr(mod, "is_exchange_trading_day",
                        lambda d: d <= date(2026, 10, 30))
    monkeypatch.setattr(mod._is_month_last_trading_day, "_cache", None, raising=False)
    assert mod._is_month_last_trading_day() is True


def test_week_first_cache_keyed_by_date(monkeypatch):
    """周首缓存键必须含具体日期：同进程跨日复调用不得返回前一天的结果。"""
    mod._WEEK_CACHE.clear()
    monkeypatch.setattr(mod, "is_exchange_trading_day",
                        lambda d: d >= date(2026, 10, 8) and d.weekday() < 5)
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))
    first = mod._is_week_first_trading_day()
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 9))
    second = mod._is_week_first_trading_day()
    assert first is True and second is False


def test_prev_month_end_pure(monkeypatch):
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))
    assert mod._prev_month_end() == "20260930"


class _FrozenWall(datetime):
    """冻结墙钟，消除单测对真实日历/时钟的依赖。"""
    _fixed = datetime(2026, 10, 7, 11, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def test_main_inner_wall_date_not_rolled_back_by_stamp(monkeypatch, capsys):
    """休市时旧行情戳不得倒拨运行日期，且不得进入盘中轮。"""
    entered = []
    monkeypatch.setattr(mod, "EX_DATE", date.today())
    monkeypatch.setattr(mod, "datetime", _FrozenWall)               # 墙钟=2026-10-07 11:00
    monkeypatch.setattr(mod, "exchange_now",
                        lambda: datetime(2026, 9, 30, 10, 0))       # 旧行情戳
    monkeypatch.setattr(mod, "run_intraday", lambda dry: entered.append(mod.EX_DATE) or "x")
    monkeypatch.setattr(mod, "_TRADING_OK", True)
    mod._main_inner(__import__("argparse").Namespace(force=False, mode="intraday", dry_run=True))
    assert entered == []                                  # 旧戳宣称盘中 → 拒绝进入
    assert mod.EX_DATE == date(2026, 10, 7)               # 运行日期=墙钟，未被倒拨
    assert "非交易时段" in capsys.readouterr().out


def test_main_inner_exposes_captured_session_to_close(monkeypatch):
    """入口捕获的交易所会话时间必须成为全局，供 run_close 的成交闸直接复用。

    否则 run_close 会二次 exchange_now；慢行情/接口抖动时又回到 10-21 首建仓
    只有 C/D 成交的故障路径。
    """
    class _Frozen1530(_FrozenWall):
        _fixed = datetime(2026, 10, 22, 15, 0, 30)

    stamp = datetime(2026, 10, 22, 15, 0, 5)
    seen = {}

    def _close(dry):
        seen.update(session=mod._session_now, ok=mod._TRADING_OK)
        return "x"

    monkeypatch.setattr(mod, "_session_now", None, raising=False)
    monkeypatch.setattr(mod, "datetime", _Frozen1530)
    monkeypatch.setattr(mod, "exchange_now", lambda: stamp)
    monkeypatch.setattr(mod, "run_close", _close)
    mod._main_inner(__import__("argparse").Namespace(force=False, mode="close", dry_run=True))

    assert seen == {"session": stamp, "ok": True}
    assert mod._session_now == stamp


def test_main_inner_open_returns_nonzero_when_all_slots_failed(monkeypatch, capsys):
    monkeypatch.setattr(mod, "run_open",
                        lambda dry, force=False: "【对比盘开盘建仓】\n【异常】A_动量 目标缺价")
    monkeypatch.setattr(mod, "load_ledger", lambda: {"strategies": {}})
    rc = mod._main_inner(
        __import__("argparse").Namespace(force=False, mode="open", dry_run=False))
    assert rc == 1
    assert "【异常】" in capsys.readouterr().out


def test_main_inner_open_partial_success_remains_zero(monkeypatch):
    monkeypatch.setattr(mod, "run_open",
                        lambda dry, force=False: "【对比盘开盘建仓】\n【异常】G 缺价\nE 调仓→['113062.SH']")
    monkeypatch.setattr(mod, "load_ledger", lambda: {"strategies": {
        "A_动量": {"holds": [{"symbol": "510300.SH", "qty": 100}]}}})
    rc = mod._main_inner(
        __import__("argparse").Namespace(force=False, mode="open", dry_run=False))
    assert rc == 0


def test_main_inner_catchup_after_1510_enters_with_gate_closed(monkeypatch):
    """15:10 后收盘补结算必须被放行进入，且 _TRADING_OK=False（内核拒绝新成交）。"""
    class _Frozen1522(_FrozenWall):
        _fixed = datetime(2026, 10, 8, 15, 22)

    entered = []
    monkeypatch.setattr(mod, "EX_DATE", date.today())
    monkeypatch.setattr(mod, "datetime", _Frozen1522)
    monkeypatch.setattr(mod, "exchange_now",
                        lambda: datetime(2026, 10, 8, 15, 22))
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)  # catch-up 仅对交易日放行
    monkeypatch.setattr(mod, "run_intraday", lambda dry: entered.append("catchup") or "x")
    monkeypatch.setattr(mod, "_TRADING_OK", True)
    mod._main_inner(__import__("argparse").Namespace(force=False, mode="intraday", dry_run=True))
    assert entered == ["catchup"]
    assert mod._TRADING_OK is False


# ------------------------------------------------- 撮合窗与会话闸（任务08 D1/D3）
def test_fills_open_boundaries():
    """撮合窗截 15:00: 15:00 可成交, 15:01-15:10 只结算, 15:22 catch-up 亦不成交。"""
    assert mod._fills_open(datetime(2026, 10, 8, 10, 0)) is True    # 盘中
    assert mod._fills_open(datetime(2026, 10, 8, 15, 0)) is True    # 收盘集合竞价截止
    assert mod._fills_open(datetime(2026, 10, 8, 15, 5)) is False   # 只结算窗
    assert mod._fills_open(datetime(2026, 10, 8, 15, 22)) is False  # catch-up 补结算
    assert mod._fills_open(datetime(2026, 10, 8, 12, 0)) is False   # 午休
    assert mod._fills_open(None) is False                           # 无时间基准


def test_is_exchange_trading_day_weekend_deterministic():
    """周末确定性 False: 不依赖 tushare API 可用性（两套日历降级不一致修复）。"""
    assert mod.is_exchange_trading_day(date(2026, 10, 10)) is False  # 周六
    assert mod.is_exchange_trading_day(date(2026, 10, 11)) is False  # 周日


def test_session_gate_1505_runs_but_fills_closed():
    """15:05 运行窗内正常进入, 但撮合闸关闭（D1: 1501-1510 只结算不成交）。"""
    _, _, ok, reason = mod._session_gate(
        datetime(2026, 10, 8, 15, 5), datetime(2026, 10, 8, 15, 5), force=False)
    assert reason == "" and ok is False


def test_session_gate_catchup_enters_with_fills_closed(monkeypatch):
    """15:22 catch-up 放行运行（交易日）但成交闸关闭。"""
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    ex, _, ok, reason = mod._session_gate(
        datetime(2026, 10, 8, 15, 22), datetime(2026, 10, 8, 15, 22), force=False)
    assert reason == "" and ok is False and ex == date(2026, 10, 8)


def test_session_gate_rejects_overnight_run_against_prev_day_post_close_stamp(monkeypatch):
    """反例(2026-10-20 实测): 凌晨 00:22 腾讯仍返回**前一日盘后**旧戳(10-19 16:14,
    hm=1614>1510) → 原判据 after_close=True，配"运行日=10-20 是交易日"即放行整轮盘中，
    并把 `event_scan_last` 写成 "20261020_AM"，令当日上午(09:30-11:30)的事件/观察池扫描
    被节流跳过。跨越日历日的旧戳不得作为"当日已收盘(可补结算)"的依据。"""
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    # ① 跨日旧戳 → 必须拒绝（即使运行日是交易日）
    _, _, ok, reason = mod._session_gate(
        datetime(2026, 10, 19, 16, 14), datetime(2026, 10, 20, 0, 22), force=False)
    assert reason != "" and ok is False
    # ② 同日盘后 catch-up 仍必须放行（原行为不回归）
    assert mod._session_gate(datetime(2026, 10, 19, 16, 14),
                             datetime(2026, 10, 19, 22, 0), force=False)[3] == ""
    # ③ 无戳（网络故障）回退墙钟：同日盘后仍放行
    assert mod._session_gate(None, datetime(2026, 10, 19, 22, 0), force=False)[3] == ""
    # ④ 无戳 + 凌晨 → 拒绝
    assert mod._session_gate(None, datetime(2026, 10, 20, 0, 22), force=False)[3] != ""
    # ⑤ 同日盘中不受影响
    assert mod._session_gate(datetime(2026, 10, 19, 10, 30),
                             datetime(2026, 10, 19, 10, 30), force=False)[3] == ""
    # ⑥ 跨日旧戳但时分落在盘中窗（如前一交易日 10:30）→ 拒绝
    assert mod._session_gate(datetime(2026, 10, 19, 10, 30),
                             datetime(2026, 10, 20, 0, 22), force=False)[3] != ""


# --------------------------------------------------- 评审修复回归 (B/C/D/F)
def test_e2_pre_defensive_qty_persists_across_calls(tmp_path, monkeypatch):
    """修复 B 反例: 减半前数量登记若存 ew['e2'] 每轮重建即丢 → 买回恒走 cur*2。
    防御期间持仓变动后(20+10 → 减半剩15 → 止盈清出 B → 剩10), 恢复须买回登记的 30 张, 而非 cur*2=20。"""
    import json as _json
    import pandas as pd
    from types import SimpleNamespace

    led = {"capital_each": 12411.0, "marks": [], "_control": {}, "_actions": {},
           "strategies": {"E_可转债双低": {
               "cash": 10000.0,
               "credit_watch": {"median_history": [{"date": "2026-09-30", "median": 140.0}]},
               "holds": [{"symbol": "113062.SH", "name": "常银转债", "qty": 20,
                          "price": 120.0, "notional": 2400.0},
                         {"symbol": "113691.SH", "name": "和邦转债", "qty": 10,
                          "price": 125.0, "notional": 1250.0}]}}}
    test_led = tmp_path / "ledger.json"
    test_led.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", test_led)
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 8, 10, 0))  # 撮合窗内

    seq = [130.0, 130.0, 138.0]           # 减半 → 保持防御 → 回撤收窄恢复
    calls = {"n": 0}

    def fake_call(*a, **k):
        med = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return pd.DataFrame({"trade_date": ["20261008"], "close": [med]})

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=fake_call))
    px = {"113062.SH": {"price": 120.0, "pct": 0.0}, "113691.SH": {"price": 125.0, "pct": 0.0}}

    # 第 1 轮: 触发减半(A 20→10, B 10→5, 剩 15 张); 登记=减半前 30 张
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))
    led1 = mod.load_ledger()
    msgs1 = mod.e2_gate(led1, px, False)
    mod.save_ledger(led1)
    assert any("E 减半" in m for m in msgs1)
    saved1 = mod.load_ledger()
    assert sum(h["qty"] for h in saved1["strategies"]["E_可转债双低"]["holds"]) == 15
    assert saved1["_control"]["e2_state"] == "defensive"
    assert saved1["_control"]["e2_pre_defensive_qty"] == 30      # 登记随台账持久(而非存 ew['e2'])

    # 第 2 轮: 防御保持, 无动作; 期间 B 被止盈清出(模拟), 状态/登记不得丢
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 9))
    led2 = mod.load_ledger()
    mod.e2_gate(led2, px, False)
    led2["strategies"]["E_可转债双低"]["holds"] = [             # 防御期间持仓变化: B 全卖
        h for h in led2["strategies"]["E_可转债双低"]["holds"] if h["symbol"] == "113062.SH"]
    mod.save_ledger(led2)
    saved2 = mod.load_ledger()
    assert saved2["_control"]["e2_state"] == "defensive"
    assert saved2["_control"]["e2_pre_defensive_qty"] == 30
    assert sum(h["qty"] for h in saved2["strategies"]["E_可转债双低"]["holds"]) == 10

    # 第 3 轮: 回撤收窄恢复 → 按登记 30 买回 20 张(10*2=20 是登记丢失后的错误目标)
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 12))
    led3 = mod.load_ledger()
    msgs3 = mod.e2_gate(led3, px, False)
    mod.save_ledger(led3)
    saved3 = mod.load_ledger()
    assert sum(h["qty"] for h in saved3["strategies"]["E_可转债双低"]["holds"]) == 30
    assert saved3["_control"]["e2_state"] == "normal"
    assert "e2_pre_defensive_qty" not in saved3["_control"]       # 恢复成功才清登记
    assert any("恢复满仓" in m for m in msgs3)


def test_e2_exit_buyback_lot_aligned_no_deadlock(tmp_path, monkeypatch):
    """修复反例: 恢复买回旧 round() 按张分配产生零股买单, 被内核 CB LOT_SIZE 恒拒 → 永久防御死锁。
    手单位分配(末位吸收) + 距目标不足1手据实转正常, 两轮内必须收敛。"""
    import json as _json
    import pandas as pd
    from types import SimpleNamespace

    led = {"marks": [], "_actions": {},
           "_control": {"e2_state": "defensive", "e2_pre_defensive_qty": 70},
           "strategies": {"E_可转债双低": {
               "cash": 10000.0,
               "credit_watch": {"median_history": [{"date": "2026-09-30", "median": 140.0}]},
               "holds": [{"symbol": "113062.SH", "name": "常银转债", "qty": 15,
                          "price": 120.0, "notional": 1800.0},
                         {"symbol": "113691.SH", "name": "和邦转债", "qty": 10,
                          "price": 125.0, "notional": 1250.0},
                         {"symbol": "113692.SH", "name": "测试转债", "qty": 10,
                          "price": 125.0, "notional": 1250.0}]}}}
    test_led = tmp_path / "ledger.json"
    test_led.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", test_led)
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 8, 10, 0))
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(
        call=lambda *a, **k: pd.DataFrame({"trade_date": ["20261008"], "close": [138.0]})))
    px = {"113062.SH": {"price": 120.0, "pct": 0.0},
          "113691.SH": {"price": 125.0, "pct": 0.0},
          "113692.SH": {"price": 125.0, "pct": 0.0}}

    # 第 1 轮: deficit=35 → 3手 → 三券各买回 10 张(全为整手, 不再产生零股被拒)
    led1 = mod.load_ledger()
    msgs1 = mod.e2_gate(led1, px, False)
    mod.save_ledger(led1)
    saved1 = mod.load_ledger()
    q1 = sum(h["qty"] for h in saved1["strategies"]["E_可转债双低"]["holds"])
    assert q1 == 65                                        # 35 + 30 整手买回全部成交
    assert saved1["_control"]["e2_state"] == "defensive"   # 未达 70 → 保持防御(登记保留)
    assert any("部分恢复" in m for m in msgs1)

    # 第 2 轮: deficit=5 < 1手 → 据实转正常并清登记(旧实现将永久死锁)
    led2 = mod.load_ledger()
    msgs2 = mod.e2_gate(led2, px, False)
    mod.save_ledger(led2)
    saved2 = mod.load_ledger()
    assert saved2["_control"]["e2_state"] == "normal"
    assert "e2_pre_defensive_qty" not in saved2["_control"]
    assert any("不足1手" in m for m in msgs2)


def test_risk_round_circuit_breaker_suppressed_outside_trading_hours(monkeypatch):
    """修复 C 反例: 15:10 后 _TRADING_OK=False, 熔断卖单必被内核拒绝;
    状态不得迁 watch、仓位不得动、不得发失实消息。"""
    from types import SimpleNamespace

    led = {"_control": {}, "_actions": {}, "strategies": {
        "F_小市值20": {"cash": 0.0, "holds": [{"symbol": "600000.SH", "name": "fixture",
                                              "qty": 100, "price": 10.0, "notional": 1000.0}]},
        "F2_小市值5只神帖对照": {"cash": 0.0, "holds": []},
        "E_可转债双低": {"cash": 0.0, "holds": []}}}
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 8, 15, 22))  # 非交易时段
    monkeypatch.setattr(mod, "_TRADING_OK", False)                                   # catch-up 闸关闭
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=lambda *a, **k: None))
    monkeypatch.setattr(mod, "tencent_quotes",
                        lambda codes: ({"399303": {"price": 1000.0, "pct": 0.0}}
                                       if codes == ["sz399303"] else {}))
    monkeypatch.setattr(mod, "fetch_recent_announcements", lambda *a, **k: [])

    msgs = mod.risk_round(led, {"600000.SH": {"price": 10.0, "pct": -4.2}}, False)
    assert led["_control"].get("crash_state") in (None, "normal")   # 无迁移
    assert "crash_watch_until" not in led["_control"]
    assert sum(h["qty"] for h in led["strategies"]["F_小市值20"]["holds"]) == 100
    assert msgs == []


def test_risk_round_same_day_noise_not_escalated(monkeypatch):
    """修复 D 反例: 观察期内同日行情微变(med -4.5%→-4.9%)不构成新事件, 不得升级清仓。"""
    from types import SimpleNamespace

    led = {"_control": {}, "_actions": {}, "strategies": {
        "F_小市值20": {"cash": 0.0, "holds": [{"symbol": "600000.SH", "name": "fixture",
                                              "qty": 300, "price": 10.0, "notional": 3000.0}]},
        "F2_小市值5只神帖对照": {"cash": 0.0, "holds": []},
        "E_可转债双低": {"cash": 0.0, "holds": []}}}
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 8, 10, 0))   # 盘中
    monkeypatch.setattr(mod, "_TRADING_OK", True)
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=lambda *a, **k: None))
    monkeypatch.setattr(mod, "tencent_quotes",
                        lambda codes: ({"399303": {"price": 1000.0, "pct": 0.0}}
                                       if codes == ["sz399303"] else {}))
    monkeypatch.setattr(mod, "fetch_recent_announcements", lambda *a, **k: [])

    msgs1 = mod.risk_round(led, {"600000.SH": {"price": 10.0, "pct": -4.5}}, False)
    assert led["_control"]["crash_state"] == "watch"
    assert led["_control"]["crash_watch_until"] == "2026-11-07"
    assert led["_control"]["crash_trigger_key"] == "2026-10-08"     # date-only 键
    assert sum(h["qty"] for h in led["strategies"]["F_小市值20"]["holds"]) == 200  # 减 1/3
    assert any("熔断一级" in m for m in msgs1)

    msgs2 = mod.risk_round(led, {"600000.SH": {"price": 9.8, "pct": -4.9}}, False)  # 同日噪声
    assert led["_control"]["crash_state"] == "watch"                 # 不升级
    assert led["_control"]["crash_watch_until"] == "2026-11-07"      # 观察窗不重设
    assert sum(h["qty"] for h in led["strategies"]["F_小市值20"]["holds"]) == 200
    assert not any("熔断二级" in m for m in msgs2)
    assert msgs2 == []


def test_e2_exit_buy_rejected_keeps_defensive_state(tmp_path, monkeypatch):
    """修复 F 反例: 恢复买入被拒(NO_QUOTE)不得声称"恢复满仓"; 状态与登记保持待下轮;
    仅"无持仓可恢复"才据实转正常并清登记。"""
    import json as _json
    import pandas as pd
    from types import SimpleNamespace

    led = {"marks": [], "_actions": {},
           "_control": {"e2_state": "defensive", "e2_pre_defensive_qty": 25},
           "strategies": {"E_可转债双低": {
               "cash": 10000.0,
               "credit_watch": {"median_history": [{"date": "2026-09-30", "median": 140.0}]},
               "holds": [{"symbol": "113062.SH", "name": "常银转债", "qty": 13,
                          "price": 120.0, "notional": 1560.0}]}}}
    test_led = tmp_path / "ledger.json"
    test_led.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", test_led)
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 8))
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 8, 10, 0))  # 撮合窗内
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(
        call=lambda *a, **k: pd.DataFrame({"trade_date": ["20261008"], "close": [138.0]})))

    led1 = mod.load_ledger()
    msgs1 = mod.e2_gate(led1, {}, False)                  # px 空 → 买回 NO_QUOTE 必被拒
    joined1 = " ".join(msgs1)
    assert "恢复满仓" not in joined1                       # 不得声称已恢复
    assert "保持防御" in joined1
    assert led1["_control"]["e2_state"] == "defensive"     # 状态保持
    assert led1["_control"]["e2_pre_defensive_qty"] == 25  # 登记保留待下轮
    assert sum(h["qty"] for h in led1["strategies"]["E_可转债双低"]["holds"]) == 13

    led1["strategies"]["E_可转债双低"]["holds"] = []       # 无持仓可恢复 → 据实转正常
    msgs2 = mod.e2_gate(led1, {}, False)
    assert any("无持仓可恢复" in m for m in msgs2)
    assert led1["_control"]["e2_state"] == "normal"
    assert "e2_pre_defensive_qty" not in led1["_control"]


# ------------------------------------------------- 首建仓 P0: 行情预取/定仓/编码
def test_run_cmd_decodes_utf8_child_output(tmp_path, monkeypatch):
    """任务包装强制子进程输出 UTF-8；父进程必须按 UTF-8 解码，不得静默丢 stdout/stderr。"""
    child = tmp_path / "child_utf8.py"
    child.write_text("import sys\nprint('中文stdout')\nprint('中文stderr', file=sys.stderr)\n",
                     encoding="utf-8")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    r = mod._run_cmd([sys.executable, str(child)], tmp_path, 30)
    assert r.returncode == 0
    assert "中文stdout" in (r.stdout or "")
    assert "中文stderr" in (r.stderr or "")


def test_ensure_prices_uses_tencent_for_cb(monkeypatch):
    """六源链覆盖不到转债：ensure_prices 必须走腾讯直连，否则 E 首建仓 NO_QUOTE。"""
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 0.0, "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "tencent_quotes",
                        lambda codes: {"113631": {"price": 128.5, "pct": 0.1, "name": "测试转债"}})
    px = mod.ensure_prices({}, ["113631.SH"])
    assert px["113631.SH"]["price"] == 128.5
    assert px["113631.SH"]["source"] == "tencent_cb"


def test_ensure_prices_uses_tencent_for_etf_when_chain_misses(monkeypatch):
    """511010 在六源链实测无价；H 首建仓必须由腾讯直连兜底，否则整槽 NO_QUOTE。"""
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 0.0, "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "tencent_quotes",
                        lambda codes: {"511010": {"price": 141.176, "pct": 0.0, "name": "国债ETF"}})
    px = mod.ensure_prices({}, ["511010.SH"])
    assert px["511010.SH"]["price"] == 141.176
    assert px["511010.SH"]["source"] == "tencent_direct"


def test_slot_prices_fills_cb_holdings_via_tencent(monkeypatch):
    led = {"strategies": {"E_可转债双低": {
        "holds": [{"symbol": "113062.SH", "qty": 20, "price": 120.0}], "cash": 0.0}}}
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 0.0, "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "tencent_quotes",
                        lambda codes: {"113062": {"price": 126.0, "pct": 0.2, "name": "常银转债"}})
    px = mod.slot_prices(led)
    assert px["113062.SH"]["price"] == 126.0


def test_momentum_signal_prefetches_target_quotes_and_sizes_on_equity(monkeypatch):
    """空槽首建仓：目标 ETF 必须预取报价；定仓基数为当前权益而非初始本金。"""
    import json as _json
    from types import SimpleNamespace
    monkeypatch.setattr(mod, "_session_now", None, raising=False)

    slot = {"holds": [], "cash": 21000.0, "equity": 21000.0}
    led = {"strategies": {"A_动量": slot}}
    engine = _json.dumps({"as_of": "2026-10-21",
                          "ranked": [{"symbol": "510300.SH"}, {"symbol": "510880.SH"}],
                          "top_k": 2, "defensive_mode": False})
    monkeypatch.setattr(mod, "_run_cmd",
                        lambda cmd, cwd, timeout: SimpleNamespace(
                            stdout=engine, stderr="", returncode=0))
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 10.0, "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 21, 14, 40))
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))
    monkeypatch.setattr(mod, "_TRADING_OK", True)

    msgs = mod.momentum_signal(led, {}, dry=False)
    got = {h["symbol"]: h["qty"] for h in slot["holds"]}
    assert got == {"510300.SH": 1000, "510880.SH": 1000}       # 21000×0.5/10，不是 12411×0.5/10
    assert "调仓→" in msgs[0]


def test_rotation_signal_prefetches_target_quotes_and_sizes_on_equity(monkeypatch):
    import pandas as pd
    monkeypatch.setattr(mod, "_session_now", None, raising=False)

    def _feeds(n=30):
        out = {}
        for sym in ("518880.SH", "159915.SZ", "510300.SH"):
            rows = [{"date": pd.Timestamp("2026-09-01") + pd.Timedelta(days=i),
                     "close": 4.0, "open": 4.0, "high": 4.0, "low": 4.0,
                     "volume": 1e6, "amount": 2e8} for i in range(n)]
            out[sym] = pd.DataFrame(rows)
        out["510300.SH"].loc[n - 1, "close"] = 4.8
        return out

    monkeypatch.setattr(mod, "_load_snapshot", _feeds)
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 4.8 if c == "510300.SH" else 4.0,
                                           "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 21, 14, 40))
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))
    monkeypatch.setattr(mod, "_TRADING_OK", True)
    slot = {"holds": [], "cash": 21000.0, "equity": 21000.0}
    led = {"strategies": {"B_轮动": slot}}

    msgs = mod.rotation_signal(led, {}, dry=False)
    assert slot["holds"] and slot["holds"][0]["symbol"] == "510300.SH"
    assert slot["holds"][0]["qty"] == 4200                      # 21000×0.98/4.8
    assert "调仓→" in msgs[0]


def test_mwu_rebalance_prefetches_target_quotes_and_sizes_on_equity(monkeypatch):
    import json as _json
    from types import SimpleNamespace
    monkeypatch.setattr(mod, "_session_now", None, raising=False)

    slot = {"holds": [], "cash": 21000.0, "equity": 21000.0}
    led = {"strategies": {"G_MWU融合": slot}}
    engine = _json.dumps({"as_of": "2026-10-21", "target": {"510300.SH": 0.9},
                          "expert_weights": {}})
    monkeypatch.setattr(mod, "_run_cmd",
                        lambda cmd, cwd, timeout: SimpleNamespace(
                            stdout=engine, stderr="", returncode=0))
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 10.0, "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 21, 14, 40))
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))
    monkeypatch.setattr(mod, "_TRADING_OK", True)

    msgs = mod.mwu_rebalance(led, {}, dry=False)
    assert slot["holds"] and slot["holds"][0]["qty"] == 1800   # 21000×0.9/10，不是 12411×0.9/10
    assert "调仓→" in msgs[0]


def test_mwu_rebalance_uses_fresh_signal_cache(tmp_path, monkeypatch):
    """14:30 预计算目标命中时不得再跑 10 分钟引擎。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "etf_mwu_cb_20261027.json").write_text(json.dumps({
        "cache_date": "2026-10-27",
        "as_of": "2026-10-08",
        "strategy_config": "config/strategy/etf_mwu_cb_monthly.yaml",
        "target": {"512010.SH": 0.9},
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "SIGNAL_CACHE_DIR", cache)
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 27))
    monkeypatch.setattr(mod, "_session_now", None, raising=False)
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 0.5, "pct": 0.0} for c in codes})
    slot = {"holds": [], "cash": 12411.0, "equity": 12411.0}
    led = {"strategies": {"H_MWU_CB融合": slot}}

    def _boom(*a, **k):
        raise AssertionError("fresh cache should skip engine")

    monkeypatch.setattr(mod, "_run_cmd", _boom)
    msgs = mod.mwu_rebalance(
        led, {}, dry=False, slot_name="H_MWU_CB融合",
        strategy="etf_mwu_cb",
        strategy_config="config/strategy/etf_mwu_cb_monthly.yaml",
        fills_ok=True)

    assert slot["holds"] and slot["holds"][0]["symbol"] == "512010.SH"
    assert "调仓→" in msgs[0]


def test_mwu_rebalance_ignores_cache_from_rebalance_day(tmp_path, monkeypatch):
    """调仓日 14:30 缓存只含盘中不完整 bar，必须回退收盘引擎。"""
    from types import SimpleNamespace

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "etf_mwu_cb_20261027.json").write_text(json.dumps({
        "cache_date": "2026-10-27",
        "as_of": "2026-10-27",
        "strategy_config": "config/strategy/etf_mwu_cb_monthly.yaml",
        "target": {"512010.SH": 0.9},
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "SIGNAL_CACHE_DIR", cache)
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 27))
    monkeypatch.setattr(mod, "_session_now", None, raising=False)
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 0.5, "pct": 0.0} for c in codes})
    calls = []
    monkeypatch.setattr(mod, "_run_cmd", lambda cmd, cwd, timeout: (
        calls.append(timeout) or SimpleNamespace(
            stdout=json.dumps({"as_of": "2026-10-27", "target": {"512010.SH": 0.9}}),
            stderr="", returncode=0)))
    slot = {"holds": [], "cash": 12411.0, "equity": 12411.0}
    led = {"strategies": {"H_MWU_CB融合": slot}}

    mod.mwu_rebalance(led, {}, dry=False, slot_name="H_MWU_CB融合",
                      strategy="etf_mwu_cb",
                      strategy_config="config/strategy/etf_mwu_cb_monthly.yaml",
                      fills_ok=True)

    assert calls == [1800]


def test_mwu_rebalance_ignores_previous_month_cache(tmp_path, monkeypatch):
    """月初缓存若仍只有上月 as_of，不能把旧月目标当新月目标。"""
    from types import SimpleNamespace

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "etf_mwu_cb_20261102.json").write_text(json.dumps({
        "cache_date": "2026-11-02",
        "as_of": "2026-10-08",
        "strategy_config": "config/strategy/etf_mwu_cb_monthly.yaml",
        "target": {"512010.SH": 0.9},
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "SIGNAL_CACHE_DIR", cache)
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 11, 2))
    monkeypatch.setattr(mod, "_session_now", None, raising=False)
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 0.5, "pct": 0.0} for c in codes})
    calls = []
    monkeypatch.setattr(mod, "_run_cmd", lambda cmd, cwd, timeout: (
        calls.append(timeout) or SimpleNamespace(
            stdout=json.dumps({"as_of": "2026-11-02", "target": {"512010.SH": 0.9}}),
            stderr="", returncode=0)))
    slot = {"holds": [], "cash": 12411.0, "equity": 12411.0}
    led = {"strategies": {"H_MWU_CB融合": slot}}

    mod.mwu_rebalance(led, {}, dry=False, slot_name="H_MWU_CB融合",
                      strategy="etf_mwu_cb",
                      strategy_config="config/strategy/etf_mwu_cb_monthly.yaml",
                      fills_ok=True)

    assert calls == [1800]


def test_benchmark_build_prefetches_quote_when_slot_empty(monkeypatch):
    slot = {"holds": [], "cash": 12411.0, "equity": 12411.0}
    led = {"strategies": {"C_沪深300持有": slot}}
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 4.4, "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))
    monkeypatch.setattr(mod, "_TRADING_OK", True)

    msgs = mod.benchmark_build_one(led, "C_沪深300持有", "510300.SH", {}, dry=False)
    assert slot["holds"] and slot["holds"][0]["symbol"] == "510300.SH"
    assert "建仓→" in msgs[0]


def test_cb_rebalance_exception_is_reported_as_abnormal(monkeypatch):
    """E 异常必须上报【异常】；返回 [] 会被注册表误登记为成功。"""
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "_run_cmd", _boom)
    msgs = mod.cb_rebalance(_fake_ledger(), {}, dry=False)
    assert msgs and msgs[0].startswith("【异常】")


def test_smallcap_rebalance_prefetches_quotes_for_empty_slot(monkeypatch):
    """F/F2 首建仓同样不能只依赖已持仓预热列表。"""
    slot = {"holds": [], "cash": 12411.0, "equity": 12411.0}
    led = {"strategies": {"F_小市值20": slot}}
    monkeypatch.setattr(mod, "smallcap_universe",
                        lambda *a, **k: [{"ts_code": "301578.SZ", "name": "辰奕智能",
                                          "circ_mv": 100, "close": 10.0}])
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 10.0, "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))
    monkeypatch.setattr(mod, "_TRADING_OK", True)

    msgs = mod.smallcap_rebalance(led, "F_小市值20", 8, 2500, True, {}, False)
    assert slot["holds"] and slot["holds"][0]["symbol"] == "SZ301578"
    assert slot["holds"][0]["qty"] == 100       # 槽内 12,411/8≈1,551，价 10 只够 1 手
    assert "月度再平衡" in msgs[0]


# ------------------------------------------------- run_intraday 扫描节流/失败重试
def _intraday_scan_env(tmp_path, monkeypatch, scan_msg=None):
    import json as _json
    from datetime import date as _date

    led = _fake_ledger()
    led["_control"]["event_scan_last"] = "20261020_PM"
    p = tmp_path / "intraday_scan.json"
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", p)
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 21))
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod, "slot_prices", lambda led: {})
    monkeypatch.setattr(mod, "risk_round", lambda led, px, dry: [])
    monkeypatch.setattr(mod, "notify", lambda *a, **k: False)
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 21, 10, 0))
    monkeypatch.setattr(mod, "_session_now", datetime(2026, 10, 21, 10, 0), raising=False)
    calls = {"event": 0, "watch": 0}

    def _event(led, dry):
        calls["event"] += 1
        return [scan_msg] if scan_msg else []

    def _watch(led, dry):
        calls["watch"] += 1
        return []

    monkeypatch.setattr(mod, "event_round", _event)
    monkeypatch.setattr(mod, "watchlist_round", _watch)
    return p, calls


def test_run_intraday_retries_scan_after_abnormal_scan(tmp_path, monkeypatch):
    """扫描返回【异常】时不得写 event_scan_last；下一轮必须重新扫描。"""
    import json as _json

    msg = "【异常】事件扫描数据源失败/不完整(2/2), 待下轮重试"
    p, calls = _intraday_scan_env(tmp_path, monkeypatch, scan_msg=msg)
    mod.run_intraday(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["event_scan_last"] == "20261020_PM"
    assert calls == {"event": 1, "watch": 1}

    mod.run_intraday(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["event_scan_last"] == "20261020_PM"
    assert calls == {"event": 2, "watch": 2}


def test_run_intraday_sets_scan_key_on_success(tmp_path, monkeypatch):
    import json as _json

    p, calls = _intraday_scan_env(tmp_path, monkeypatch)
    mod.run_intraday(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["event_scan_last"] == "20261021_AM"
    assert calls == {"event": 1, "watch": 1}

    mod.run_intraday(False)   # 同半天不再扫描
    assert calls == {"event": 1, "watch": 1}


def test_run_intraday_halfday_uses_exchange_time(tmp_path, monkeypatch):
    """节流键按交易所时间（_session_now）而非墙钟；13:00 应写 PM。"""
    import json as _json

    p, _ = _intraday_scan_env(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_session_now", datetime(2026, 10, 21, 13, 0), raising=False)
    mod.run_intraday(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["event_scan_last"] == "20261021_PM"


def test_event_round_reports_source_failure(monkeypatch):
    class FakeAk:
        @staticmethod
        def stock_notice_report(**kwargs):
            raise RuntimeError("dns")

    monkeypatch.setitem(sys.modules, "akshare", FakeAk)
    led = _fake_ledger()
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))
    msgs = mod.event_round(led, False)
    assert any(m.startswith("【异常】") for m in msgs)


def test_watchlist_round_reports_item_failure(monkeypatch):
    led = _fake_ledger()
    led["strategies"]["X_事件套利"]["watchlist"] = [{"code": "600000", "name": "测试"}]
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))

    def _boom(code):
        raise RuntimeError("dns")
    monkeypatch.setattr(mod, "fetch_recent_announcements", _boom)
    msgs = mod.watchlist_round(led, False)
    assert any(m.startswith("【异常】") for m in msgs)
    assert "last_checked" not in led["strategies"]["X_事件套利"]["watchlist"][0]


# ------------------------------------------------- 首建仓 P0: 捕获撮合闸/marks/预算/快照末端
def test_close_uses_captured_session_when_exchange_fails(tmp_path, monkeypatch):
    """15:00 启动、慢行情后 exchange_now 失败，周/月仍必须按启动会话成交。"""
    import json as _json

    p, calls = _weekly_env(tmp_path, monkeypatch, datetime(2026, 10, 19, 22, 5))
    monkeypatch.setattr(mod, "_session_now", datetime(2026, 10, 19, 14, 40), raising=False)
    monkeypatch.setattr(mod, "exchange_now", lambda: None)
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["week_rebalance_done"]["2026-W43"] == "all"
    assert calls == {"momentum": 1, "rotation": 1, "mwu": 1}


def test_close_marks_reflect_post_trade_equity(tmp_path, monkeypatch):
    """marks 必须在注册表/成交之后计算，首日即反映费用与持仓市值。"""
    import json as _json

    p = _boot_env(tmp_path, monkeypatch, datetime(2026, 10, 20, 10, 0))
    mod.run_close(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    c = saved["strategies"]["C_沪深300持有"]
    mark_eq = saved["marks"][-1]["equity"]["C_沪深300持有"]
    assert mark_eq < 12411.0
    assert abs(mark_eq - (c["cash"] + c["holds"][0]["qty"] * 4.4)) < 1.0
    assert saved["marks"][-1]["date"] == "2026-10-20"


def test_smallcap_rebalance_uses_per_stock_budget(monkeypatch):
    """F 8 只: 12,411/8≈1,551，价 1 时应买 1,500 股，而不是固定 100 股。"""
    slot = {"holds": [], "cash": 12411.0, "equity": 12411.0}
    led = {"strategies": {"F_小市值20": slot}}
    recs = [{"ts_code": f"00{i:04d}.SZ", "name": f"S{i}", "circ_mv": i, "close": 1.0}
            for i in range(1, 9)]
    monkeypatch.setattr(mod, "smallcap_universe", lambda *a, **k: recs)
    monkeypatch.setattr(mod, "realtime_quotes",
                        lambda codes: {c: {"price": 1.0, "pct": 0.0} for c in codes})
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))
    monkeypatch.setattr(mod, "_TRADING_OK", True)

    msgs = mod.smallcap_rebalance(led, "F_小市值20", 8, 2500, True, {}, False)
    assert len(slot["holds"]) == 8
    assert all(h["qty"] == 1500 for h in slot["holds"])
    assert "月度再平衡" in msgs[0]


def test_maybe_refresh_snapshot_rebuilds_on_stale_data(tmp_path, monkeypatch):
    """文件 mtime 很新但数据末端早于上一交易日时，必须重建快照。"""
    import pandas as pd

    root = tmp_path / "repo"
    daily = root / "daily"
    out = root / "quant_research" / "output"
    out.mkdir(parents=True)
    daily.mkdir()
    snap = out / "feeds_snapshot_v5.pkl"
    pd.to_pickle({"510300.SH": pd.DataFrame(
        [{"date": pd.Timestamp("2026-10-16"), "close": 1.0}])}, snap)
    monkeypatch.setattr(mod, "PROJECT_DIR", daily)
    monkeypatch.setattr(mod, "EX_DATE", date(2026, 10, 21))
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: d.weekday() < 5)
    calls = []

    def _fake(cmd, cwd, timeout):
        calls.append(cmd)
        return None

    monkeypatch.setattr(mod, "_run_cmd", _fake)
    msg = mod.maybe_refresh_snapshot(False)
    assert calls and "快照已刷新" in msg
    assert "2026-10-16" in msg
    assert "--source" in calls[0] and "akshare" in calls[0]


# ------------------------------------------------- 10-22 缺价事故回归
def test_revalue_uses_cost_when_quote_missing():
    """缺价不得把持仓排除出净值（否则 C/D 会被记成只剩现金）。"""
    slot = {"cash": 100.0, "holds": [{"symbol": "510300.SH", "qty": 10, "price": 5.0}]}
    eq = mod.revalue(slot, {})
    assert eq == 150.0
    assert slot["valuation_status"] == {"fully_valued": False,
                                        "unvalued": {"510300.SH": "missing"}}


def test_close_defers_when_core_benchmark_unquoted(tmp_path, monkeypatch):
    """核心 C/D 缺价时整轮不写 marks/注册表；恢复后由后续轮补写。"""
    import json as _json
    from datetime import date as _date

    led = {"strategies": {
        "C_沪深300持有": {"cash": 100.0, "equity": 500.0,
                          "holds": [{"symbol": "510300.SH", "qty": 100, "price": 4.0}]},
        "D_红利低波持有": {"cash": 100.0, "equity": 500.0,
                          "holds": [{"symbol": "512890.SH", "qty": 100, "price": 1.0}]},
    }, "marks": [], "_control": {}}
    p = tmp_path / "ledger.json"
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", p)
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 22))
    monkeypatch.setattr(mod, "_session_now", datetime(2026, 10, 22, 15, 0), raising=False)
    monkeypatch.setattr(mod, "_TRADING_OK", True)
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod, "slot_prices", lambda led: {})
    monkeypatch.setattr(mod, "ensure_prices", lambda px, syms: px)

    out = mod.run_close(False)
    assert out.startswith("【异常】收盘缺价")
    assert "510300.SH" in out and "512890.SH" in out
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["marks"] == []
    assert saved["_control"] == {}


def test_buywatch_reports_rejected_fill(monkeypatch):
    """K 的 apply_orders 被拒时必须报【顺延】，不得声称"建仓→"。"""
    from datetime import date as _date

    slot = {"holds": [], "cash": 12411.0, "equity": 12411.0}
    led = {"strategies": {"K_项目选股": slot}}
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 22))
    monkeypatch.setattr(mod, "_TRADING_OK", False)
    monkeypatch.setattr(mod, "buywatch_universe",
                        lambda *a, **k: [{"symbol": "600000", "name": "测试", "close": 10.0, "score": 1}])

    def _ensure(px, syms):
        px.update({s: {"price": 10.0} for s in syms})
        return px

    monkeypatch.setattr(mod, "ensure_prices", _ensure)

    msgs = mod.buywatch_rebalance(led, {}, False)
    assert msgs and msgs[0].startswith("【顺延】K_项目选股")
    assert slot["holds"] == []


def test_pead_universe_raises_when_all_source_calls_fail(monkeypatch):
    from datetime import date as _date
    from types import SimpleNamespace

    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 22))
    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=lambda *a, **k: None))
    with pytest.raises(RuntimeError, match="forecast"):
        mod.pead_universe()


def test_smallcap_universe_raises_on_missing_source(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=lambda *a, **k: None))
    with pytest.raises(RuntimeError, match="daily_basic"):
        mod.smallcap_universe("20260930", True, 8)


def test_ok_symbol_excludes_delisting_and_st_names():
    assert not mod._ok_symbol("元道退", "301139.SZ")
    assert not mod._ok_symbol("*ST样本", "000001.SZ")
    assert not mod._ok_symbol("正常股", "688001.SH")
    assert mod._ok_symbol("天元智能", "603273.SH")


def test_smallcap_universe_excludes_delisting_names(monkeypatch):
    """301139 元道退仍在 list_status=L，但退市整理股不得进入小市值候选。"""
    import pandas as pd
    from types import SimpleNamespace

    db = pd.DataFrame({
        "ts_code": ["301139.SZ", "603273.SH"],
        "circ_mv": [100.0, 200.0],
        "close": [0.73, 15.0],
    })
    sb = pd.DataFrame({
        "ts_code": ["301139.SZ", "603273.SH"],
        "name": ["元道退", "天元智能"],
        "list_date": ["20200101", "20200101"],
    })

    def _call(api, **kwargs):
        return db if api == "daily_basic" else sb

    monkeypatch.setattr(mod, "ts_client", lambda: SimpleNamespace(call=_call))
    recs = mod.smallcap_universe("20260930", False, 8)
    assert [r["ts_code"] for r in recs] == ["603273.SH"]


def test_intraday_does_not_mark_close_done_on_close_failure(tmp_path, monkeypatch):
    """收盘返回【异常】(如核心缺价) 时不得写 close_done，后续轮还要补写。"""
    import json as _json
    from datetime import date as _date

    led = _fake_ledger()
    led["_control"] = {}
    p = tmp_path / "ledger.json"
    p.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", p)
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 22))
    monkeypatch.setattr(mod, "exchange_now", lambda: datetime(2026, 10, 22, 15, 0))
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod, "slot_prices", lambda led: {})
    monkeypatch.setattr(mod, "event_round", lambda led, dry: [])
    monkeypatch.setattr(mod, "watchlist_round", lambda led, dry: [])
    monkeypatch.setattr(mod, "risk_round", lambda led, px, dry: [])
    notified = []
    monkeypatch.setattr(mod, "notify", lambda msg, **k: notified.append(msg) or False)
    monkeypatch.setattr(mod, "_TRADING_OK", True)
    monkeypatch.setattr(mod, "run_close", lambda dry: "【异常】收盘缺价 ['510300.SH']")

    mod.run_intraday(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"].get("close_done") != "2026-10-22"
    assert any(m.startswith("【异常】收盘缺价") for m in notified)

    monkeypatch.setattr(mod, "run_close", lambda dry: "收盘完成")
    mod.run_intraday(False)
    saved = _json.loads(p.read_text(encoding="utf-8"))
    assert saved["_control"]["close_done"] == "2026-10-22"

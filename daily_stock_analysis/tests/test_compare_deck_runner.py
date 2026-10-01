# -*- coding: utf-8 -*-
"""compare_deck_runner 核心交易原语单测（离线，无网络）。"""

import importlib.util
import sys
from pathlib import Path

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
    slot = _slot(cash=100.0)
    mod.apply_orders(
        slot,
        sells=[{"symbol": "512890.SH", "qty": 100, "price": 50.0}],   # 回款 5000-5
        buys=[{"symbol": "510300.SH", "qty": 100, "price": 4.416}],   # 需 446.6
        px={},
    )
    assert len(slot["holds"]) == 1
    assert slot["holds"][0]["symbol"] == "510300.SH"
    total = slot["cash"] + slot["holds"][0]["notional"]
    assert total <= 12411.0 + 0.01  # 现金+持仓+费用 ≤ 本金


def test_apply_orders_buy_skipped_when_cash_insufficient():
    slot = _slot(cash=100.0, holds=[])
    # 卖 118 元回款 113, 买 446.6 买不起 → 买入跳过, 持仓空
    mod.apply_orders(
        slot,
        sells=[{"symbol": "512890.SH", "qty": 100, "price": 1.18}],
        buys=[{"symbol": "510300.SH", "qty": 100, "price": 4.416}],
        px={},
    )
    assert slot["holds"] == []


def test_apply_orders_buy_skipped_when_cash_insufficient():
    slot = _slot(cash=50.0)
    mod.apply_orders(slot, sells=[], buys=[{"symbol": "510300.SH", "qty": 100, "price": 4.416}], px={})
    assert slot["holds"] == []
    assert slot["cash"] == 50.0  # 未动


def test_apply_orders_replaces_position():
    slot = _slot(holds=[{"symbol": "A", "qty": 1, "price": 1, "notional": 1}])
    mod.apply_orders(slot, sells=[{"symbol": "A", "qty": 1, "price": 1.0}], buys=[], px={})
    assert slot["holds"] == []


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
    return {"strategies": {
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
        "J_短期反转": {"holds": [], "cash": 12411.0, "equity": 12411.0},
        "C_沪深300持有": {"holds": [{"symbol": "510300.SH", "name": "沪深300ETF", "qty": 2800, "price": 4.416}], "cash": 41.2, "equity": 12406.0},
        "D_红利低波持有": {"holds": [{"symbol": "512890.SH", "name": "红利低波ETF", "qty": 10500, "price": 1.18}], "cash": 16.0, "equity": 12406.0},
    }}


def test_build_close_summary_contains_actions_positions_risk():
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


def test_build_close_summary_flat_when_no_actions():
    out = mod.build_close_summary(_fake_ledger(), {})
    assert "▎今日操作\n· 无调仓" in out


# ---------------------------------------------------------------- mwu_target_orders
def test_mwu_target_orders_lot_sizes_and_diff():
    # 防御腿 511010 10张/手, 其余 100股
    target = {"511010.SH": 0.66, "512480.SH": 0.34}
    prices = {"511010.SH": 140.0, "512480.SH": 1.0}
    sells, buys = mod.mwu_target_orders(target, {}, prices)
    # 0.66*12411=8191 / 1400 → 5手=50张; 0.34*12411=4220 / 100 → 4200股
    assert {"symbol": "511010.SH", "qty": 50, "price": 140.0} in buys
    assert {"symbol": "512480.SH", "qty": 4200, "price": 1.0} in buys
    assert sells == []


def test_mwu_target_orders_partial_trim_and_exit():
    # 部分减仓 + 目标剔除全卖
    target = {"511010.SH": 0.66}
    prices = {"511010.SH": 140.0, "512480.SH": 1.0}
    sells, buys = mod.mwu_target_orders(target, {"511010.SH": 80, "512480.SH": 1000}, prices)
    assert {"symbol": "511010.SH", "qty": 30, "price": 140.0} in sells   # 80→50
    assert {"symbol": "512480.SH", "qty": 1000, "price": 1.0} in sells   # 剔除
    assert buys == []


def test_mwu_target_orders_skips_missing_price():
    sells, buys = mod.mwu_target_orders({"512480.SH": 1.0}, {}, {})
    assert sells == [] and buys == []


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
    mod.apply_orders(slot, sells=[{"symbol": "512890.SH", "qty": 100, "price": 1.196}], buys=[], px={})
    assert len(slot["holds"]) == 1
    assert slot["holds"][0]["qty"] == 5100            # 扣量保留, 不清空
    assert slot["holds"][0]["price"] == 1.18          # 成本价不变
    assert abs(slot["cash"] - (100 * 1.196 - 5.0)) < 0.01


def test_apply_orders_full_sell_removes():
    slot = _slot(cash=0.0, holds=[{"symbol": "A", "qty": 100, "price": 2.0, "notional": 200.0}])
    mod.apply_orders(slot, sells=[{"symbol": "A", "qty": 100, "price": 2.0}], buys=[], px={})
    assert slot["holds"] == []
    assert abs(slot["cash"] - (200.0 - 5.0)) < 0.01


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
    """固化 10-08 预演: mock 交易所时间/行情/引擎子进程, 验证月度调仓全链不崩。"""
    import json as _json
    import shutil as _shutil
    from datetime import date as _date, datetime as _dt

    # 台账副本到 tmp（不碰真台账）
    real = mod.LEDGER
    led = _json.loads(real.read_text(encoding="utf-8"))
    for k in ("rebalance_done", "close_done", "erp_last_check"):
        led.setdefault("_control", {}).pop(k, None)
    test_led = tmp_path / "ledger.json"
    test_led.write_text(_json.dumps(led, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "LEDGER", test_led)

    # mock 时间与交易日历
    monkeypatch.setattr(mod, "EX_DATE", _date(2026, 10, 8))
    monkeypatch.setattr(mod, "exchange_now", lambda: _dt(2026, 10, 8, 15, 5))
    monkeypatch.setattr(mod, "is_exchange_trading_day", lambda d: True)
    monkeypatch.setattr(mod, "_is_month_last_trading_day", lambda: False)
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
    # ERP mock(单测已覆盖 erp_decision)
    monkeypatch.setattr(mod, "erp_gate", lambda led, px, dry: [])
    monkeypatch.setattr(mod, "pead_universe", lambda *a, **k: [])
    monkeypatch.setattr(mod, "buywatch_universe", lambda *a, **k: [])
    monkeypatch.setattr(mod, "reversal_universe", lambda *a, **k: [])
    monkeypatch.setattr(mod, "_tushare_pro", lambda: None)
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
    # 引擎被调用 ≥1 次(A 动量 + G mwu)
    assert calls["n"] >= 1
    # Y 槽面值模型不被污染
    assert new_led["strategies"]["Y_确定性收益"]["equity"] >= 12000
    assert "月度再平衡" in out or "无调仓" in out


# ---------------------------------------------------------------- e2_gate_decision
def test_e2_gate_hysteresis():
    assert mod.e2_gate_decision(130.05, "normal") == "enter_defensive"   # 今日实测
    assert mod.e2_gate_decision(125.0, "normal") == "hold"               # 中性区
    assert mod.e2_gate_decision(125.0, "defensive") == "hold"            # 未恢复不退出
    assert mod.e2_gate_decision(117.9, "defensive") == "exit_defensive"
    assert mod.e2_gate_decision(0, "normal") == "no_data"

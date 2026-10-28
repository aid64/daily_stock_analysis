# -*- coding: utf-8 -*-
"""纸面执行核心回归测试。

每条测试对应 .scratch/dsa-deployment-review-20261007/contract-probe-results.json
中的一条已确认反例（或需保留的已验证正确行为）。纯函数、无网络、无台账、无 .env。
"""

from datetime import date, datetime, timedelta

import pytest

from src.core.paper_execution import (
    AssetClass,
    AssetPolicy,
    ExecutionContext,
    Intent,
    Lot,
    Quote,
    RejectReason,
    execute_intents,
    merge_lots_view,
    order_key,
    price_limit_ratio,
    price_limits,
    trade_fee,
    value_positions,
)

DAY = date(2026, 10, 8)
PREV = date(2026, 10, 7)


def _ctx(**kw) -> ExecutionContext:
    defaults = dict(slot="T", trading_day=DAY, now=datetime(2026, 10, 8, 14, 0))
    defaults.update(kw)
    return ExecutionContext(**defaults)


def _q(symbol, price, **kw) -> Quote:
    return Quote(symbol=symbol, price=price, **kw)


def _quote_map(*quotes) -> dict:
    return {q.symbol: q for q in quotes}


def _buy(symbol, qty, cls=AssetClass.ETF, **kw) -> Intent:
    return Intent(symbol=symbol, side="BUY", qty=qty, asset_class=cls, **kw)


def _sell(symbol, qty, cls=AssetClass.ETF, **kw) -> Intent:
    return Intent(symbol=symbol, side="SELL", qty=qty, asset_class=cls, **kw)


# --------------------------------------------------------------- 卖出合法性
def test_sell_without_position_rejected_no_phantom_cash():
    """反例: 未持有证券凭空卖出使 cash 12411→13405.5。"""
    r = execute_intents(12411.0, [], [_sell("600000.SH", 100, AssetClass.STOCK)],
                        _quote_map(_q("600000.SH", 10.0)), _ctx())
    assert r.fills == () and r.cash == 12411.0
    assert r.rejections[0].reason is RejectReason.NO_POSITION


def test_oversell_rejected_position_untouched():
    """反例: 持有100卖出200被全额记入回款并清仓。"""
    lots = (Lot("510300.SH", 100, 4.4, PREV),)
    r = execute_intents(12411.0, lots, [_sell("510300.SH", 200)],
                        _quote_map(_q("510300.SH", 4.5)), _ctx())
    assert r.fills == () and r.cash == 12411.0
    assert r.rejections[0].reason is RejectReason.OVERSELL
    assert sum(l.qty for l in r.lots) == 100  # 持仓未被动


def test_partial_sell_retains_remaining_lots():
    """保留已验证行为: 5200 卖 100 后剩 5100，成本不变。"""
    lots = (Lot("512890.SH", 5200, 1.18, PREV),)
    r = execute_intents(100.0, lots, [_sell("512890.SH", 100)],
                        _quote_map(_q("512890.SH", 1.2)), _ctx())
    assert len(r.fills) == 1
    assert [(l.qty, l.price) for l in r.lots] == [(5100, 1.18)]


def test_stock_same_day_buy_cannot_sell_but_next_day_can():
    """反例: 股票当日买卖成功。T+1 下当日买入不可卖；老批次可卖；次日余仓可卖。"""
    q = _quote_map(_q("600000.SH", 10.0))
    # 批次1: 昨日老仓可卖
    r1 = execute_intents(5000.0, (Lot("600000.SH", 100, 10.0, PREV),),
                         [_sell("600000.SH", 100, AssetClass.STOCK)], q, _ctx())
    assert len(r1.fills) == 1
    # 批次2（同日）: 当日新买成交
    r2 = execute_intents(r1.cash, (), [_buy("600000.SH", 100, AssetClass.STOCK)], q, _ctx())
    assert len(r2.fills) == 1
    assert [(l.qty, l.buy_day) for l in r2.lots] == [(100, DAY)]
    # 批次3（同日）: 卖当日新买 → T_PLUS_1 拒绝
    r3 = execute_intents(r2.cash, r2.lots, [_sell("600000.SH", 100, AssetClass.STOCK)], q, _ctx())
    assert r3.fills == ()
    assert r3.rejections[0].reason is RejectReason.T_PLUS_1
    # 批次4（次日）: 同一持仓可卖
    r4 = execute_intents(r3.cash, r3.lots, [_sell("600000.SH", 100, AssetClass.STOCK)], q,
                         _ctx(trading_day=date(2026, 10, 9)))
    assert len(r4.fills) == 1


def test_t_plus_1_applies_to_etf_not_cb():
    """ETF 默认 T+1；转债 T+0 当日买卖放行。"""
    etf = (Lot("510300.SH", 100, 4.4, DAY),)
    r = execute_intents(0.0, etf, [_sell("510300.SH", 100)],
                        _quote_map(_q("510300.SH", 4.5)), _ctx())
    assert r.rejections[0].reason is RejectReason.T_PLUS_1
    cb = (Lot("113062.SH", 10, 126.0, DAY),)
    r2 = execute_intents(0.0, cb, [_sell("113062.SH", 10, AssetClass.CB)],
                         _quote_map(_q("113062.SH", 127.0)), _ctx())
    assert len(r2.fills) == 1


# --------------------------------------------------------------- 报价闸
def test_zero_price_rejected():
    """反例: 价格0也能买入（只扣5元佣金）。"""
    r = execute_intents(12411.0, [], [_buy("600000.SH", 100, AssetClass.STOCK)],
                        _quote_map(_q("600000.SH", 0.0)), _ctx())
    assert r.fills == () and r.cash == 12411.0
    assert r.rejections[0].reason is RejectReason.PRICE_INVALID


def test_missing_quote_rejected_not_skipped():
    r = execute_intents(12411.0, [], [_buy("600000.SH", 100, AssetClass.STOCK)], {}, _ctx())
    assert r.rejections[0].reason is RejectReason.NO_QUOTE
    assert r.cash == 12411.0


def test_stale_quote_rejected_by_flag_and_by_age():
    flag = execute_intents(12411.0, [], [_buy("510300.SH", 100)],
                           _quote_map(_q("510300.SH", 4.5, is_stale=True)), _ctx())
    assert flag.rejections[0].reason is RejectReason.STALE_QUOTE
    old = _q("510300.SH", 4.5, timestamp=datetime(2026, 9, 30, 10, 0))
    aged = execute_intents(12411.0, [], [_buy("510300.SH", 100)], _quote_map(old),
                           _ctx(quote_max_age=timedelta(minutes=15)))
    assert aged.rejections[0].reason is RejectReason.STALE_QUOTE


def test_limit_up_down_rejected():
    buy_q = _q("510300.SH", 4.84, limit_up=4.84, limit_down=4.36)
    buy = execute_intents(12411.0, [], [_buy("510300.SH", 100)], _quote_map(buy_q), _ctx())
    assert buy.rejections[0].reason is RejectReason.LIMIT_UP_DOWN
    lots = (Lot("510300.SH", 100, 4.4, PREV),)
    sell_q = _q("510300.SH", 4.35, limit_up=4.84, limit_down=4.36)
    sell = execute_intents(0.0, lots, [_sell("510300.SH", 100)], _quote_map(sell_q), _ctx())
    assert sell.rejections[0].reason is RejectReason.LIMIT_UP_DOWN


def test_price_limit_ratio_board_rules():
    assert price_limit_ratio("600000.SH", "浦发银行") == 0.10
    assert price_limit_ratio("300750.SZ", "宁德时代") == 0.20
    assert price_limit_ratio("688981.SH", "中芯国际") == 0.20
    assert price_limit_ratio("830799.BJ", "艾融软件") == 0.30
    assert price_limit_ratio("600000.SH", "ST浦发") == 0.05
    assert price_limit_ratio("300750.SZ", "ST宁德") == 0.20      # 创业板 ST 仍 20%
    assert price_limit_ratio("830799.BJ", "ST艾融") == 0.30      # 北交所 ST 仍 30%
    assert price_limit_ratio("600000.SH", "退市浦发") == 0.10    # 退市整理期不是 ST 5%
    assert price_limit_ratio("510300.SH", "", AssetClass.ETF) == 0.10
    assert price_limit_ratio("113524.SH", "", AssetClass.CB) == 0.20
    assert price_limit_ratio("204001.SH", "", AssetClass.REPO) == 0.0


def test_price_limits_rounding_and_invalid_pre_close():
    assert price_limits("600000.SH", 10.0) == (11.0, 9.0)
    assert price_limits("600000.SH", 10.05) == (11.06, 9.05)    # 四舍五入到分
    assert price_limits("300750.SZ", 10.0) == (12.0, 8.0)
    assert price_limits("830799.BJ", 10.0) == (13.0, 7.0)
    assert price_limits("600000.SH", 10.0, "ST某某") == (10.5, 9.5)
    assert price_limits("510300.SH", 4.0, "", AssetClass.ETF) == (4.4, 3.6)
    assert price_limits("113524.SH", 100.0, "", AssetClass.CB) == (120.0, 80.0)
    assert price_limits("600000.SH", 0.0) == (None, None)
    assert price_limits("600000.SH", float("nan")) == (None, None)
    assert price_limits("600000.SH", None) == (None, None)


# --------------------------------------------------------------- 全局闸与幂等
def test_trading_disabled_blocks_all_fills():
    """反例: _TRADING_OK=False 仍买入100。"""
    lots0 = ()
    for kw in (dict(trading_ok=False), dict(session_open=False)):
        r = execute_intents(12411.0, lots0, [_buy("510300.SH", 100)],
                            _quote_map(_q("510300.SH", 4.5)), _ctx(**kw))
        assert r.fills == () and r.cash == 12411.0
        assert r.rejections[0].reason is RejectReason.SESSION_CLOSED


def test_duplicate_intent_rejected_idempotent():
    """反例: 相同输入重试 100→200→300。同批重复与跨批重放都只成交一次。"""
    intents = [_buy("510300.SH", 100), _buy("510300.SH", 100)]
    r = execute_intents(12411.0, (), intents, _quote_map(_q("510300.SH", 4.5)), _ctx())
    assert len(r.fills) == 1
    assert r.rejections[0].reason is RejectReason.DUPLICATE_ORDER
    assert sum(l.qty for l in r.lots) == 100
    # 跨批重放：同一键再次提交被拒
    r2 = execute_intents(12411.0, (), [_buy("510300.SH", 100)],
                         _quote_map(_q("510300.SH", 4.5)),
                         _ctx(executed_order_keys=frozenset(r.order_keys)))
    assert r2.fills == () and r2.rejections[0].reason is RejectReason.DUPLICATE_ORDER


def test_order_key_deterministic_and_distinct():
    ctx = _ctx()
    a = order_key(ctx, Intent("510300.SH", "BUY", 100, AssetClass.ETF))
    b = order_key(ctx, Intent("510300.SH", "BUY", 100, AssetClass.ETF))
    c = order_key(ctx, Intent("510300.SH", "SELL", 100, AssetClass.ETF))
    assert a == b and a != c


# --------------------------------------------------------------- 现金与费用
def test_insufficient_cash_buy_rejected_cash_untouched():
    r = execute_intents(50.0, (), [_buy("510300.SH", 100)],
                        _quote_map(_q("510300.SH", 4.416)), _ctx())
    assert r.fills == () and r.cash == 50.0
    assert r.rejections[0].reason is RejectReason.INSUFFICIENT_CASH


def test_cash_conservation_no_negative_cash():
    """先卖后买：现金=初始+卖回款-买支出-费用；全程非负。"""
    cash0 = 12411.0
    r = execute_intents(
        cash0, (),
        [_buy("600000.SH", 100, AssetClass.STOCK), _buy("510300.SH", 100)],
        _quote_map(_q("600000.SH", 10.0), _q("510300.SH", 4.416)), _ctx())
    bought_cost = sum(f.notional + f.fee for f in r.fills)
    assert r.cash == pytest.approx(cash0 - bought_cost, abs=0.01)
    assert r.cash >= 0
    assert r.fees_total == pytest.approx(sum(f.fee for f in r.fills), abs=0.01)


def test_fees_commission_floor_and_stock_sell_stamp():
    assert trade_fee(AssetClass.ETF, "BUY", 350.0) == 5.0           # 最低佣金
    assert trade_fee(AssetClass.ETF, "BUY", 100000.0) == 8.45       # 万0.845
    assert trade_fee(AssetClass.STOCK, "SELL", 100000.0) == 8.45 + 50.0  # +印花0.05%
    assert trade_fee(AssetClass.ETF, "SELL", 100000.0) == 8.45      # ETF 免印花
    assert trade_fee(AssetClass.REPO, "SELL", 12000.0) == 0.0       # 逆回购零费假设


def test_lot_size_cb_rejected():
    r = execute_intents(12411.0, (), [_buy("113062.SH", 15, AssetClass.CB)],
                        _quote_map(_q("113062.SH", 126.0)), _ctx())
    assert r.rejections[0].reason is RejectReason.LOT_SIZE
    assert r.cash == 12411.0


def test_same_symbol_buys_merge_weighted_cost():
    """反例: 同证券加仓产生两行持仓、上游字典丢数量。核心用批次，投影合并加权成本。

    两笔是不同订单（order_id 区分；相同 symbol/side/qty/day 的无 id 意图
    会被幂等键判重——见 test_duplicate_intent_rejected_idempotent）。"""
    r = execute_intents(
        12411.0, (),
        [_buy("600000.SH", 100, AssetClass.STOCK, order_id="a"),
         _buy("600000.SH", 100, AssetClass.STOCK, order_id="b")],
        _quote_map(_q("600000.SH", 10.0)), _ctx())
    assert len(r.fills) == 2
    view = merge_lots_view(r.lots)
    assert view["600000.SH"]["qty"] == 200
    assert view["600000.SH"]["avg_cost"] == pytest.approx(10.0)


# --------------------------------------------------------------- 估值
def test_valuation_missing_quote_marked_not_cost_disguised():
    """反例: 无行情按成本价估值且无 stale 标记。缺价持仓不进 equity。"""
    lots = (Lot("510300.SH", 100, 4.4, PREV),)
    v = value_positions(5000.0, lots, {}, symbol_asset_class={"510300.SH": AssetClass.ETF})
    assert v.unvalued == {"510300.SH": "missing"}
    assert v.equity == 5000.0 and not v.fully_valued


def test_repo_valued_at_principal():
    """保留已验证行为: 逆回购按本金估值，外部怪报价不影响。"""
    lots = (Lot("204001.SH", 120, 100.0, PREV),)   # 本金 12000
    v = value_positions(411.0, lots, _quote_map(_q("204001.SH", 0.016)),
                        symbol_asset_class={"204001.SH": AssetClass.REPO})
    assert v.market_value == pytest.approx(12000.0)
    assert v.equity == pytest.approx(12411.0)
    assert v.fully_valued


def test_valuation_unknown_asset_class_flagged():
    lots = (Lot("MYSTERY.SH", 100, 9.9, PREV),)
    v = value_positions(0.0, lots, _quote_map(_q("MYSTERY.SH", 9.9)), symbol_asset_class={})
    assert v.unvalued == {"MYSTERY.SH": "no_asset_class"}
    assert v.equity == 0.0 and not v.fully_valued


# --------------------------------------------------------------- 政策覆盖
def test_custom_asset_policy_override():
    """债券/货币/跨境 ETF 的 T+0 品种可由注册表按标的覆盖默认 T+1。"""
    money = AssetPolicy(AssetClass.ETF, lot_size=100, t_plus_1=False)
    r = execute_intents(0.0, (Lot("511880.SH", 100, 1.0, DAY),),
                        [_sell("511880.SH", 100)], _quote_map(_q("511880.SH", 1.01)), _ctx(),
                        asset_policies={AssetClass.ETF: money})
    assert len(r.fills) == 1

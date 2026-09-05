"""执行模块测试：PaperBroker / 离散优化 sizer / Executor

全部使用合成数据与模拟账户，不依赖网络与实盘 DLL。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from quant_research.trade import (
    AbstractBroker,
    BrokerError,
    Order,
    PaperBroker,
    Side,
    integer_lot_allocate,
    Target,
)
from quant_research.trade.executor import Executor, ExecutorConfig, MarketClock
from quant_research.backtest.mwu import build_experts


# ── PaperBroker ───────────────────────────────


class TestPaperBroker:
    def test_buy_sell_roundtrip(self):
        b = PaperBroker(initial_cash=10000, min_commission=5)
        b.set_quote("AAA.SH", 10.0)
        b.send_order(Order("AAA.SH", Side.BUY, 100, 10.0))
        assert b.get_account().positions[0].quantity == 100
        assert b.get_account().cash < 10000  # 扣费
        # 卖出
        b.set_quote("AAA.SH", 11.0)
        b.send_order(Order("AAA.SH", Side.SELL, 100, 11.0))
        acc = b.get_account()
        assert len(acc.positions) == 0
        # 盈利扣除手续费后应回本略多
        assert acc.cash > 10000

    def test_insufficient_cash_rejected(self):
        b = PaperBroker(initial_cash=1000, min_commission=5)
        b.set_quote("AAA.SH", 100.0)
        with pytest.raises(BrokerError, match="资金不足"):
            b.send_order(Order("AAA.SH", Side.BUY, 100, 100.0))  # 10000 > 1000

    def test_insufficient_sellable_rejected(self):
        b = PaperBroker(initial_cash=10000)
        b.set_quote("AAA.SH", 10.0)
        b.send_order(Order("AAA.SH", Side.BUY, 100, 10.0))
        with pytest.raises(BrokerError, match="可卖数量不足"):
            b.send_order(Order("AAA.SH", Side.SELL, 200, 10.0))

    def test_quote_missing_raises(self):
        b = PaperBroker()
        with pytest.raises(BrokerError, match="无 .* 的行情"):
            b.get_quote("NOPE.SH")


# ── 离散整数手优化 ────────────────────────────


class TestIntegerLotAllocate:
    def test_equal_weights_allocate_lots(self):
        cash = 10_000.0
        targets = [
            Target("A.SH", 0.4, 2.0),    # 一手 200 元
            Target("B.SH", 0.4, 3.0),    # 一手 300 元
            Target("C.SH", 0.2, 1.0),    # 一手 100 元
        ]
        res = integer_lot_allocate(targets, cash, lot_buffer=1.0)
        # 总分配 ≤ 现金
        assert sum(res.target_value.values()) <= cash
        # 权重高者市值占比高
        assert res.target_value["A.SH"] >= res.target_value["C.SH"]
        # 权重为零者不入池
        assert all(qty >= 0 for qty in res.target_quantity.values())

    def test_lot_size_respected(self):
        """债券 ETF（lot=10）按 10 张一手分配"""
        cash = 1000.0
        targets = [Target("BOND.SH", 1.0, 100.0, lot_size=10)]
        res = integer_lot_allocate(targets, cash, lot_buffer=1.0)
        assert res.target_quantity["BOND.SH"] % 10 == 0
        assert res.target_quantity["BOND.SH"] > 0

    def test_cash_buffer(self):
        """lot_buffer=0.98 时分配不超过 98% 现金"""
        cash = 10_000.0
        targets = [Target("A.SH", 1.0, 10.0)]  # 一手 1000 元
        res = integer_lot_allocate(targets, cash, lot_buffer=0.98)
        assert sum(res.target_value.values()) <= cash * 0.98 + 1e-6


# ── 执行引擎 ─────────────────────────────────


def _mk_executor(capital: float = 10_000.0) -> tuple[Executor, PaperBroker]:
    broker = PaperBroker(initial_cash=capital)
    executor = Executor(ExecutorConfig(broker=broker, total_value=capital))
    return executor, broker


class TestMarketClock:
    def test_open_morning(self):
        clock = MarketClock()
        assert clock.is_open(dt.datetime(2026, 8, 14, 10, 0))  # 周五上午

    def test_closed_noon(self):
        clock = MarketClock()
        assert not clock.is_open(dt.datetime(2026, 8, 14, 12, 0))

    def test_closed_weekend(self):
        clock = MarketClock()
        assert not clock.is_open(dt.datetime(2026, 8, 16, 10, 0))  # 周日

    def test_closed_night(self):
        clock = MarketClock()
        assert not clock.is_open(dt.datetime(2026, 8, 14, 20, 0))


class TestExecutor:
    def test_buys_new_target(self):
        executor, broker = _mk_executor()
        broker.set_quote("AAA.SH", 10.0)
        # 交易时段内（周五上午）
        now = dt.datetime(2026, 8, 14, 10, 0)
        action = executor.rebalance({"AAA.SH": 1.0}, {"AAA.SH": 10.0}, now=now)
        assert action.kind == "rebalance"
        assert action.orders and action.orders[0].side is Side.BUY
        pos = executor.snapshot()["positions"]
        assert pos.get("AAA.SH", 0) > 0

    def test_skips_outside_hours(self):
        executor, broker = _mk_executor()
        broker.set_quote("AAA.SH", 10.0)
        now = dt.datetime(2026, 8, 14, 20, 0)  # 晚上
        action = executor.rebalance({"AAA.SH": 1.0}, {"AAA.SH": 10.0}, now=now)
        assert action.kind == "risk_skip"
        assert executor.snapshot()["positions"] == {}

    def test_sells_excess(self):
        executor, broker = _mk_executor()
        broker.set_quote("AAA.SH", 10.0)
        now = dt.datetime(2026, 8, 14, 10, 0)
        # 先建仓 AAA.SH
        executor.rebalance({"AAA.SH": 1.0}, {"AAA.SH": 10.0}, now=now)
        before = executor.snapshot()["positions"].get("AAA.SH", 0)
        assert before > 0
        # 切换到 BBB
        broker.set_quote("BBB.SH", 20.0)
        action = executor.rebalance({"BBB.SH": 1.0}, {"AAA.SH": 10.0, "BBB.SH": 20.0}, now=now)
        sells = [o for o in action.orders if o.side is Side.SELL]
        buys = [o for o in action.orders if o.side is Side.BUY]
        assert sells and any(o.symbol == "AAA.SH" for o in sells)
        assert buys and any(o.symbol == "BBB.SH" for o in buys)

    def test_noop_when_unchanged(self):
        executor, broker = _mk_executor()
        broker.set_quote("AAA.SH", 10.0)
        now = dt.datetime(2026, 8, 14, 10, 0)
        executor.rebalance({"AAA.SH": 1.0}, {"AAA.SH": 10.0}, now=now)
        # 再次同目标 → noop
        action = executor.rebalance({"AAA.SH": 1.0}, {"AAA.SH": 10.0}, now=now)
        assert action.kind == "noop"


# ── 实盘 MWU 信号层 ──────────────────────────

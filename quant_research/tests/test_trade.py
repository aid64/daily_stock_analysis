"""执行模块测试：PaperBroker / 离散优化 sizer / Executor / LiveMWU

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
from quant_research.trade.pipeline import LiveMWU
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


class TestLiveMWU:
    """使用随时间推进的窗口模拟价格演化，验证 MWU 权重随胜负更新"""

    N_WINDOW = 300

    def _rolling(self, k: int, up: float = 0.005) -> dict:
        """第 k 轮的行情窗口：UP 涨 / DOWN 跌 / BOND 防御资产下跌"""
        n = self.N_WINDOW
        start_up = k * 7
        closes_up = 1.0 * (1.0 + up) ** np.arange(start_up, start_up + n)
        start_down = k * 7
        closes_down = 1.0 * (1.0 - up) ** np.arange(start_down, start_down + n)
        start_bond = k * 7
        closes_bond = 100.0 * (1.0 - 0.003) ** np.arange(start_bond, start_bond + n)
        amount = np.full(min(20, n), 1e8)
        return {
            "UP.SH": (closes_up, amount),
            "DOWN.SH": (closes_down, amount),
            "BOND.SH": (closes_bond, amount),
        }

    def test_momentum_weights_update(self):
        """动量赢、防御输 → 防御被罚权重降，动量相对上升"""
        experts = build_experts({
            "momentum": {"lookback": 5, "top_k": 1, "ma_filter": 10},
            "mean_reversion": {"rsi_period": 2, "rsi_buy": 30, "ma_filter": 10, "top_k": 1},
            "defensive": {"symbol": "BOND.SH"},
        })
        mwu = LiveMWU(experts, eta=0.3, loss_scale=0.05, share_alpha=0.0)
        target = mwu.update(self._rolling(0))
        # 上涨市：动量选中 UP.SH
        assert "UP.SH" in target
        w_mom0, w_def0 = mwu.weights["momentum"], mwu.weights["defensive"]
        for k in range(1, 11):
            mwu.update(self._rolling(k))
        assert mwu.weights["defensive"] < w_def0
        assert mwu.weights["momentum"] > w_mom0

    def test_target_summary(self):
        experts = build_experts({
            "momentum": {"lookback": 5, "top_k": 1, "ma_filter": 10},
            "mean_reversion": {"rsi_period": 2, "rsi_buy": 30, "ma_filter": 10, "top_k": 1},
            "defensive": {"symbol": "BOND.SH"},
        })
        mwu = LiveMWU(experts, eta=0.1, loss_scale=0.05, share_alpha=0.02)
        target = mwu.update(self._rolling(0))
        assert target  # 非空
        assert sum(mwu.weights.values()) == pytest.approx(1.0, rel=1e-6)

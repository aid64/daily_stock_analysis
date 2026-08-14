"""回测引擎测试：佣金 / T+1 / 涨跌停 / 引擎流程

所有测试使用合成数据，不依赖网络。
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_research.backtest.broker import (
    AShareCommission,
    LimitUpDownFilter,
    TPlus1Broker,
    get_limit_pct,
)
from quant_research.backtest.engine import BacktestEngine, BacktestResult
from quant_research.backtest.strategies import SMACross
from quant_research.config.schema import (
    AppConfig,
    BacktestConfig,
    DataConfig,
    DataSource,
    TushareConfig,
)


# ── 辅助 ──────────────────────────────────────


def _make_config(initial_capital: float = 10000.0) -> AppConfig:
    """构造测试用 AppConfig（不触发网络）"""
    return AppConfig(
        data=DataConfig(primary=DataSource.BAOSTOCK),
        tushare=TushareConfig(token=""),
        backtest=BacktestConfig(initial_capital=initial_capital),
    )


def _make_synthetic_bars(n: int = 60, start_price: float = 10.0) -> pd.DataFrame:
    """生成合成 OHLC 数据（正弦波动，确保有金叉死叉）"""
    dates = pd.bdate_range("2024-01-01", periods=n)
    # 正弦波 + 趋势，确保 SMA 产生交叉信号
    t = np.linspace(0, 4 * np.pi, n)
    close = start_price + np.sin(t) * 2.0 + np.arange(n) * 0.05
    return pd.DataFrame({
        "date": dates,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "volume": np.random.randint(50000, 200000, n).astype(float),
        "amount": close * 100000,
    })


# ── 佣金测试 ──────────────────────────────────


class TestAShareCommission:
    def test_buy_commission(self):
        """买入佣金 = 金额 * 万1，无印花税"""
        comm = AShareCommission()
        # 买入 100 股 @ 100 元 = 10000 元
        fee = comm._getcommission(size=100, price=100.0, pseudoexec=False)
        # 佣金 = max(10000 * 0.0001, 5) = max(1, 5) = 5
        # 印花税 = 0（买入）
        # 过户费 = 10000 * 0.00002 = 0.2
        assert fee == pytest.approx(5 + 0.2, rel=0.001)

    def test_sell_commission_with_stamp_duty(self):
        """卖出佣金 = 佣金 + 印花税 + 过户费"""
        comm = AShareCommission()
        # 卖出 100 股 @ 100 元 = 10000 元
        fee = comm._getcommission(size=-100, price=100.0, pseudoexec=False)
        # 佣金 = max(10000 * 0.0001, 5) = 5
        # 印花税 = 10000 * 0.0005 = 5
        # 过户费 = 10000 * 0.00002 = 0.2
        assert fee == pytest.approx(5 + 5 + 0.2, rel=0.001)

    def test_min_commission_applied(self):
        """小额交易触发最低佣金 5 元"""
        comm = AShareCommission()
        # 买入 10 股 @ 10 元 = 100 元
        fee = comm._getcommission(size=10, price=10.0, pseudoexec=False)
        # 佣金 = max(100 * 0.0001, 5) = max(0.01, 5) = 5
        # 过户费 = 100 * 0.00002 = 0.002
        assert fee == pytest.approx(5 + 0.002, rel=0.001)

    def test_large_trade_no_min_cap(self):
        """大额交易佣金按比例计算，不受最低佣金影响"""
        comm = AShareCommission()
        # 买入 1000 股 @ 100 元 = 100000 元
        fee = comm._getcommission(size=1000, price=100.0, pseudoexec=False)
        # 佣金 = max(100000 * 0.0001, 5) = 10
        # 过户费 = 100000 * 0.00002 = 2
        assert fee == pytest.approx(10 + 2, rel=0.001)
        assert fee > 5.0  # 超过最低佣金

    def test_custom_rates(self):
        """自定义费率"""
        comm = AShareCommission(
            commission=0.0003,
            stamp_duty=0.001,
            transfer_fee=0.0001,
            min_commission=1.0,
        )
        # 卖出 100 股 @ 50 元 = 5000 元
        fee = comm._getcommission(size=-100, price=50.0, pseudoexec=False)
        # 佣金 = max(5000 * 0.0003, 1) = 1.5
        # 印花税 = 5000 * 0.001 = 5
        # 过户费 = 5000 * 0.0001 = 0.5
        assert fee == pytest.approx(1.5 + 5 + 0.5, rel=0.001)


# ── T+1 测试 ──────────────────────────────────


class TestTPlus1Broker:
    def test_cannot_sell_on_buy_day(self):
        """买入当日不能卖出"""
        broker = TPlus1Broker(t_plus_1=True)
        broker._last_buy_date = {}  # 模拟 start() 初始化
        broker.record_buy("600519.SH", "2024-01-15")
        assert broker.can_sell_t1("600519.SH", "2024-01-15") is False

    def test_can_sell_next_day(self):
        """次日可以卖出"""
        broker = TPlus1Broker(t_plus_1=True)
        broker._last_buy_date = {}
        broker.record_buy("600519.SH", "2024-01-15")
        assert broker.can_sell_t1("600519.SH", "2024-01-16") is True

    def test_disabled_t_plus_1(self):
        """关闭 T+1 后不限制"""
        broker = TPlus1Broker(t_plus_1=False)
        broker._last_buy_date = {}
        broker.record_buy("600519.SH", "2024-01-15")
        assert broker.can_sell_t1("600519.SH", "2024-01-15") is True

    def test_no_buy_record_allows_sell(self):
        """没有买入记录（持仓来自之前）允许卖出"""
        broker = TPlus1Broker(t_plus_1=True)
        broker._last_buy_date = {}
        assert broker.can_sell_t1("600519.SH", "2024-01-15") is True


# ── 涨跌停测试 ────────────────────────────────


class TestLimitUpDownFilter:
    def test_limit_up_blocks_buy(self):
        """涨停不能买入"""
        f = LimitUpDownFilter(limit_pct=0.1)
        # 前收 10 元，涨停 11 元
        assert f.can_buy(11.0, 10.0) is False

    def test_limit_down_blocks_sell(self):
        """跌停不能卖出"""
        f = LimitUpDownFilter(limit_pct=0.1)
        # 前收 10 元，跌停 9 元
        assert f.can_sell(9.0, 10.0) is False

    def test_normal_price_allows_trade(self):
        """正常价格可交易"""
        f = LimitUpDownFilter(limit_pct=0.1)
        assert f.can_buy(10.5, 10.0) is True
        assert f.can_sell(10.5, 10.0) is True

    def test_zero_prev_close(self):
        """前收盘为 0 时不判定涨跌停"""
        f = LimitUpDownFilter(limit_pct=0.1)
        assert f.is_limit_up(10.0, 0.0) is False
        assert f.is_limit_down(5.0, 0.0) is False

    def test_get_limit_pct_main_board(self):
        """主板 10%"""
        assert get_limit_pct("600519.SH") == 0.1
        assert get_limit_pct("000001.SZ") == 0.1

    def test_get_limit_pct_chinext(self):
        """创业板 20%"""
        assert get_limit_pct("300750.SZ") == 0.2
        assert get_limit_pct("301088.SZ") == 0.2

    def test_get_limit_pct_star(self):
        """科创板 20%"""
        assert get_limit_pct("688981.SH") == 0.2

    def test_get_limit_pct_bse(self):
        """北交所 30%"""
        assert get_limit_pct("430047.BJ") == 0.3
        assert get_limit_pct("830799.BJ") == 0.3


# ── 引擎流程测试 ──────────────────────────────


class TestBacktestEngine:
    def test_run_with_synthetic_data(self):
        """用合成数据跑通完整回测流程"""
        cfg = _make_config(initial_capital=10000.0)
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        df = _make_synthetic_bars(n=80)

        result = engine.run(SMACross, {"fast_period": 5, "slow_period": 20}, {"TEST.SH": df})

        assert isinstance(result, BacktestResult)
        assert result.final_value > 0
        assert result.total_trades > 0  # 合成数据应产生交易
        assert -1.0 <= result.total_return <= 10.0  # 合理范围
        assert result.max_drawdown >= 0

    def test_empty_data_feeds_raises(self):
        """空数据应抛 ValueError"""
        cfg = _make_config()
        engine = BacktestEngine(cfg)
        with pytest.raises(ValueError, match="data_feeds"):
            engine.run(SMACross, {}, {})

    def test_result_summary_format(self):
        """summary() 输出包含关键指标"""
        r = BacktestResult(
            final_value=12000,
            total_return=0.2,
            annual_return=0.05,
            sharpe=0.3,
            max_drawdown=0.15,
            calmar=0.33,
            total_trades=10,
            win_rate=0.5,
            profit_factor=1.5,
        )
        s = r.summary()
        assert "最终资金" in s
        assert "12,000.00" in s
        assert "夏普比率" in s
        assert "盈亏比" in s

    def test_short_data_no_trade(self):
        """数据足以计算 SMA 但无交叉信号时不交易"""
        cfg = _make_config()
        engine = BacktestEngine(cfg)
        # 25 行平盘数据：SMA(20) 可计算，但价格不变不会产生交叉
        dates = pd.bdate_range("2024-01-01", periods=25)
        df = pd.DataFrame({
            "date": dates,
            "open": [10.0] * 25,
            "high": [10.0] * 25,
            "low": [10.0] * 25,
            "close": [10.0] * 25,
            "volume": [100000.0] * 25,
            "amount": [1000000.0] * 25,
        })
        result = engine.run(SMACross, {"fast_period": 5, "slow_period": 20}, {"TEST.SH": df})
        assert result.total_trades == 0

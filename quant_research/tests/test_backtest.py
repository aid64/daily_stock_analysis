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
from quant_research.backtest.strategies import SMACross, ETFMomentum
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
        # 过户费 = 10000 * 0.00001 = 0.1（2022-07 降费后 0.001%）
        assert fee == pytest.approx(5 + 0.1, rel=0.001)

    def test_sell_commission_with_stamp_duty(self):
        """卖出佣金 = 佣金 + 印花税 + 过户费"""
        comm = AShareCommission()
        # 卖出 100 股 @ 100 元 = 10000 元
        fee = comm._getcommission(size=-100, price=100.0, pseudoexec=False)
        # 佣金 = max(10000 * 0.0001, 5) = 5
        # 印花税 = 10000 * 0.0005 = 5
        # 过户费 = 10000 * 0.00001 = 0.1
        assert fee == pytest.approx(5 + 5 + 0.1, rel=0.001)

    def test_min_commission_applied(self):
        """小额交易触发最低佣金 5 元"""
        comm = AShareCommission()
        # 买入 10 股 @ 10 元 = 100 元
        fee = comm._getcommission(size=10, price=10.0, pseudoexec=False)
        # 佣金 = max(100 * 0.0001, 5) = max(0.01, 5) = 5
        # 过户费 = 100 * 0.00001 = 0.001
        assert fee == pytest.approx(5 + 0.001, rel=0.001)

    def test_large_trade_no_min_cap(self):
        """大额交易佣金按比例计算，不受最低佣金影响"""
        comm = AShareCommission()
        # 买入 1000 股 @ 100 元 = 100000 元
        fee = comm._getcommission(size=1000, price=100.0, pseudoexec=False)
        # 佣金 = max(100000 * 0.0001, 5) = 10
        # 过户费 = 100000 * 0.00001 = 1（2022-07 降费后 0.001%）
        assert fee == pytest.approx(10 + 1, rel=0.001)
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
        assert result.completed_orders > 0
        assert result.buy_orders > 0
        assert result.sell_orders > 0
        assert result.traded_value > 0
        assert result.commission_paid > 0
        assert result.turnover > 0
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
            closed_trades=7,
            open_trades=3,
            win_rate=0.5,
            profit_factor=1.5,
        )
        s = r.summary()
        assert "最终资金" in s
        assert "12,000.00" in s
        assert "夏普比率" in s
        assert "盈亏比" in s
        assert "开仓交易: 10（平仓 7 / 持仓中 3）" in s

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

    def test_in_life_gap_forward_filled(self):
        """生命周期内的数据洞应前向填充，不得产生 NaN 伪崩塌

        场景：A.SH 缺失一个 B.SH 有的交易日（数据源缺口）。若不填充，
        持仓在洞日按 NaN 定价，净值出现 -95% 级别的单日伪回撤。
        """
        import backtrader as bt

        class BuyAndHoldFirst(bt.Strategy):
            def next(self):
                if self.getposition(self.datas[0]).size == 0:
                    # 仓位需占组合绝对主导，否则洞日"持仓估值记 0"
                    # 的伪回撤可能小于断言阈值（1% 仓位只造成 1% 回撤）
                    self.buy(self.datas[0], size=9000)

        dates = pd.bdate_range("2024-01-01", periods=10)
        close = np.linspace(10.0, 10.5, 10)

        def _bars(idx, closes):
            return pd.DataFrame({
                "date": idx,
                "open": np.asarray(closes) * 0.99,
                "high": np.asarray(closes) * 1.02,
                "low": np.asarray(closes) * 0.98,
                "close": closes,
                "volume": 100000.0,
                "amount": np.asarray(closes) * 100000,
            })

        a_gap = _bars(dates.delete(5), close[np.arange(10) != 5])  # 缺第 6 个交易日
        b_full = _bars(dates, np.full(10, 20.0))

        cfg = _make_config(initial_capital=100000.0)  # 资金充足避免保证金问题
        engine = BacktestEngine(cfg)
        result = engine.run(BuyAndHoldFirst, {}, {"A.SH": a_gap, "B.SH": b_full})

        assert result.final_value > 90000  # 价格平稳，净值不应崩塌
        assert result.max_drawdown < 0.10


# ── ETF 动量轮动测试 ──────────────────────────


def _make_trend_bars(
    n: int,
    daily_ret: float,
    start_price: float = 1.0,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """生成固定日收益率的 ETF 合成数据（含 amount 列）"""
    dates = pd.bdate_range(start, periods=n)
    close = start_price * (1.0 + daily_ret) ** np.arange(n)
    return pd.DataFrame({
        "date": dates,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.full(n, 1_000_000.0),
        "amount": close * 1_000_000.0,
    })


_SMALL_MOMENTUM = {
    "lookback": 5,
    "top_k": 1,
    "ma_filter": 10,
    "min_volume": 0.0,
    "rebalance_freq": "W",
}


class _RebalanceSpy(ETFMomentum):
    """记录调仓日期与买卖成交记录的探针策略"""

    rebalance_dates: list = []
    buy_fills: list = []   # (symbol, date)
    sell_fills: list = []  # (symbol, date)

    def _is_rebalance_day(self, dt):
        fired = super()._is_rebalance_day(dt)
        if fired:
            self.rebalance_dates.append(dt)
        return fired

    def notify_order(self, order):
        if order.status == order.Completed:
            dt = self.datas[0].datetime.date(0)
            if order.isbuy():
                self.buy_fills.append((order.data._name, dt))
            else:
                self.sell_fills.append((order.data._name, dt))


class TestETFMomentum:
    def test_all_downtrend_holds_cash(self):
        """全部标的下跌（无绝对动量）→ 空仓持币"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        feeds = {
            "DOWN1.SH": _make_trend_bars(80, -0.002),
            "DOWN2.SH": _make_trend_bars(80, -0.001),
        }
        result = engine.run(ETFMomentum, dict(_SMALL_MOMENTUM), feeds)
        assert result.total_trades == 0
        assert result.completed_orders == 0
        assert result.traded_value == 0
        assert result.turnover == 0
        assert result.final_value == pytest.approx(cfg.backtest.initial_capital)

    def test_buys_strongest_uptrend(self):
        """强/弱上涨标的并存 → 买入最强者并获利"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        feeds = {
            "STRONG.SH": _make_trend_bars(80, 0.005),
            "WEAK.SH": _make_trend_bars(80, 0.001),
        }
        _RebalanceSpy.buy_fills = []
        result = engine.run(_RebalanceSpy, dict(_SMALL_MOMENTUM), feeds)
        # TradeAnalyzer 只统计已平仓交易，期末持仓未平 → 用成交记录断言
        bought = {sym for sym, _ in _RebalanceSpy.buy_fills}
        assert "STRONG.SH" in bought
        assert result.final_value > cfg.backtest.initial_capital

    def test_late_listing_excluded_until_real_data(self):
        """晚上市标的 NaN 对齐后，真实数据前不得买入"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        early = _make_trend_bars(120, 0.004)  # 全程数据，持续上涨
        # LATE 从 union 第 61 个交易日起才有真实数据（真正"晚上市"）
        late_start = early["date"].iloc[60]
        late = _make_trend_bars(60, 0.02, start=str(late_start.date()))

        _RebalanceSpy.rebalance_dates = []
        _RebalanceSpy.buy_fills = []
        engine.run(_RebalanceSpy, dict(_SMALL_MOMENTUM), {"EARLY.SH": early, "LATE.SH": late})

        late_buys = [d for sym, d in _RebalanceSpy.buy_fills if sym == "LATE.SH"]
        for d in late_buys:
            assert pd.Timestamp(d) >= pd.Timestamp(late_start), (
                f"LATE.SH 在真实数据前被买入: {d} < {late_start}"
            )

    def test_rebalance_only_first_day_of_week(self):
        """周度模式：调仓只发生在每周第一个交易日（周一）"""
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))
        feeds = {"UP.SH": _make_trend_bars(80, 0.003)}

        _RebalanceSpy.rebalance_dates = []
        _RebalanceSpy.buy_fills = []
        engine.run(_RebalanceSpy, dict(_SMALL_MOMENTUM), feeds)

        assert len(_RebalanceSpy.rebalance_dates) >= 8  # 80 交易日 ≈ 16 周
        weeks = set()
        for d in _RebalanceSpy.rebalance_dates:
            assert d.weekday() == 0  # bdate 数据周一为每周首个交易日
            iso = d.isocalendar()
            weeks.add((iso[0], iso[1]))
        assert len(weeks) == len(_RebalanceSpy.rebalance_dates)  # 每周仅一次


# ── ETF 动量轮动 P2 增强测试 ──────────────────


def _make_shaped_bars(daily_rets: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """按逐日收益率序列生成合成数据（总长度 = len(daily_rets)）"""
    n = len(daily_rets)
    dates = pd.bdate_range(start, periods=n)
    close = 1.0 * np.cumprod([1.0 + r for r in daily_rets])
    return pd.DataFrame({
        "date": dates,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.full(n, 1_000_000.0),
        "amount": close * 1_000_000.0,
    })


class TestETFMomentumP2:
    """P2 新参数：多窗口动量 / 波动率调整 / 止损带 / 防御轮动"""

    def test_multi_window_changes_ranking(self):
        """多窗口动量：短窗口失效但长窗口仍强的标的应继续持有

        A：前 65 日平盘 → 5 日 +1%/日 → 后 10 日平盘
           期末（最后调仓日 bar76）5 日动量 = 0（绝对动量排除），
           (5,10) 平均动量 ≈ +1.5%
        B：前 70 日平盘 → 10 日 +0.1%/日
           期末 5 日动量 ≈ +0.5%，(5,10) 平均 ≈ +0.55%
        单窗口 lookback=5 → 期末卖出 A 换入 B；
        多窗口 (5,10) → A 平均动量更高，继续持有 A。
        断言以最后一次买入成交为准（两者上涨初期都会先买 A）。
        """
        flat = [0.0] * 65
        a = _make_shaped_bars(flat + [0.01] * 5 + [0.0] * 10)
        b = _make_shaped_bars([0.0] * 70 + [0.001] * 10)
        cfg = _make_config(200000.0)  # 充足资金，避免 lots 取整+滑点导致买单被拒
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))

        def last_bought() -> str:
            return max(_RebalanceSpy.buy_fills, key=lambda item: item[1])[0]

        _RebalanceSpy.buy_fills = []
        _RebalanceSpy.sell_fills = []
        engine.run(_RebalanceSpy, dict(_SMALL_MOMENTUM), {"A.SH": a, "B.SH": b})
        single = last_bought()

        _RebalanceSpy.buy_fills = []
        _RebalanceSpy.sell_fills = []
        engine.run(
            _RebalanceSpy,
            dict(_SMALL_MOMENTUM, mom_windows=(5, 10)),
            {"A.SH": a, "B.SH": b},
        )
        multi = last_bought()

        assert single == "B.SH"   # 短动量归零 → 换仓到 B
        assert multi == "A.SH"    # 多窗口平均仍强 → 保持 A

    def test_vol_adjusted_prefers_low_vol(self):
        """波动率调整排序：高动量高波动让位给低波动标的

        HIGH：+2.4%/+0.4% 交替（均值 1.4%，日波动 ≈ 1.0%）
        LOW： +0.45%/+0.35% 交替（均值 0.4%，日波动 ≈ 0.05%）
        默认排序 HIGH 动量高入选；vol_adjusted 后 LOW 得分反超。
        """
        high = _make_shaped_bars([0.024, 0.004] * 40)
        low = _make_shaped_bars([0.0045, 0.0035] * 40)
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))

        _RebalanceSpy.buy_fills = []
        _RebalanceSpy.sell_fills = []
        engine.run(_RebalanceSpy, dict(_SMALL_MOMENTUM), {"HIGH.SH": high, "LOW.SH": low})
        plain = {sym for sym, _ in _RebalanceSpy.buy_fills}

        _RebalanceSpy.buy_fills = []
        _RebalanceSpy.sell_fills = []
        engine.run(
            _RebalanceSpy,
            dict(_SMALL_MOMENTUM, vol_adjusted=True),
            {"HIGH.SH": high, "LOW.SH": low},
        )
        adjusted = {sym for sym, _ in _RebalanceSpy.buy_fills}

        assert plain == {"HIGH.SH"}
        assert adjusted == {"LOW.SH"}

    def test_stop_loss_exits_before_rebalance(self):
        """止损带：暴跌日为周中，止损应在下周调仓日前离场

        数据从周二起。bar75-76 连续两日 -9%（周二/周三，单日均未触及
        主板 -10% 跌停——R2 broker 强制闸会拒单跌停日卖出，场景须保持
        物理可行；累计约 -17% 使价格跌破入场均价 -8% 止损线，bar76
        收盘触发）。调仓日均为周一，暴跌周内无调仓。
        无止损 → 持仓到期末，开仓 1 笔未平仓（total_trades=1,
        closed_trades=0）；
        有止损(-8%) → bar77 开盘平仓，closed_trades=1。
        """
        rets = [0.005] * 30 + [0.0005] * 45 + [-0.095, -0.095] + [0.0005] * 2
        bars = _make_shaped_bars(rets, start="2024-01-02")  # 2024-01-02 是周二
        cfg = _make_config(200000.0)
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))

        result_plain = engine.run(ETFMomentum, dict(_SMALL_MOMENTUM), {"UP.SH": bars})
        assert result_plain.total_trades == 1  # 开仓 1 笔
        assert result_plain.closed_trades == 0  # 一直持有未平仓
        assert result_plain.open_trades == 1
        assert result_plain.win_rate == 0  # 无平仓回合 → 胜率不可得

        _RebalanceSpy.buy_fills = []
        _RebalanceSpy.sell_fills = []
        result_stop = engine.run(
            _RebalanceSpy,
            dict(_SMALL_MOMENTUM, stop_loss_pct=-0.08),
            {"UP.SH": bars},
        )
        assert result_stop.closed_trades == 1
        sell_dates = [d for sym, d in _RebalanceSpy.sell_fills if sym == "UP.SH"]
        crash_next = bars["date"].iloc[77]  # 止损触发次日（bar77）
        for d in sell_dates:
            assert pd.Timestamp(d) <= pd.Timestamp(crash_next)

    def test_defensive_rotation_when_no_candidates(self):
        """防御轮动：风险动量失效后全仓切换到防御资产

        注：回测初期（动量窗口未满）候选为空也会先持防御，
        断言聚焦轮动语义——风险清仓后防御再次被买入。
        """
        risk = _make_shaped_bars([0.005] * 60 + [-0.003] * 20)
        defensive = _make_shaped_bars([0.0001] * 80)
        cfg = _make_config(200000.0)
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))

        _RebalanceSpy.buy_fills = []
        _RebalanceSpy.sell_fills = []
        engine.run(
            _RebalanceSpy,
            dict(_SMALL_MOMENTUM, defensive="511010.SH"),
            {"510300.SH": risk, "511010.SH": defensive},
        )
        bought = {sym for sym, _ in _RebalanceSpy.buy_fills}

        assert "510300.SH" in bought   # 持有过风险资产
        assert "511010.SH" in bought   # 持有过防御资产
        # 轮动时序：最后一次防御买入晚于最后一次风险卖出（风险→防御切换）
        last_def = max(d for sym, d in _RebalanceSpy.buy_fills if sym == "511010.SH")
        risk_sells = [d for sym, d in _RebalanceSpy.sell_fills if sym == "510300.SH"]
        assert risk_sells, "风险资产从未被卖出"
        assert pd.Timestamp(last_def) > pd.Timestamp(max(risk_sells))

    def test_defensive_excluded_from_momentum_ranking(self):
        """防御资产不参与动量排序：即使动量弱也能被轮动持有"""
        # 防御资产走平（动量 0，若参与排序会被绝对动量排除）
        defensive = _make_shaped_bars([0.0] * 80)
        risk = _make_shaped_bars([0.005] * 40 + [-0.004] * 40)
        cfg = _make_config()
        engine = BacktestEngine(cfg, logger=logging.getLogger("test"))

        _RebalanceSpy.buy_fills = []
        _RebalanceSpy.sell_fills = []
        engine.run(
            _RebalanceSpy,
            dict(_SMALL_MOMENTUM, defensive="511010.SH"),
            {"510300.SH": risk, "511010.SH": defensive},
        )
        bought = {sym for sym, _ in _RebalanceSpy.buy_fills}
        assert "511010.SH" in bought

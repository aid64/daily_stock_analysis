"""Backtrader 引擎封装

提供：
- BacktestResult：回测结果数据类
- BacktestEngine：封装 Cerebro + A股 Broker + Analyzers

用法：
    from quant_research.backtest import BacktestEngine
    from quant_research.data import DataLoader
    from quant_research.config import load_config

    cfg = load_config()
    loader = DataLoader(cfg)
    df = loader.get_daily_bars("600519.SH", "2020-01-01", "2024-12-31")

    engine = BacktestEngine(cfg)
    result = engine.run(SimpleStrategy, {"period": 20}, {"600519.SH": df})
    print(result.summary())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Type

import backtrader as bt
import pandas as pd

from ..config.schema import AppConfig
from .broker import AShareCommission, TPlus1Broker, get_limit_pct, LimitUpDownFilter


@dataclass
class BacktestResult:
    """回测结果"""

    # 核心指标
    final_value: float = 0.0
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    calmar: float = 0.0

    # 交易统计
    total_trades: int = 0
    won_trades: int = 0
    lost_trades: int = 0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0

    # 原始数据
    equity_curve: Optional[pd.Series] = None
    trades: list = field(default_factory=list)

    def summary(self) -> str:
        """生成文本摘要"""
        return (
            f"=== 回测结果 ===\n"
            f"  最终资金: {self.final_value:,.2f}\n"
            f"  总收益: {self.total_return:.2%}\n"
            f"  年化收益: {self.annual_return:.2%}\n"
            f"  夏普比率: {self.sharpe:.3f}\n"
            f"  最大回撤: {self.max_drawdown:.2%}\n"
            f"  Calmar: {self.calmar:.3f}\n"
            f"  总交易: {self.total_trades}\n"
            f"  胜率: {self.win_rate:.2%}\n"
            f"  盈亏比: {self.profit_factor:.2f}"
        )


class BacktestEngine:
    """Backtrader 引擎封装

    特性：
    - A 股佣金 / 印花税 / 过户费 / 最低 5 元
    - T+1 限制
    - 涨跌停过滤
    - Sharpe / DrawDown / Returns / TradeAnalyzer
    - PyFolio 输出（可对接 QuantStats）
    """

    def __init__(self, config: AppConfig, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or logging.getLogger("backtest")
        self._cerebro: Optional[bt.Cerebro] = None

    def _setup_broker(self, cerebro: bt.Cerebro) -> None:
        """配置 A 股 Broker"""
        cfg = self.config.backtest
        broker = TPlus1Broker(t_plus_1=cfg.allow_t_plus_1)
        broker.setcash(cfg.initial_capital)
        cerebro.broker = broker

        # 佣金
        comminfo = AShareCommission(
            commission=cfg.commission_rate,
            stamp_duty=cfg.stamp_duty,
            transfer_fee=cfg.transfer_fee,
            min_commission=cfg.min_commission,
        )
        cerebro.broker.addcommissioninfo(comminfo)

        # 滑点
        cerebro.broker.set_slippage_perc(cfg.slippage)

    def _setup_analyzers(self, cerebro: bt.Cerebro) -> None:
        """配置分析器"""
        cerebro.addanalyzer(
            bt.analyzers.SharpeRatio,
            _name="sharpe",
            timeframe=bt.TimeFrame.Days,
            annualize=True,
            riskfreerate=0.02,  # 无风险利率 2%
        )
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
        cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="timereturn", timeframe=bt.TimeFrame.Days)
        cerebro.addanalyzer(bt.analyzers.PyFolio, _name="pyfolio")

    def run(
        self,
        strategy_cls: Type[bt.Strategy],
        strategy_params: Optional[dict] = None,
        data_feeds: Optional[dict[str, pd.DataFrame]] = None,
    ) -> BacktestResult:
        """运行回测

        Args:
            strategy_cls: Backtrader 策略类
            strategy_params: 策略参数
            data_feeds: {symbol: DataFrame}，DataFrame 需含 date/open/high/low/close/volume 列

        Returns:
            BacktestResult
        """
        if data_feeds is None or len(data_feeds) == 0:
            raise ValueError("data_feeds 不能为空")

        cerebro = bt.Cerebro(stdstats=False)
        self._setup_broker(cerebro)
        self._setup_analyzers(cerebro)

        # 添加数据源
        for symbol, df in data_feeds.items():
            if df is None or len(df) == 0:
                self.logger.warning(f"跳过空数据: {symbol}")
                continue

            # 确保日期为索引
            df = df.copy()
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            df = df.sort_index()

            # Backtrader 需要 openinterest 列（可为 0）
            if "openinterest" not in df.columns:
                df["openinterest"] = 0.0

            feed = bt.feeds.PandasData(dataname=df, name=symbol)
            cerebro.adddata(feed, name=symbol)

        # 添加策略
        params = strategy_params or {}
        cerebro.addstrategy(strategy_cls, **params)

        # 设置初始资金（再次确认）
        cerebro.broker.setcash(self.config.backtest.initial_capital)

        self.logger.info(
            f"开始回测: 策略={strategy_cls.__name__}, 标的={list(data_feeds.keys())}, "
            f"初始资金={self.config.backtest.initial_capital:,.0f}"
        )

        results = cerebro.run()
        strat = results[0]

        # 提取指标
        result = self._extract_metrics(strat, cerebro)

        self.logger.info(result.summary())
        return result

    def _extract_metrics(self, strat: bt.Strategy, cerebro: bt.Cerebro) -> BacktestResult:
        """从策略实例提取回测指标"""
        result = BacktestResult()

        # 最终资金
        result.final_value = cerebro.broker.getvalue()
        initial = self.config.backtest.initial_capital
        result.total_return = (result.final_value - initial) / initial

        # Sharpe
        try:
            sharpe_analysis = strat.analyzers.sharpe.get_analysis()
            result.sharpe = float(sharpe_analysis.get("sharperatio", 0) or 0)
        except Exception:
            result.sharpe = 0.0

        # 最大回撤
        try:
            dd_analysis = strat.analyzers.drawdown.get_analysis()
            result.max_drawdown = float(dd_analysis.max.drawdown / 100)
        except Exception:
            result.max_drawdown = 0.0

        # 年化收益
        try:
            returns_analysis = strat.analyzers.returns.get_analysis()
            result.annual_return = float(returns_analysis.get("rnorm100", 0) / 100)
        except Exception:
            result.annual_return = 0.0

        # Calmar = 年化收益 / 最大回撤
        if result.max_drawdown > 0:
            result.calmar = abs(result.annual_return / result.max_drawdown)

        # 交易统计
        try:
            trades_analysis = strat.analyzers.trades.get_analysis()
            result.total_trades = trades_analysis.total.closed if hasattr(trades_analysis, "total") else 0
            if result.total_trades > 0:
                won = trades_analysis.won.total if hasattr(trades_analysis, "won") else 0
                lost = trades_analysis.lost.total if hasattr(trades_analysis, "lost") else 0
                result.won_trades = won
                result.lost_trades = lost
                result.win_rate = won / result.total_trades if result.total_trades > 0 else 0

                avg_profit = trades_analysis.won.pnl.average if hasattr(trades_analysis, "won") and won > 0 else 0
                avg_loss = trades_analysis.lost.pnl.average if hasattr(trades_analysis, "lost") and lost > 0 else 0
                result.avg_profit = float(avg_profit or 0)
                result.avg_loss = float(avg_loss or 0)

                # TradeAnalyzer 结构：won.pnl.{total,average,max}（无 .net 层）
                total_profit = trades_analysis.won.pnl.total if hasattr(trades_analysis, "won") else 0
                total_loss = abs(trades_analysis.lost.pnl.total) if hasattr(trades_analysis, "lost") else 0
                result.profit_factor = float(total_profit / total_loss) if total_loss > 0 else 0.0
        except Exception as e:
            self.logger.warning(f"交易统计提取失败: {e!r}")

        # 权益曲线
        try:
            timereturn = strat.analyzers.timereturn.get_analysis()
            result.equity_curve = pd.Series(timereturn)
        except Exception:
            pass

        return result

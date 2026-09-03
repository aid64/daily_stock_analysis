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


class PandasBarsData(bt.feeds.PandasData):
    """PandasData 扩展：增加 amount（成交额）line，供流动性过滤使用

    所有数据源的 normalize_ohlc 均保证 amount 列存在。
    """

    lines = ("amount",)
    params = (("amount", -1),)  # -1 = 按列名自动定位


class OrderStatsAnalyzer(bt.Analyzer):
    """记录成交订单级别的金额、费用和状态统计。

    record_fills=False 时不保留逐单明细（大规模网格省内存），
    聚合统计不受影响。
    """

    params = (("record_fills", True),)

    def start(self):
        self._stats = {
            "completed_orders": 0,
            "buy_orders": 0,
            "sell_orders": 0,
            "buy_value": 0.0,
            "sell_value": 0.0,
            "traded_value": 0.0,
            "commission": 0.0,
            "rejected_orders": 0,
            "margin_orders": 0,
            "canceled_orders": 0,
        }
        self._fills = [] if self.p.record_fills else None
        self._equity_values = []

    def next(self):
        self._equity_values.append(float(self.strategy.broker.getvalue()))

    def notify_order(self, order):
        status = order.status
        if status == order.Completed:
            size = abs(float(order.executed.size or 0.0))
            price = float(order.executed.price or 0.0)
            value = size * price
            self._stats["completed_orders"] += 1
            self._stats["traded_value"] += value
            self._stats["commission"] += float(order.executed.comm or 0.0)
            if order.isbuy():
                self._stats["buy_orders"] += 1
                self._stats["buy_value"] += value
            else:
                self._stats["sell_orders"] += 1
                self._stats["sell_value"] += value
            try:
                fill_date = bt.num2date(order.executed.dt).date()
            except (TypeError, ValueError):
                fill_date = None
            if self._fills is not None:
                self._fills.append({
                    "date": fill_date,
                    "symbol": getattr(order.data, "_name", None),
                    "side": "buy" if order.isbuy() else "sell",
                    "size": float(order.executed.size or 0.0),
                    "price": price,
                    "value": value,
                    "commission": float(order.executed.comm or 0.0),
                })
        elif status == order.Rejected:
            self._stats["rejected_orders"] += 1
        elif status == order.Margin:
            self._stats["margin_orders"] += 1
        elif status == order.Canceled:
            self._stats["canceled_orders"] += 1

    def get_analysis(self):
        analysis = dict(self._stats)
        analysis["fills"] = list(self._fills) if self._fills is not None else []
        analysis["average_equity"] = (
            sum(self._equity_values) / len(self._equity_values)
            if self._equity_values else 0.0
        )
        return analysis


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
    closed_trades: int = 0
    open_trades: int = 0
    won_trades: int = 0
    lost_trades: int = 0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    completed_orders: int = 0
    buy_orders: int = 0
    sell_orders: int = 0
    buy_value: float = 0.0
    sell_value: float = 0.0
    traded_value: float = 0.0
    commission_paid: float = 0.0
    rejected_orders: int = 0
    margin_orders: int = 0
    canceled_orders: int = 0
    turnover: float = 0.0

    # 原始数据
    equity_curve: Optional[pd.Series] = None
    trades: list = field(default_factory=list)
    fills: list = field(default_factory=list)

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
            f"  开仓交易: {self.total_trades}（平仓 {self.closed_trades} / 持仓中 {self.open_trades}）\n"
            f"  成交订单: {self.completed_orders}\n"
            f"  成交额: {self.traded_value:,.2f}\n"
            f"  区间换手率: {self.turnover:.2f}x\n"
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

    def __init__(
        self,
        config: AppConfig,
        logger: Optional[logging.Logger] = None,
        commission_overrides: Optional[dict] = None,
        record_fills: bool = True,
    ):
        self.config = config
        self.logger = logger or logging.getLogger("backtest")
        self._commission_overrides = dict(commission_overrides) if commission_overrides else {}
        # fills 逐单明细供 paper 三表/对账用；大规模网格逐配置回测可关
        # （O(订单数) 内存，聚合统计 completed_orders/commission 不受影响）
        self._record_fills = record_fills
        self._cerebro: Optional[bt.Cerebro] = None

    def _setup_broker(self, cerebro: bt.Cerebro) -> None:
        """配置 A 股 Broker"""
        cfg = self.config.backtest
        broker = TPlus1Broker(t_plus_1=cfg.allow_t_plus_1)
        broker.setcash(cfg.initial_capital)
        cerebro.broker = broker

        # 佣金（ETF 等品种可通过 commission_overrides 免印花税/过户费）
        comm_params = {
            "commission": cfg.commission_rate,
            "stamp_duty": cfg.stamp_duty,
            "transfer_fee": cfg.transfer_fee,
            "min_commission": cfg.min_commission,
        }
        comm_params.update(self._commission_overrides)
        comminfo = AShareCommission(**comm_params)
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
        cerebro.addanalyzer(OrderStatsAnalyzer, _name="order_stats",
                            record_fills=self._record_fills)
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

        # 第一遍：预处理各标的（日期索引 + 排序 + openinterest）
        prepared: dict[str, pd.DataFrame] = {}
        for symbol, df in data_feeds.items():
            if df is None or len(df) == 0:
                self.logger.warning(f"跳过空数据: {symbol}")
                continue

            df = df.copy()
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            df = df.sort_index()
            df = df[~df.index.duplicated(keep="last")]

            # Backtrader 需要 openinterest 列（可为 0）
            if "openinterest" not in df.columns:
                df["openinterest"] = 0.0

            prepared[symbol] = df

        # 第二遍：多标的日期对齐（晚上市标的用 NaN 填充早期缺口）。
        # Backtrader 默认等所有 feed 就绪才调用 next()，若不对齐，
        # 回测起点会被最晚上市的标的拖后；NaN 填充后由策略层
        # 在动量/均线窗口中检测 NaN 自动排除该标的。
        #
        # 标的生命周期内的数据洞（数据源缺失个别交易日，如 510500
        # 缺 2015-04-13/14）同样会产生 NaN bar：持仓按 NaN 价格
        # 估值/成交，单日"回撤"可达 -95% 以上。因此对 [上市日,
        # 最后交易日] 区间内的洞做前向填充；上市前的区间保持 NaN，
        # 仍由策略层排除。
        if len(prepared) > 1:
            union_index = sorted(set().union(*(df.index for df in prepared.values())))
            aligned: dict[str, pd.DataFrame] = {}
            for sym, df in prepared.items():
                out = df.reindex(union_index)
                in_life = (out.index >= df.index[0]) & (out.index <= df.index[-1])
                out.loc[in_life] = out.loc[in_life].ffill()
                aligned[sym] = out
            prepared = aligned

        # 第三遍：添加数据源
        for symbol, df in prepared.items():
            feed = PandasBarsData(dataname=df, name=symbol)
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
            def analysis_value(node, *keys, default=0):
                for key in keys:
                    if node is None:
                        return default
                    if isinstance(node, dict):
                        node = node.get(key)
                    else:
                        node = getattr(node, key, None)
                return default if node is None else node

            # 口径拆分（ROADMAP-V2 F3）：两段式/调仓型策略持仓很少归零，
            # 只报 closed 会让 total_trades 恒为 0、与 52x 换手矛盾。
            # total_trades=开仓数（含未平仓），closed_trades=已平仓回合数，
            # 胜率/盈亏等只基于 closed_trades；订单数另见 completed_orders。
            closed = int(analysis_value(trades_analysis, "total", "closed") or 0)
            opened = int(analysis_value(trades_analysis, "total", "total") or 0)
            result.closed_trades = closed
            result.open_trades = max(opened - closed, 0)
            result.total_trades = opened if opened > 0 else closed
            if closed > 0:
                won = int(analysis_value(trades_analysis, "won", "total"))
                lost = int(analysis_value(trades_analysis, "lost", "total"))
                result.won_trades = won
                result.lost_trades = lost
                result.win_rate = won / closed if closed > 0 else 0

                avg_profit = analysis_value(trades_analysis, "won", "pnl", "average") if won > 0 else 0
                avg_loss = analysis_value(trades_analysis, "lost", "pnl", "average") if lost > 0 else 0
                result.avg_profit = float(avg_profit or 0)
                result.avg_loss = float(avg_loss or 0)

                total_profit = analysis_value(trades_analysis, "won", "pnl", "total")
                total_loss = abs(analysis_value(trades_analysis, "lost", "pnl", "total"))
                result.profit_factor = float(total_profit / total_loss) if total_loss > 0 else 0.0
        except Exception as e:
            self.logger.warning(f"交易统计提取失败: {e!r}")

        try:
            order_stats = strat.analyzers.order_stats.get_analysis()
            for field in (
                "completed_orders", "buy_orders", "sell_orders", "buy_value",
                "sell_value", "traded_value", "commission", "rejected_orders",
                "margin_orders", "canceled_orders",
            ):
                value = order_stats.get(field, 0)
                target = "commission_paid" if field == "commission" else field
                setattr(result, target, int(value) if field.endswith("orders") else float(value))
            average_equity = float(order_stats.get("average_equity", initial) or initial)
            result.turnover = result.traded_value / average_equity if average_equity > 0 else 0.0
            result.fills = list(order_stats.get("fills") or [])
        except (AttributeError, TypeError, ValueError) as e:
            self.logger.warning(f"订单统计提取失败: {e!r}")

        # 权益曲线
        try:
            timereturn = strat.analyzers.timereturn.get_analysis()
            result.equity_curve = pd.Series(timereturn)
        except Exception:
            pass

        return result

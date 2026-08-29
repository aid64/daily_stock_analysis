"""A股 Broker：佣金 + 印花税 + 过户费 + T+1 + 涨跌停强制闸

参考 backtrader 官方 CommInfoBase 和 BackBroker 扩展点。
R2（2026-08-29）：交易规则收拢到 core/rules.py 查表（"规则即数据"）：
- get_limit_pct 委托 RuleBook（补创业板 2020-08-24 前后 10%/20%、
  159915/588xxx ETF 20%、转债 2022-08-01 起无/20% 的历史口径）
- TPlus1Broker 升级为执行路径强制闸：涨跌停不可买/卖、当日买入不可卖
  （T+1 品种）在 broker 提交路径拦截拒单，不再依赖策略层自觉调用
  （此前 TPlus1Broker 是被动组件、LimitUpDownFilter 无任何策略调用，
  两条规则实际从未生效）
"""

from __future__ import annotations

import datetime
import logging
import math

import backtrader as bt
from backtrader.brokers import BackBroker
from backtrader.comminfo import CommInfoBase

from ..core import rules as _rules


class AShareCommission(CommInfoBase):
    """A股佣金 + 印花税 + 过户费

    - 佣金：双边万 1（互联网券商最低），单笔最低 5 元
    - 印花税：卖出单边（默认 0.05%，2023-08-28 减半后口径；ETF/转债经
      commission_overrides 置 0）
    - 过户费：双边 0.001%（仅沪市，简化为全市场）

    费率参数来自运行配置（引擎注入 commission_overrides）；
    品种/日期维度的规则真源在 core.rules.RuleBook。
    """

    params = (
        ("commission", 0.0001),     # 双边万 1（绝对小数，percabs=True 不转百分比）
        ("stamp_duty", 0.0005),     # 卖出单边 0.05%
        ("transfer_fee", 0.00001),  # 双边 0.001%
        ("min_commission", 5.0),    # 单笔最低 5 元（1 万本金约束）
        ("stocklike", True),
        ("commtype", CommInfoBase.COMM_PERC),  # 按金额比例
        ("percabs", True),          # commission 是绝对小数，不让 backtrader 再乘 0.01
    )

    def _getcommission(self, size, price, pseudoexec):
        """计算单笔交易佣金

        Args:
            size: 正数买入，负数卖出
            price: 成交价
            pseudoexec: True 表示预计算（不实际扣费），False 表示实际成交
        """
        amount = abs(size * price)

        # 佣金：双边万 1，最低 5 元
        comm = max(amount * self.p.commission, self.p.min_commission)

        # 印花税：仅卖出
        stamp = amount * self.p.stamp_duty if size < 0 else 0

        # 过户费：双边
        transfer = amount * self.p.transfer_fee

        return comm + stamp + transfer

    def getvaluesize(self, size, price):
        """估值防御：零仓位或非有限价按 0 计，防止 0×NaN=NaN 污染组合估值

        多标的 NaN 日期对齐（晚上市标的早期缺口）会产生 NaN 行情，
        backtrader 默认 size*price 会把整个 broker.getvalue() 变成 NaN。
        """
        if size == 0 or not math.isfinite(price):
            return 0.0
        return super().getvaluesize(size, price)

    def profitandloss(self, size, price, now):
        """浮动盈亏防御：同 getvaluesize，NaN 行情不计盈亏"""
        if size == 0 or not (math.isfinite(price) and math.isfinite(now)):
            return 0.0
        return super().profitandloss(size, price, now)


class TPlus1Broker(BackBroker):
    """T+1 + 涨跌停执行闸 Broker（R2：从被动组件升级为提交路径强制闸）

    在 transmit() 提交路径按 (品种, 日期) 查 core.rules 拒单：
    - 涨停（前一收盘涨幅 ≥ limit_pct-容差）→ 买入拒单
    - 跌停 → 卖出拒单
    - T+1 品种当日买入 → 当日卖出拒单（买入日期由 _execute 实际成交
      时自动记录；策略层 record_buy/can_sell_t1 仍可用作前置检查）
    """

    params = (
        ("t_plus_1", True),      # 全局开关（引擎由 cfg.allow_t_plus_1 注入）
        ("rules_gate", True),    # 涨跌停/T+1 强制闸（False 退回旧行为）
        ("gate_tolerance", 0.001),
    )

    def start(self):
        super().start()
        # 每个标的最近买入的成交日期：{data_name: "YYYY-MM-DD"}
        self._last_buy_date: dict[str, str] = {}
        # 涨跌停规则缓存：{symbol: (limit_pct, t_plus_1)}（按数据名）
        self._rule_cache: dict[str, tuple] = {}

    # ── 规则查询 ─────────────────────────────────

    def _rules_for(self, data) -> tuple:
        name = data._name
        if name not in self._rule_cache:
            r = _rules.get_rules(name)
            self._rule_cache[name] = (r.limit_pct, r.t_plus_1)
        return self._rule_cache[name]

    # ── 强制闸 ───────────────────────────────────

    def _gate_reject_reason(self, order) -> str | None:
        """返回拒单原因；None = 放行"""
        data = order.data
        try:
            current = float(data.close[0])
            prev = float(data.close[-1])
        except (IndexError, ValueError):
            return None
        if not (math.isfinite(current) and math.isfinite(prev) and prev > 0):
            return None

        limit_pct, t_plus_1 = self._rules_for(data)
        change = (current - prev) / prev

        if order.isbuy():
            if limit_pct is not None and change >= limit_pct - self.p.gate_tolerance:
                return f"涨停({change:.2%})不可买入"
        else:
            if limit_pct is not None and change <= -limit_pct + self.p.gate_tolerance:
                return f"跌停({change:.2%})不可卖出"
            if (
                self.p.t_plus_1
                and t_plus_1
                and self._last_buy_date.get(data._name)
                == data.datetime.date(0).isoformat()
            ):
                return "T+1 当日买入不可卖出"
        return None

    def transmit(self, order, check=True):
        if self.p.rules_gate:
            reason = self._gate_reject_reason(order)
            if reason is not None:
                order.reject(self)
                self.notify(order)
                logging.getLogger("rules_gate").debug(
                    "拒单 %s %s %s: %s",
                    "BUY" if order.isbuy() else "SELL",
                    order.data._name,
                    order.size,
                    reason,
                )
                return order
        return super().transmit(order, check=check)

    # ── T+1 买入记录（实际成交时自动记录）──────────

    def _execute(self, order, ago=None, price=None, cash=None, position=None,
                 dtcoc=None):
        # ago=None 为 pseudo 执行（check_submitted 预检）；真实成交 ago 非空
        if ago is not None and order.isbuy():
            try:
                self._last_buy_date[order.data._name] = (
                    order.data.datetime.date(0).isoformat()
                )
            except (IndexError, ValueError):
                pass
        return super()._execute(order, ago=ago, price=price, cash=cash,
                                position=position, dtcoc=dtcoc)

    # ── 策略层前置检查 API（保留兼容）──────────────

    def record_buy(self, data_name: str, date_str: str) -> None:
        """策略层手动记录买入日期（也可由 broker 实际成交自动记录）"""
        self._last_buy_date[data_name] = date_str

    def can_sell_t1(self, data_name: str, current_date_str: str) -> bool:
        """策略层在卖出前调用，检查 T+1

        Returns:
            True 表示可以卖出（买入日期 < 当前日期）
        """
        if not self.p.t_plus_1:
            return True
        last_buy = self._last_buy_date.get(data_name)
        if last_buy is None:
            return True  # 没有当日买入记录
        return last_buy < current_date_str


class LimitUpDownFilter:
    """涨跌停过滤器（策略层前置检查组件，可与 broker 强制闸叠加使用）

    涨停：涨幅 >= limit_pct - 容差 → 不能买入
    跌停：跌幅 <= -limit_pct + 容差 → 不能卖出
    """

    def __init__(self, limit_pct: float = 0.1, tolerance: float = 0.001):
        """
        Args:
            limit_pct: 涨跌停幅度（建议传 get_limit_pct(symbol, date) 的查表值）
            tolerance: 容差（避免浮点误差）
        """
        self.limit_pct = limit_pct
        self.tolerance = tolerance

    def is_limit_up(self, current_close: float, prev_close: float) -> bool:
        """是否涨停"""
        if prev_close <= 0:
            return False
        change = (current_close - prev_close) / prev_close
        return change >= self.limit_pct - self.tolerance

    def is_limit_down(self, current_close: float, prev_close: float) -> bool:
        """是否跌停"""
        if prev_close <= 0:
            return False
        change = (current_close - prev_close) / prev_close
        return change <= -self.limit_pct + self.tolerance

    def can_buy(self, current_close: float, prev_close: float) -> bool:
        """涨停不能买入"""
        return not self.is_limit_up(current_close, prev_close)

    def can_sell(self, current_close: float, prev_close: float) -> bool:
        """跌停不能卖出"""
        return not self.is_limit_down(current_close, prev_close)


def get_limit_pct(symbol: str, date: datetime.date | None = None) -> float | None:
    """查表返回涨跌停幅度（委托 core.rules；date=None 返回现行规则）"""
    return _rules.get_limit_pct(symbol, date)

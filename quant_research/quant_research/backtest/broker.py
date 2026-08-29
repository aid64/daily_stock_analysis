"""A股 Broker：佣金 + 印花税 + 过户费 + T+1 + 涨跌停过滤

参考 backtrader 官方 CommInfoBase 和 BackBroker 扩展点。
社区依据：知乎调研中 A 股回测真实性问题的工程化解决。
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import backtrader as bt
from backtrader.brokers import BackBroker
from backtrader.comminfo import CommInfoBase


class AShareCommission(CommInfoBase):
    """A股佣金 + 印花税 + 过户费

    - 佣金：双边万 1（互联网券商最低），单笔最低 5 元
    - 印花税：卖出单边 0.05%（2023 年起减半）
    - 过户费：双边 0.002%（仅沪市，简化为全市场）
    """

    params = (
        ("commission", 0.0001),     # 双边万 1（绝对小数，percabs=True 不转百分比）
        ("stamp_duty", 0.0005),     # 卖出单边 0.05%
        ("transfer_fee", 0.00002),  # 双边 0.002%
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
    """T+1 限制 Broker：买入当日不能卖出

    实现：通过策略层在 next() 中调用 check_t1_before_sell() 检查。
    Broker 本身不跟踪日期（BackBroker 内部机制复杂），T+1 逻辑委托给策略基类。
    """

    params = (
        ("t_plus_1", True),
    )

    def start(self):
        super().start()
        # 记录每个标的最近买入的日期：{data_name: last_buy_date_str}
        self._last_buy_date: dict[str, str] = {}

    def record_buy(self, data_name: str, date_str: str) -> None:
        """策略层在买入成交后调用，记录买入日期"""
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
    """涨跌停过滤器（独立组件，非 Broker 子类）

    在策略层调用 check() 方法过滤无法成交的订单。
    涨停：涨幅 >= limit_pct - 容差 → 不能买入
    跌停：跌幅 <= -limit_pct + 容差 → 不能卖出
    """

    def __init__(self, limit_pct: float = 0.1, tolerance: float = 0.001):
        """
        Args:
            limit_pct: 涨跌停幅度，主板 0.1，创业板/科创板 0.2
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


def get_limit_pct(symbol: str) -> float:
    """根据标的代码返回涨跌停幅度

    - 创业板（300xxx）：20%
    - 科创板（688xxx）：20%
    - 主板（60xxxx / 00xxxx）：10%
    - 北交所（43xxxx / 83xxxx / 87xxxx）：30%
    """
    code = symbol.split(".")[0] if "." in symbol else symbol
    if code.startswith("300") or code.startswith("301"):
        return 0.2
    if code.startswith("688") or code.startswith("689"):
        return 0.2
    if code.startswith("43") or code.startswith("83") or code.startswith("87"):
        return 0.3
    return 0.1

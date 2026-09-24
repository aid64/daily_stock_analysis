# -*- coding: utf-8 -*-
"""统一出站事件契约（v4.2 计划 P4：通知事件化）。

问题（批评#8）：告警与决策包"两套话术"——同一逻辑事件（如 000899 止损）
在盘中以告警形态、在晚间摘要以决策形态各说一遍，措辞不一致、用户被烦到。
根治方向：**发送方产"事件"，渠道消费**——出站消息统一为携带业务身份
（logical_id）、优先级、时效标记的事件，而非各处拼字符串直发。

本模块只定义**契约与判定**（不夺 NotificationService 的发送职责）：
  - NotificationEvent：logical_id / priority / route_type / symbols /
    time_sensitive / content / payload；
  - time_sensitive 判定：带 T+1 挂单时效语义的内容（开盘放弃/14:50 撤单）
    只在交易日有意义——非交易日门（P4-4）据此拦截；
  - to_send_kwargs()：映射到 send_with_results 的既有参数，供发送方渐进
    迁移（不强制一次性重写所有 sender）。

与既有 logical_id 体系（notification_digest_service / alert_worker）对齐：
  daily_operation_sheet:<date> / event_alert:<symbol>:<type>:<direction>。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:  # 模块级导入使测试可 patch notification_events.is_market_open
    from src.core.trading_calendar import is_market_open as _is_market_open
except Exception:  # noqa: BLE001 - 日历库缺失时占位，is_trading_day fail-open
    _is_market_open = None


def is_market_open(market: str, check) -> bool:
    """交易日历薄封装（可被测试 patch 的稳定符号）。"""
    if _is_market_open is None:
        raise RuntimeError("trading_calendar unavailable")
    return bool(_is_market_open(market, check))

# 优先级（与决策包/摘要分级一致：持仓相关 > 重要 > 其他）
PRIORITY_HOLDING = 10      # 与用户持仓直接相关（止损/减仓）
PRIORITY_IMPORTANT = 20    # 重要（alert / critical / system_error）
PRIORITY_NORMAL = 30       # 其他

# 路由类型（复用 notification_routing 的三类，不新增渠道语义）
ROUTE_REPORT = "report"
ROUTE_ALERT = "alert"
ROUTE_SYSTEM_ERROR = "system_error"


@dataclass
class NotificationEvent:
    """统一出站事件（发送方产事件，渠道消费）。"""
    logical_id: str                       # 业务身份（同一事件刷新替换旧摘要）
    route_type: str                       # report / alert / system_error
    content: str                          # 渲染后的消息正文
    priority: int = PRIORITY_NORMAL
    symbols: List[str] = field(default_factory=list)
    time_sensitive: bool = False          # 带 T+1 挂单时效（非交易日应拦截）
    bypass_digest: bool = False           # 绕过每日摘要即时送达（盘前决策包/告警）
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_send_kwargs(self) -> Dict[str, Any]:
        """映射到 NotificationService.send_with_results 的既有参数。"""
        return {
            "content": self.content,
            "route_type": self.route_type,
            "dedup_key": self.logical_id,
            "cooldown_key": self.logical_id,
        }


def is_trading_day(market: str = "cn", check=None) -> bool:
    """交易日判定（复用 trading_calendar，fail-open）。

    非交易日门（P4-4）的唯一判据。日历不可用时 fail-open 返回 True——
    与既有 run_daily_pipeline 的日历门同口径（不因日历库缺失而静默不发）。
    """
    from datetime import date

    try:
        return is_market_open(market, check or date.today())
    except Exception:  # noqa: BLE001 - 日历不可用 fail-open
        return True


def time_sensitive_suppressed_note(market: str = "cn") -> Optional[str]:
    """非交易日时，返回"时效挂单指令被抑制"的说明；交易日返回 None。

    用于决策包/告警渲染：非交易日不展示"开盘放弃/14:50 撤单"这类只在
    交易日有意义的挂单边界，改为一行说明"下个交易日生效"。
    """
    if is_trading_day(market):
        return None
    return ("ℹ️ 今日非交易日：以下为决策视图，挂单边界（开盘放弃/14:50 撤单）"
            "将在下个交易日盘中生效，今日无需下单。")

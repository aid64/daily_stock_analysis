# -*- coding: utf-8 -*-
"""ETF 溢价率买点监控服务（P2-3，第 2 层底仓的买入时点护栏）。

背景（2026-08-28 社区调研，详见 quant_research/HANDOFF.md 第 4 节）：
511380 转债 ETF 适合做底仓（T+0、无个券踩雷/强赎风险、费率低），但市价
相对净值存在溢价——溢价偏高时买入承受"溢价收敛 + 指数滞涨"双重风险
（2026-08 实测：近一年仅 +1.15%，市价相对净值溢价偏高）。本服务在溢价
率超过阈值时提醒暂缓买入，帮助执行"低溢价分批买入"纪律。

数据源：ak.fund_etf_spot_em()（东财 ETF 实时行情，含 最新价/IOPV实时估值）。
溢价率 = (最新价 / IOPV - 1) × 100；IOPV 缺失/非正/列缺失返回 None（不推送、
不报错）。注意盘中 IOPV 与收盘净值存在口径差异，阈值判断为近似护栏。

使用方式：作为 schedule 模式后台任务周期调用 run_once()（见 main.py 注册）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 状态保留天数（symbol + 日期 组合键：同一券同一日最多提醒一次）
STATE_RETENTION_DAYS = 7

DEFAULT_SYMBOL = "511380"
DEFAULT_MAX_PREMIUM_PCT = 1.5  # 溢价率上限（%），超过则提醒暂缓买入


def compute_premium_pct(price: Optional[float], iopv: Optional[float]) -> Optional[float]:
    """溢价率 = (价格 / IOPV - 1) × 100；任一缺失/非正返回 None。"""
    if price is None or iopv is None:
        return None
    try:
        price = float(price)
        iopv = float(iopv)
    except (TypeError, ValueError):
        return None
    if price <= 0 or iopv <= 0:
        return None
    return (price / iopv - 1.0) * 100.0


class EtfPremiumMonitorService:
    """ETF 溢价率监控：行情抓取 → 溢价计算 → 阈值判定 → 去重推送。"""

    def __init__(
        self,
        *,
        notifier: Optional[Any] = None,
        state_path: Optional[Path] = None,
        now_provider: Optional[Callable[[], date]] = None,
        symbol: str = DEFAULT_SYMBOL,
        max_premium_pct: float = DEFAULT_MAX_PREMIUM_PCT,
        premium_fetcher: Optional[Callable[[], Optional[float]]] = None,
    ) -> None:
        """初始化。

        Args:
            notifier: 通知服务（缺省时在首次推送前懒加载 NotificationService）
            state_path: 去重状态文件路径（默认 data/cache/etf_premium_state.json）
            now_provider: 当前日期提供器（测试注入用）
            symbol: 监控的 ETF 代码（默认 511380 转债 ETF）
            max_premium_pct: 溢价率上限（%），超过提醒暂缓买入
            premium_fetcher: 溢价率获取器（返回 % 或 None；缺省用 akshare
                行情计算，注入用于测试与未来接入集思录溢价数据）
        """
        self.notifier = notifier
        self.now_provider = now_provider or date.today
        self.symbol = str(symbol)
        self.max_premium_pct = float(max_premium_pct)
        self.premium_fetcher = premium_fetcher or self._default_premium_fetcher
        project_root = Path(__file__).resolve().parents[2]
        self.state_path = Path(state_path) if state_path else (
            project_root / "data" / "cache" / "etf_premium_state.json"
        )
        self._notified: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------
    # 状态持久化（同一券同一天最多提醒一次；溢价回落次日可再提醒）
    # ------------------------------------------------------------------

    def _load_state(self) -> Dict[str, str]:
        if self._notified is not None:
            return self._notified
        state: Dict[str, str] = {}
        try:
            if self.state_path.exists():
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("notified"), dict):
                    state = {str(k): str(v) for k, v in raw["notified"].items()}
        except (OSError, ValueError) as exc:
            logger.warning("[EtfPremiumMonitor] 状态文件读取失败（按空状态处理）: %s", exc)
        self._notified = state
        return state

    def _save_state(self) -> None:
        if self._notified is None:
            return
        cutoff = (self.now_provider() - timedelta(days=STATE_RETENTION_DAYS)).isoformat()
        pruned = {k: v for k, v in self._notified.items() if v >= cutoff}
        self._notified = pruned
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"notified": pruned}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("[EtfPremiumMonitor] 状态文件写入失败（不影响本轮推送）: %s", exc)

    # ------------------------------------------------------------------
    # 行情获取与溢价计算
    # ------------------------------------------------------------------

    def _default_premium_fetcher(self) -> Optional[float]:
        """akshare 东财 ETF 实时行情 → 溢价率（%）；任何失败返回 None。"""
        try:
            import akshare as ak

            df = ak.fund_etf_spot_em()
        except Exception as exc:  # noqa: BLE001 - 网络失败不应拖垮后台任务
            logger.warning("[EtfPremiumMonitor] ETF 行情拉取失败: %s", exc)
            return None
        if df is None or len(df) == 0:
            return None
        price_col, iopv_col, code_col = "最新价", "IOPV实时估值", "代码"
        if not {price_col, iopv_col, code_col}.issubset(df.columns):
            logger.warning(
                "[EtfPremiumMonitor] 行情表缺列（存在: %s）", df.columns.tolist()
            )
            return None
        rows = df[df[code_col].astype(str) == self.symbol]
        if len(rows) == 0:
            logger.info("[EtfPremiumMonitor] 行情表中未找到 %s", self.symbol)
            return None
        try:
            price = float(rows[price_col].iloc[0])
            iopv = float(rows[iopv_col].iloc[0])
        except (TypeError, ValueError) as exc:
            logger.warning("[EtfPremiumMonitor] %s 价格/IOPV 解析失败: %s", self.symbol, exc)
            return None
        return compute_premium_pct(price, iopv)

    # ------------------------------------------------------------------
    # 消息构建
    # ------------------------------------------------------------------

    @staticmethod
    def _build_message(symbol: str, premium_pct: float, threshold: float) -> str:
        from src.notification import NotificationBuilder

        content = (
            f"**{symbol} 当前溢价率 {premium_pct:+.2f}%**，已超过暂缓买入线 {threshold:.2f}%。\n\n"
            "📉 溢价偏高时买入承受\"溢价收敛 + 指数滞涨\"双重风险，建议：\n"
            "- 暂缓新买入，等待溢价回落至阈值以下再分批建仓\n"
            "- 已有持仓无需恐慌卖出（底仓属性，溢价收敛是买入时点问题）\n"
            "- 溢价参考盘中 IOPV，与收盘净值存在口径差异，以集思录/基金公司数据复核"
        )
        return NotificationBuilder.build_simple_alert(
            title=f"ETF 溢价监控 | {symbol} 溢价 {premium_pct:+.2f}% 超阈值",
            content=content,
            alert_type="warning",
        )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run_once(self) -> Dict[str, Any]:
        """执行一轮检查。返回统计 dict（不抛异常，供后台任务安全调用）。"""
        stats: Dict[str, Any] = {
            "symbol": self.symbol,
            "premium_pct": None,
            "threshold": self.max_premium_pct,
            "notified": 0,
            "suppressed": 0,
        }
        try:
            premium = self.premium_fetcher()
        except Exception as exc:  # noqa: BLE001 - 双保险：获取器内部已兜底
            logger.warning("[EtfPremiumMonitor] 溢价获取异常: %s", exc)
            return stats
        if premium is None:
            return stats
        premium = round(float(premium), 4)
        stats["premium_pct"] = premium
        if premium <= self.max_premium_pct:
            logger.debug(
                "[EtfPremiumMonitor] %s 溢价 %.2f%% ≤ %.2f%%，无需提醒",
                self.symbol, premium, self.max_premium_pct,
            )
            return stats

        today = self.now_provider()
        key = f"{self.symbol}:{today.isoformat()}"
        state = self._load_state()
        if key in state:
            stats["suppressed"] = 1
            return stats

        message = self._build_message(self.symbol, premium, self.max_premium_pct)
        dispatched = self._send_notification(message)
        if dispatched:
            state[key] = today.isoformat()
            self._save_state()
            stats["notified"] = 1
        return stats

    def _send_notification(self, message: str) -> bool:
        """推送通知；任何异常只记录日志并返回 False（不拖垮任务）。"""
        try:
            from src.notification import NotificationService

            notification_service = self.notifier or NotificationService()
            result = notification_service.send_with_results(
                message,
                route_type="alert",
                severity="warning",
                dedup_key=f"etf_premium:{self.symbol}",
                cooldown_key="etf_premium",
            )
            return bool(getattr(result, "success", False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EtfPremiumMonitor] 通知发送失败: %s", exc)
            return False

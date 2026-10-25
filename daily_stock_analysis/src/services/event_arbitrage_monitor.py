# -*- coding: utf-8 -*-
"""事件套利监控服务：扫描全市场公告中的要约收购/现金选择权/私有化/主动退市事件。

背景（2026-08 广域调研定稿，详见 quant_research/HANDOFF.md 第 4 节）：
此类事件 2025 年 A 股发生 9 起，实盘参与者单笔收益 2.5%-18%（玉龙股份/
中航产融/ST新潮/德邦等）。价差由要约价/现金选择权价格合约锁定，属于
"确定性价差"而非预测性策略，因此不需要回测验证，适合小资金（容量小、
机构无法参与）。

数据源（2026-08-18 实测验证）：
- ak.stock_notice_report(symbol="全部", date=YYYYMMDD)：东财全市场日公告流
  （约 500-1300 条/日，含 代码/名称/公告标题/公告类型/公告日期/网址），
  关键词过滤实测在 2026-08-12 命中 ST龙元(600491) 要约收购 3 条公告。

使用方式：作为 schedule 模式后台任务周期调用 run_once()（见 main.py 注册）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 事件关键词：标题命中即视为疑似事件
EVENT_KEYWORDS: tuple[str, ...] = (
    "要约收购",
    "现金选择权",
    "私有化",
    "主动退市",
    "终止上市",
    "吸收合并",  # 私有化/合并退市的标准公告措辞（如南航、ST平能案例）
)
# 噪声排除：命中关键词但含这些模式的标题为风险提示类噪声（如 *ST 股
# "可能因股价低于 1 元而终止上市的风险提示"），不是套利事件。
# "配售"：抢权配售（正股优先配售转债/增发）属另一类事件，且存在正股
# 下跌风险（社区教训：龙建股份抢权正股 -9%），不归入套利信号（P2-1）。
NOISE_PATTERNS: tuple[str, ...] = (
    "风险提示",
    "终止上市风险",
    "吸收合并全资子公司",  # 母公司内部重组（无套利意义），区别于换股吸收合并
    "配售",
)

# 状态文件保留天数（超过该天数的 seen 记录会被清理）
STATE_RETENTION_DAYS = 7

# 巴菲特套利四问（1988 致股东信），推送时附带提醒用户自查
BUFFETT_FOUR_QUESTIONS = (
    "1. 事件完成的确定性有多大？（收购方实力 / 审批进度 / 股东大会通过概率）",
    "2. 资金会被占用多久？（申报期 + 资金到账，通常 1-3 个月）",
    "3. 有无更优结果可能？（竞价收购 / 换股价值高于现金选择权）",
    "4. 若事件失败，后果如何？（预估跌幅 vs 当前价差，风险收益比）",
)

RISK_NOTES = (
    "⚠️ 风险提示：仅全面要约可 100% 按要约价卖出；部分要约有配售比例风险；"
    "议案被否可能下跌 20%+。单事件仓位建议 ≤ 总资金 10%，请阅读公告原文后再决策。"
)

# 事件驱动检查清单（P2-1，来自 2026-08 社区实盘教训：大智慧换股合并
# 中止审核/立案、广汇部分要约 75% 比例、龙建股份抢权正股 -9% 等）
EVENT_CHECKLIST = (
    "🔎 事件检查清单：",
    "1. 前置生效条件是否明确？（审批/反垄断/股东大会，任一未过即失败）",
    "2. 是否存在审核中止/立案调查风险？（时间成本会吞掉年化收益）",
    "3. 部分要约：锁定收益 = 要约比例 × 要约价，剩余仓位暴露市场波动",
    "4. 时间成本：申报期 + 资金到账通常 1-3 个月，价差需按年化折算",
)

# 格雷厄姆套利年化公式（P2-1）：C=成功概率，G=成功收益，L=失败损失，
# Y=占用年数，P=现价。推送模板提供公式骨架，参数由用户按公告自查填入。
GRAHAM_FORMULA = (
    "📐 格雷厄姆年化收益 = [C×G − (1−C)×L] / (Y×P)"
    "（C=成功概率，G=成功收益，L=失败损失，Y=占用年数，P=现价）"
)


def extract_offer_price(title: str) -> Optional[float]:
    """从公告标题提取要约价格（元/股，best-effort）。

    匹配 "X元/股" 与 "要约价格为X元" 两类措辞；提取失败返回 None
    （推送降级为无价差信息，不影响原有行为）。
    """
    if not title:
        return None
    import re

    for pattern in (
        r"(\d+(?:\.\d+)?)\s*元/股",
        r"要约价格[为约]?\s*(\d+(?:\.\d+)?)\s*元",
        r"价格[为]\s*(\d+(?:\.\d+)?)\s*元",
    ):
        match = re.search(pattern, title)
        if match:
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if value > 0:
                return value
    return None


def classify_signal(
    offer_price: Optional[float], current_price: Optional[float]
) -> tuple[str, Optional[float]]:
    """按要约价 vs 现价分类信号（P2-1 假套利排除）。

    Returns:
        (signal_class, spread_pct)：
        - ("arbitrage", +X)：要约价 ≥ 现价，存在锁定价差（%）
        - ("speculative", -X)：要约价 < 现价，市价已高于要约价
          （亚振家居/艾艾精工型纯投机盘，无套利意义）
        - ("unknown", None)：要约价或现价缺失（无法判定，不降级）
    """
    if offer_price is None or current_price is None or current_price <= 0:
        return ("unknown", None)
    spread_pct = (offer_price / current_price - 1.0) * 100.0
    if offer_price >= current_price:
        return ("arbitrage", spread_pct)
    return ("speculative", spread_pct)


class _EventItem:
    """单条疑似事件公告（内部结构）。"""

    __slots__ = (
        "code", "name", "title", "notice_type", "notice_date", "url", "keyword",
        "offer_price", "current_price", "signal_class", "spread_pct",
    )

    def __init__(
        self,
        code: str,
        name: str,
        title: str,
        notice_type: str,
        notice_date: str,
        url: str,
        keyword: str,
    ) -> None:
        self.code = code
        self.name = name
        self.title = title
        self.notice_type = notice_type
        self.notice_date = notice_date
        self.url = url
        self.keyword = keyword
        # P2-1 富化字段（run_once 中填充；None = 不可得，推送降级）
        self.offer_price: Optional[float] = None
        self.current_price: Optional[float] = None
        self.signal_class: Optional[str] = None
        self.spread_pct: Optional[float] = None

    @property
    def fingerprint(self) -> str:
        """去重指纹：URL 哈希（同一条公告跨日重复推送时 URL 稳定）。"""
        digest = hashlib.md5(self.url.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"{self.code}:{digest}"


class EventArbitrageMonitorService:
    """事件套利监控：公告扫描 → 关键词过滤 → 去重 → 推送。"""

    def __init__(
        self,
        *,
        notifier: Optional[Any] = None,
        state_path: Optional[Path] = None,
        now_provider: Optional[Callable[[], date]] = None,
        lookback_days: int = 1,
        price_fetcher: Optional[Callable[[str], Optional[float]]] = None,
    ) -> None:
        """初始化。

        Args:
            notifier: 通知服务（缺省时在首次推送前懒加载 NotificationService）
            state_path: 去重状态文件路径（默认 data/cache/event_arbitrage_state.json）
            now_provider: 当前日期提供器（测试注入用）
            lookback_days: 向前扫描天数（默认 1 = 今天 + 昨天，重启不漏）
            price_fetcher: 现价获取器（P2-1，参数为股票代码，返回最新价或
                None；缺省用 akshare 单券询价接口，失败降级为 None）
        """
        self.notifier = notifier
        self.now_provider = now_provider or date.today
        self.lookback_days = max(0, int(lookback_days))
        self.price_fetcher = price_fetcher or self._default_price_fetcher
        project_root = Path(__file__).resolve().parents[2]
        self.state_path = Path(state_path) if state_path else (
            project_root / "data" / "cache" / "event_arbitrage_state.json"
        )
        self._seen: Optional[Dict[str, str]] = None  # fingerprint -> notice_date

    @staticmethod
    def _default_price_fetcher(code: str) -> Optional[float]:
        """akshare 单券询价（stock_bid_ask_em）；任何失败返回 None（best-effort）。"""
        try:
            import akshare as ak

            df = ak.stock_bid_ask_em(symbol=code)
            if df is None or len(df) == 0:
                return None
            pair = df[df["item"] == "最新"]
            if len(pair) == 0:
                return None
            value = float(pair["value"].iloc[0])
            return value if value > 0 else None
        except Exception as exc:  # noqa: BLE001 - 现价获取失败不应影响推送
            logger.debug("[EventArbitrageMonitor] %s 现价获取失败: %s", code, exc)
            return None

    # ------------------------------------------------------------------
    # 状态持久化
    # ------------------------------------------------------------------

    def _load_state(self) -> Dict[str, str]:
        if self._seen is not None:
            return self._seen
        seen: Dict[str, str] = {}
        try:
            if self.state_path.exists():
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("seen"), dict):
                    seen = {str(k): str(v) for k, v in raw["seen"].items()}
        except (OSError, ValueError) as exc:
            logger.warning("[EventArbitrageMonitor] 状态文件读取失败（按空状态处理）: %s", exc)
        self._seen = seen
        return seen

    def _save_state(self) -> None:
        if self._seen is None:
            return
        # 清理过期记录，防止文件无限增长
        today = self.now_provider()
        cutoff = (today - timedelta(days=STATE_RETENTION_DAYS)).isoformat()
        pruned = {k: v for k, v in self._seen.items() if v >= cutoff}
        self._seen = pruned
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"seen": pruned}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("[EventArbitrageMonitor] 状态文件写入失败（不影响本轮推送）: %s", exc)

    # ------------------------------------------------------------------
    # 公告获取与过滤
    # ------------------------------------------------------------------

    def _fetch_announcements(self, date_str: str) -> List[Dict[str, Any]]:
        """拉取指定日期的全市场公告（东财接口）。失败返回空表（不抛异常）。"""
        import akshare as ak

        try:
            df = ak.stock_notice_report(symbol="全部", date=date_str)
        except Exception as exc:  # noqa: BLE001 - 网络失败不应拖垮后台任务
            logger.warning(
                "[EventArbitrageMonitor] %s 公告拉取失败: %s", date_str, exc
            )
            return []
        if df is None or len(df) == 0:
            return []
        records: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            records.append(
                {
                    "代码": str(row.get("代码", "") or ""),
                    "名称": str(row.get("名称", "") or ""),
                    "公告标题": str(row.get("公告标题", "") or ""),
                    "公告类型": str(row.get("公告类型", "") or ""),
                    "公告日期": str(row.get("公告日期", "") or ""),
                    "网址": str(row.get("网址", "") or ""),
                }
            )
        return records

    @staticmethod
    def _is_noise(title: str) -> bool:
        return any(pattern in title for pattern in NOISE_PATTERNS)

    @classmethod
    def _match_keyword(cls, title: str) -> Optional[str]:
        """返回命中的第一个事件关键词；噪声标题返回 None。"""
        if cls._is_noise(title):
            return None
        for keyword in EVENT_KEYWORDS:
            if keyword in title:
                return keyword
        return None

    def _collect_events(self, scan_dates: List[str]) -> List[_EventItem]:
        events: List[_EventItem] = []
        for date_str in scan_dates:
            for record in self._fetch_announcements(date_str):
                title = record["公告标题"]
                keyword = self._match_keyword(title)
                if keyword is None:
                    continue
                if not record["代码"] or not record["网址"]:
                    continue
                events.append(
                    _EventItem(
                        code=record["代码"],
                        name=record["名称"],
                        title=title,
                        notice_type=record["公告类型"],
                        notice_date=record["公告日期"] or date_str,
                        url=record["网址"],
                        keyword=keyword,
                    )
                )
        return events

    # ------------------------------------------------------------------
    # 消息构建
    # ------------------------------------------------------------------

    @staticmethod
    def _build_message(events: List[_EventItem]) -> str:
        from src.notification import NotificationBuilder

        lines: List[str] = []
        for item in events[:10]:  # 单条消息最多 10 条，防超长
            lines.append(f"**{item.name} ({item.code})** — 命中 [{item.keyword}]")
            lines.append(f"- 公告：{item.title}")
            if item.signal_class == "arbitrage":
                lines.append(
                    f"- 💰 要约价 {item.offer_price:.2f} vs 现价 {item.current_price:.2f}"
                    f"（价差 +{item.spread_pct:.2f}%，部分要约按比例锁定）"
                )
            elif item.signal_class == "speculative":
                lines.append(
                    f"- ⚠️ 市价 {item.current_price:.2f} 已高于要约价 {item.offer_price:.2f}"
                    f"（价差 {item.spread_pct:.2f}%）——投机盘，非套利，不建议参与"
                )
            lines.append(f"- 链接：{item.url}")
            lines.append("")
        if len(events) > 10:
            lines.append(f"…另有 {len(events) - 10} 条，详见东财公告页")
            lines.append("")

        content = "\n".join(lines)
        content += "📋 **参与前四问核查（巴菲特套利框架）**\n" + "\n".join(
            BUFFETT_FOUR_QUESTIONS
        )
        content += "\n\n" + "\n".join(EVENT_CHECKLIST)
        content += "\n" + GRAHAM_FORMULA
        content += "\n\n" + RISK_NOTES
        return NotificationBuilder.build_simple_alert(
            title=f"事件套利监控 | 发现 {len(events)} 条疑似机会",
            content=content,
            alert_type="warning",
        )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run_once(self) -> Dict[str, int]:
        """执行一轮扫描。返回统计 dict（不抛异常，供后台任务安全调用）。"""
        today = self.now_provider()
        scan_dates = [
            (today - timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(self.lookback_days, -1, -1)
        ]
        stats = {"scanned_dates": len(scan_dates), "events": 0, "notified": 0, "suppressed": 0}

        try:
            events = self._collect_events(scan_dates)
        except Exception as exc:  # noqa: BLE001 - 双保险：采集内部已兜底
            logger.warning("[EventArbitrageMonitor] 事件采集异常: %s", exc)
            return stats
        # 批内去重：同一公告跨扫描日重复返回时只保留一条（同 URL 同代码）
        unique_events: Dict[str, _EventItem] = {}
        for item in events:
            unique_events.setdefault(item.fingerprint, item)
        events = list(unique_events.values())
        stats["events"] = len(events)
        if not events:
            return stats

        seen = self._load_state()
        fresh: List[_EventItem] = []
        for item in events:
            notice_date = item.notice_date.replace("-", "")
            if item.fingerprint in seen and seen[item.fingerprint] == notice_date:
                stats["suppressed"] += 1
                continue
            fresh.append(item)

        if not fresh:
            return stats

        # P2-1 富化：提取要约价 + 获取现价 → 价差分类（假套利排除）。
        # 价格获取失败仅降级为 unknown，不影响推送。
        for item in fresh:
            item.offer_price = extract_offer_price(item.title)
            if item.offer_price is not None:
                try:
                    item.current_price = self.price_fetcher(item.code)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[EventArbitrageMonitor] %s 现价获取异常: %s", item.code, exc)
                    item.current_price = None
            item.signal_class, item.spread_pct = classify_signal(
                item.offer_price, item.current_price
            )

        message = self._build_message(fresh)
        dispatched = self._send_notification(message, fresh)
        if dispatched:
            for item in fresh:
                seen[item.fingerprint] = item.notice_date.replace("-", "")
            self._save_state()
            stats["notified"] = len(fresh)
        return stats

    def _send_notification(self, message: str, events: List[_EventItem]) -> bool:
        """推送通知；任何异常只记录日志并返回 False（不拖垮任务）。"""
        try:
            from src.notification import NotificationService

            notification_service = self.notifier or NotificationService()
            result = notification_service.send_with_results(
                message,
                route_type="alert",
                severity="warning",
                dedup_key="event_arbitrage:daily",
                cooldown_key="event_arbitrage",
            )
            return bool(getattr(result, "success", False))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[EventArbitrageMonitor] 通知发送失败（%d 条事件未标记已读）: %s",
                len(events),
                exc,
            )
            return False

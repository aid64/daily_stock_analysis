# -*- coding: utf-8 -*-
"""转债打新日历服务：申购 / 缴款 / 上市 三段式提醒。

背景（2026-08 广域调研定稿，详见 quant_research/HANDOFF.md 第 4 节）：
2026 年新债上市首日平均涨幅 52.95%（45 只中 32 只顶格 57.3%），信用申购
零成本参与，单账户年期望 600-2000 元（户均中 2-4 签）。属于规则性正期望，
无需回测验证。

数据源（2026-08-18 实测验证）：
- ak.bond_zh_cov()：全量发行表（约 1050 只），含 申购日期 / 上市时间 列；
  实测 7-12 月申购窗口 21 只，含未来待申购新债。

三段式规则：
- subscribe（申购）：申购日期 ∈ [今天, 今天+1] → 顶格申购提醒
- payment（缴款）：申购日期 ∈ [今天-2, 今天-1] → T+2 缴款截止提醒
  （T 日申购，T+1 晚公布中签，T+2 16:00 前须缴款；12 个月内 3 次弃购
  将被禁打新半年）
- listing（上市）：上市时间 ∈ [今天, 今天+1] → 上市首日卖出提醒
  （社区共识：上市首日 14:50 前卖出落袋，不博弈后续波动）

使用方式：作为 schedule 模式后台任务周期调用 run_once()（见 main.py 注册）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# 各阶段提醒的日期偏移窗口（含端点，单位：日历日）
SUBSCRIBE_WINDOW_DAYS = 1  # [今天, 今天+1]
PAYMENT_WINDOW_DAYS = 2  # 申购日期 ∈ [今天-2, 今天-1]
LISTING_WINDOW_DAYS = 1  # 上市时间 ∈ [今天, 今天+1]

# 状态保留天数（债券代码 + 阶段 + 日期 组合键，过期清理）
STATE_RETENTION_DAYS = 45

STAGE_TIPS: Dict[str, str] = {
    "rights": "📌 配债登记提醒（今日买入/持有正股可获优先配债；抢权需承担正股敞口风险，安全垫以公告为准）",
    "subscribe": "🎯 今日/明日申购（9:25-15:00 券商 APP 顶格申购，无需预缴资金）",
    "payment": "💰 缴款提醒（中签后 T+2 16:00 前账户留足 1000 元/签，弃购 3 次禁打新半年）",
    "listing": "📈 上市卖出提醒（建议上市首日 14:50 前卖出落袋）",
}


class _BondReminder:
    """单条打新提醒（内部结构）。"""

    __slots__ = ("bond_code", "bond_name", "stage", "reference_date", "note")

    def __init__(
        self,
        bond_code: str,
        bond_name: str,
        stage: str,
        reference_date: str,
        note: str,
    ) -> None:
        self.bond_code = bond_code
        self.bond_name = bond_name
        self.stage = stage
        self.reference_date = reference_date
        self.note = note

    @property
    def dedup_key(self) -> str:
        """同一券同一阶段同一天最多提醒一次。"""
        return f"{self.bond_code}:{self.stage}:{date.today().isoformat()}"


class BondNewIssueCalendarService:
    """转债打新日历：发行表扫描 → 三段判定 → 去重 → 推送。"""

    def __init__(
        self,
        *,
        notifier: Optional[Any] = None,
        state_path: Optional[Path] = None,
        now_provider: Optional[Callable[[], date]] = None,
    ) -> None:
        self.notifier = notifier
        self.now_provider = now_provider or date.today
        project_root = Path(__file__).resolve().parents[2]
        self.state_path = Path(state_path) if state_path else (
            project_root / "data" / "cache" / "bond_new_issue_state.json"
        )
        self._notified: Optional[Dict[str, str]] = None  # dedup_key -> iso date

    # ------------------------------------------------------------------
    # 状态持久化
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
            logger.warning("[BondNewIssueCalendar] 状态文件读取失败（按空状态处理）: %s", exc)
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
            logger.warning("[BondNewIssueCalendar] 状态文件写入失败（不影响本轮推送）: %s", exc)

    # ------------------------------------------------------------------
    # 数据获取与阶段判定
    # ------------------------------------------------------------------

    def _fetch_calendar(self) -> List[Dict[str, Any]]:
        """拉取全量转债发行表（akshare 主源，tushare 备源）。失败返回空表（不抛异常）。"""
        import akshare as ak

        try:
            df = ak.bond_zh_cov()
        except Exception as exc:  # noqa: BLE001 - 网络失败不应拖垮后台任务
            logger.warning("[BondNewIssueCalendar] akshare 发行表拉取失败: %s", exc)
            df = None
        if df is not None and len(df) > 0:
            required = {"债券代码", "债券简称", "申购日期"}
            if required.issubset(df.columns):
                return df.to_dict(orient="records")
            logger.warning(
                "[BondNewIssueCalendar] akshare 发行表缺少必要列（存在: %s），尝试 tushare 备源",
                df.columns.tolist(),
            )
        # tushare 备源：cb_issue（onl_date 申购日）+ cb_basic（上市日/简称）
        return self._fetch_calendar_tushare()

    def _fetch_calendar_tushare(self) -> List[Dict[str, Any]]:
        """tushare 备源：cb_issue 网上发行日期(≈申购日) + cb_basic 上市日期。

        仅在 akshare 主源失败时调用；未配置 TUSHARE_TOKEN 或接口失败返回空表。
        2026-08-18 实测：cb_basic 1162 只（含 bond_short_name/list_date），
        cb_issue 1135 只（含 onl_date），两表 ts_code join 覆盖全部在市+近年退市券。
        """
        try:
            from src.config import get_config
            from data_provider.tushare_fetcher import _TushareHttpClient

            token = (getattr(get_config(), "tushare_token", None) or "").strip()
            if not token:
                logger.info("[BondNewIssueCalendar] 未配置 TUSHARE_TOKEN，tushare 备源不可用")
                return []
            client = _TushareHttpClient(token=token)
            issue_df = client.query(
                "cb_issue", fields="ts_code,onl_date"
            )
            basic_df = client.query(
                "cb_basic", fields="ts_code,bond_short_name,list_date"
            )
        except Exception as exc:  # noqa: BLE001 - 备源失败不应拖垮后台任务
            logger.warning("[BondNewIssueCalendar] tushare 备源拉取失败: %s", exc)
            return []

        if issue_df is None or basic_df is None or len(basic_df) == 0:
            return []
        basic = basic_df.set_index("ts_code")

        def _clean(value: Any) -> Any:
            """NaN/NaT 归一为 None，日期保持 tushare 原生 YYYYMMDD 字符串。"""
            return None if value is None or pd.isna(value) else value

        records: List[Dict[str, Any]] = []
        for _, row in issue_df.iterrows():
            ts_code = str(row.get("ts_code", "") or "")
            if not ts_code or ts_code not in basic.index:
                continue
            meta = basic.loc[ts_code]
            records.append(
                {
                    # "123283.SZ" → "123283"，与 akshare 债券代码口径一致
                    "债券代码": ts_code.split(".")[0],
                    "债券简称": str(meta.get("bond_short_name", "") or ""),
                    "申购日期": _clean(row.get("onl_date")),
                    "上市时间": _clean(meta.get("list_date")),
                }
            )
        logger.info("[BondNewIssueCalendar] tushare 备源可用：%d 条发行记录", len(records))
        return records

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        """解析日期值；空/NaT/无法解析返回 None。"""
        if value is None:
            return None
        try:
            ts = pd.Timestamp(value)
        except (ValueError, TypeError):
            return None
        if pd.isna(ts):
            return None
        return ts.date()

    def _classify_all(
        self, record: Dict[str, Any], today: date
    ) -> List[Tuple[str, str, str]]:
        """判定单条记录命中的全部提醒阶段（P2-2：新增配债登记阶段）。

        阶段语义：
        - rights（配债登记）：申购日 == 明天 → 今日为股权登记日（按申购日
          前一交易日近似，节假日顺延以公告为准）。持有人可参与优先配债；
          抢权（仅为配债买入正股）存在正股下跌风险，社区教训：龙建股份
          抢权配售正股 -9%。
        - subscribe（申购）：申购日 ∈ [今天, 明天]
        - payment（缴款）：申购日 ∈ [今天-2, 今天-1]（T+2 缴款窗口）
        - listing（上市）：上市日 ∈ [今天, 明天]

        Returns:
            [(stage, bond_code, bond_name), ...]，按时间顺序；无命中返回空表
        """
        bond_code = str(record.get("债券代码", "") or "").strip()
        bond_name = str(record.get("债券简称", "") or "").strip()
        if not bond_code:
            return []

        subscribe_date = self._parse_date(record.get("申购日期"))
        listing_date = self._parse_date(record.get("上市时间"))
        stages: List[Tuple[str, str, str]] = []

        # 配债登记：申购日 == 明天（登记日 ≈ 申购日前一交易日）
        if subscribe_date is not None and subscribe_date == today + timedelta(days=1):
            stages.append(("rights", bond_code, bond_name))

        # 申购阶段：[今天, 今天+1]
        if subscribe_date is not None and today <= subscribe_date <= today + timedelta(
            days=SUBSCRIBE_WINDOW_DAYS
        ):
            stages.append(("subscribe", bond_code, bond_name))

        # 缴款阶段：申购日期 ∈ [今天-2, 今天-1]（T+2 缴款窗口）
        if subscribe_date is not None and today - timedelta(
            days=PAYMENT_WINDOW_DAYS
        ) <= subscribe_date <= today - timedelta(days=1):
            stages.append(("payment", bond_code, bond_name))

        # 上市阶段：[今天, 今天+1]
        if listing_date is not None and today <= listing_date <= today + timedelta(
            days=LISTING_WINDOW_DAYS
        ):
            stages.append(("listing", bond_code, bond_name))

        return stages

    def _classify(
        self, record: Dict[str, Any], today: date
    ) -> Optional[Tuple[str, str, str]]:
        """判定单条记录命中的提醒阶段（返回首个命中；兼容旧语义）。

        优先级与既有行为一致：subscribe > payment > listing（rights 仅在
        _classify_all 中参与，不改变单阶段兼容口径）。

        Returns:
            (stage, bond_code, bond_name) 或 None
        """
        priority = {"subscribe": 0, "payment": 1, "listing": 2}
        stages = [
            s for s in self._classify_all(record, today) if s[0] in priority
        ]
        if not stages:
            return None
        stages.sort(key=lambda s: priority.get(s[0], 9))
        return stages[0]

    def _collect_reminders(
        self, today: date, records: Optional[List[Dict[str, Any]]] = None
    ) -> List[_BondReminder]:
        reminders: List[_BondReminder] = []
        for record in (records if records is not None else self._fetch_calendar()):
            for stage, bond_code, bond_name in self._classify_all(record, today):
                reference = (
                    self._parse_date(record.get("上市时间" if stage == "listing" else "申购日期"))
                )
                reference_str = reference.isoformat() if reference else "未知日期"
                note = (
                    f"今日为股权登记日（申购日 {reference_str}），持有人可优先配债；"
                    f"抢权需承担正股敞口风险，以公告为准"
                    if stage == "rights"
                    else f"今日上市，建议 14:50 前卖出"
                    if stage == "listing" and reference == today
                    else f"明日上市，明日 14:50 前卖出" if stage == "listing"
                    else f"申购日 {reference_str}"
                    if stage == "subscribe"
                    else f"申购日 {reference_str}，留意中签缴款"
                )
                reminders.append(
                    _BondReminder(bond_code, bond_name, stage, reference_str, note)
                )
        # 配债登记最早（申购前一日）、申购、缴款、上市，同类内按代码排序
        stage_order = {"rights": 0, "subscribe": 1, "payment": 2, "listing": 3}
        reminders.sort(key=lambda r: (stage_order.get(r.stage, 9), r.bond_code))
        return reminders

    # ------------------------------------------------------------------
    # 只读查询（供策略引擎 / 决策包盘前汇总，不推送、不写状态）
    # ------------------------------------------------------------------

    def upcoming_reminders(self, days: int = 2) -> List[Dict[str, Any]]:
        """未来 days 日内的打新提醒（去重后按阶段+代码排序）。

        与 run_once 推送链路互不影响：本方法只读。days 夹在 [1, 7]。
        发行表只拉取一次，多日分类复用同一份 records。
        """
        from datetime import timedelta

        today = self.now_provider()
        span = max(1, min(int(days), 7))
        try:
            records = self._fetch_calendar()
        except Exception as exc:  # noqa: BLE001 - 查询失败返回空（调用方降级）
            logger.warning("[BondNewIssueCalendar] 汇总查询拉取失败: %s", exc)
            return []

        seen: Dict[Tuple[str, str], _BondReminder] = {}
        for offset in range(span):
            day = today + timedelta(days=offset)
            for reminder in self._collect_reminders(day, records=records):
                seen.setdefault((reminder.bond_code, reminder.stage), reminder)

        return [
            {
                "bond_code": r.bond_code,
                "bond_name": r.bond_name,
                "stage": r.stage,
                "reference_date": r.reference_date,
                "note": r.note,
            }
            for r in sorted(
                seen.values(),
                key=lambda x: (
                    {"rights": 0, "subscribe": 1, "payment": 2, "listing": 3}.get(
                        x.stage, 9),
                    x.bond_code,
                ),
            )
        ]

    # ------------------------------------------------------------------
    # 消息构建
    # ------------------------------------------------------------------

    @staticmethod
    def _build_message(reminders: List[_BondReminder]) -> str:
        from src.notification import NotificationBuilder

        sections: Dict[str, List[str]] = {"rights": [], "subscribe": [], "payment": [], "listing": []}
        for reminder in reminders:
            sections.setdefault(reminder.stage, []).append(
                f"- **{reminder.bond_name} ({reminder.bond_code})** — {reminder.note}"
            )

        parts: List[str] = []
        for stage in ("rights", "subscribe", "payment", "listing"):
            lines = sections.get(stage) or []
            if not lines:
                continue
            parts.append(f"{STAGE_TIPS[stage]}：")
            parts.extend(lines)
            parts.append("")

        content = "\n".join(parts)
        content += "💡 顶格申购无需预缴资金，中签才缴款；上市首日卖出为社区共识默认策略。"
        content += "\n⚠️ 抢权配售 ≠ 无风险套利：正股敞口可能吞掉配债收益（社区实测教训），以公告安全垫为准。"
        return NotificationBuilder.build_simple_alert(
            title="转债打新日历提醒",
            content=content,
            alert_type="info",
        )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run_once(self) -> Dict[str, int]:
        """执行一轮扫描。返回统计 dict（不抛异常，供后台任务安全调用）。"""
        today = self.now_provider()
        stats = {"reminders": 0, "notified": 0, "suppressed": 0}

        try:
            reminders = self._collect_reminders(today)
        except Exception as exc:  # noqa: BLE001 - 双保险
            logger.warning("[BondNewIssueCalendar] 提醒采集异常: %s", exc)
            return stats
        stats["reminders"] = len(reminders)
        if not reminders:
            return stats

        notified_state = self._load_state()
        fresh: List[_BondReminder] = []
        for reminder in reminders:
            key = reminder.dedup_key
            if key in notified_state:
                stats["suppressed"] += 1
                continue
            fresh.append(reminder)

        if not fresh:
            return stats

        message = self._build_message(fresh)
        dispatched = self._send_notification(message, fresh)
        if dispatched:
            today_str = today.isoformat()
            for reminder in fresh:
                notified_state[reminder.dedup_key] = today_str
            self._save_state()
            stats["notified"] = len(fresh)
        return stats

    def _send_notification(self, message: str, reminders: List[_BondReminder]) -> bool:
        try:
            from src.notification import NotificationService

            notification_service = self.notifier or NotificationService()
            result = notification_service.send_with_results(
                message,
                route_type="alert",
                severity="info",
                dedup_key="bond_new_issue:daily",
                cooldown_key="bond_new_issue",
            )
            return bool(getattr(result, "success", False))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[BondNewIssueCalendar] 通知发送失败（%d 条提醒未标记已读）: %s",
                len(reminders),
                exc,
            )
            return False

# -*- coding: utf-8 -*-
"""市场监控模块测试：事件套利监控 + 转债打新日历。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from src.services.bond_new_issue_calendar import BondNewIssueCalendarService
from src.services.event_arbitrage_monitor import (
    EventArbitrageMonitorService,
    _EventItem,
)


def _make_notifier(success: bool = True) -> MagicMock:
    """构造模拟通知服务：send_with_results 返回可控结果。"""
    notifier = MagicMock()
    result = MagicMock()
    result.success = success
    notifier.send_with_results.return_value = result
    return notifier


def _make_event(
    code: str = "600491",
    name: str = "ST龙元",
    title: str = "关于要约收购公司股份的提示性公告",
    url: str = "https://example.com/notice/1",
    keyword: str = "要约收购",
    notice_date: str = "2026-08-12",
) -> _EventItem:
    return _EventItem(
        code=code,
        name=name,
        title=title,
        notice_type="要约收购报告书",
        notice_date=notice_date,
        url=url,
        keyword=keyword,
    )


class EventArbitrageKeywordTestCase(unittest.TestCase):
    """关键词匹配与噪声排除。"""

    def test_match_real_event_keywords(self) -> None:
        self.assertEqual(
            EventArbitrageMonitorService._match_keyword("关于要约收购公司股份的公告"),
            "要约收购",
        )
        self.assertEqual(
            EventArbitrageMonitorService._match_keyword("本次终止上市事项现金选择权申报结果"),
            "现金选择权",
        )
        self.assertEqual(
            EventArbitrageMonitorService._match_keyword("公司主动退市的议案"),
            "主动退市",
        )

    def test_exclude_risk_warning_noise(self) -> None:
        # *ST 股"可能因股价低于 1 元而终止上市的风险提示"是噪声，不是套利事件
        self.assertIsNone(
            EventArbitrageMonitorService._match_keyword(
                "关于公司股票存在可能因股价低于1元而终止上市的第二次风险提示公告"
            )
        )
        self.assertIsNone(
            EventArbitrageMonitorService._match_keyword("终止上市风险提示公告")
        )

    def test_no_keyword_returns_none(self) -> None:
        self.assertIsNone(
            EventArbitrageMonitorService._match_keyword("2026年半年度报告")
        )

    def test_absorb_merger_keyword_with_noise_exclusion(self) -> None:
        # 换股吸收合并（真实事件）命中
        self.assertEqual(
            EventArbitrageMonitorService._match_keyword("关于换股吸收合并XX股份有限公司的预案"),
            "吸收合并",
        )
        # 吸收合并全资子公司（内部重组噪声）排除
        self.assertIsNone(
            EventArbitrageMonitorService._match_keyword(
                "关于吸收合并全资子公司的公告"
            )
        )


class EventArbitrageRunOnceTestCase(unittest.TestCase):
    """run_once 集成行为：采集 → 去重 → 推送。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = Path(self._tmp.name) / "state.json"
        self.today = date(2026, 8, 18)
        self.notifier = _make_notifier(success=True)

    def _make_service(self) -> EventArbitrageMonitorService:
        return EventArbitrageMonitorService(
            notifier=self.notifier,
            state_path=self.state_path,
            now_provider=lambda: self.today,
            lookback_days=1,
        )

    def _announcement_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "代码": "600491",
                    "名称": "ST龙元",
                    "公告标题": "ST龙元:关于宁波开海要约收购公司股份的提示性公告",
                    "公告类型": "要约收购报告书",
                    "公告日期": "2026-08-18",
                    "网址": "https://example.com/notice/1",
                },
                {
                    "代码": "300068",
                    "名称": "ST南都",
                    "公告标题": "ST南都:关于新增累计诉讼及仲裁情况的公告",
                    "公告类型": "诉讼仲裁",
                    "公告日期": "2026-08-18",
                    "网址": "https://example.com/notice/2",
                },
                {
                    "代码": "688496",
                    "名称": "*ST清越",
                    "公告标题": "*ST清越:可能因股价低于1元而终止上市的第二次风险提示公告",
                    "公告类型": "终止上市风险提示",
                    "公告日期": "2026-08-18",
                    "网址": "https://example.com/notice/3",
                },
            ]
        )

    @patch("akshare.stock_notice_report")
    def test_run_once_filters_and_notifies(self, mock_report: MagicMock) -> None:
        mock_report.return_value = self._announcement_df()
        service = self._make_service()
        stats = service.run_once()

        self.assertEqual(stats["events"], 1)  # 仅命中要约收购，排除诉讼与风险提示
        self.assertEqual(stats["notified"], 1)
        self.assertEqual(stats["suppressed"], 0)
        self.notifier.send_with_results.assert_called_once()
        call_kwargs = self.notifier.send_with_results.call_args
        self.assertEqual(call_kwargs.kwargs.get("route_type"), "alert")
        message = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get(
            "content", ""
        )
        self.assertIn("ST龙元", message)
        self.assertIn("要约收购", message)
        self.assertIn("巴菲特", message)  # 四问核查表随消息附带

    @patch("akshare.stock_notice_report")
    def test_run_once_dedup_across_rounds(self, mock_report: MagicMock) -> None:
        mock_report.return_value = self._announcement_df()
        service = self._make_service()

        first = service.run_once()
        self.assertEqual(first["notified"], 1)

        # 第二轮同日扫描：去重后不再推送
        second = service.run_once()
        self.assertEqual(second["events"], 1)
        self.assertEqual(second["notified"], 0)
        self.assertEqual(second["suppressed"], 1)
        self.assertEqual(self.notifier.send_with_results.call_count, 1)

    @patch("akshare.stock_notice_report")
    def test_run_once_dedup_persists_across_instances(self, mock_report: MagicMock) -> None:
        """状态持久化：服务重建（模拟进程重启）后同日事件不重复推送。"""
        mock_report.return_value = self._announcement_df()
        first_service = self._make_service()
        self.assertEqual(first_service.run_once()["notified"], 1)

        restarted = self._make_service()  # 新实例，从状态文件恢复
        stats = restarted.run_once()
        self.assertEqual(stats["events"], 1)
        self.assertEqual(stats["notified"], 0)
        self.assertEqual(stats["suppressed"], 1)
        self.assertEqual(self.notifier.send_with_results.call_count, 1)

    @patch("akshare.stock_notice_report")
    def test_run_once_network_failure_returns_zero_safely(self, mock_report: MagicMock) -> None:
        mock_report.side_effect = ConnectionError("eastmoney down")
        service = self._make_service()
        stats = service.run_once()  # 不应抛异常
        self.assertEqual(stats["events"], 0)
        self.assertEqual(stats["notified"], 0)

    @patch("akshare.stock_notice_report")
    def test_notification_failure_does_not_mark_seen(self, mock_report: MagicMock) -> None:
        """通知失败时事件不标记已读，下一轮可重试。"""
        mock_report.return_value = self._announcement_df()
        failing_notifier = _make_notifier(success=False)
        service = EventArbitrageMonitorService(
            notifier=failing_notifier,
            state_path=self.state_path,
            now_provider=lambda: self.today,
        )
        stats = service.run_once()
        self.assertEqual(stats["events"], 1)
        self.assertEqual(stats["notified"], 0)

        # 换成功通知器重跑：事件应重新推送
        service.notifier = self.notifier
        stats2 = service.run_once()
        self.assertEqual(stats2["notified"], 1)


class BondNewIssueClassifyTestCase(unittest.TestCase):
    """三段式窗口判定。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.today = date(2026, 8, 18)
        self.service = BondNewIssueCalendarService(
            state_path=Path(self._tmp.name) / "state.json",
            now_provider=lambda: self.today,
        )

    def _classify(self, subscribe_date, listing_date):
        record = {
            "债券代码": "123283",
            "债券简称": "丰茂转债",
            "申购日期": subscribe_date,
            "上市时间": listing_date,
        }
        return self.service._classify(record, self.today)

    def test_subscribe_stage_today_and_tomorrow(self) -> None:
        self.assertEqual(self._classify(self.today, None), ("subscribe", "123283", "丰茂转债"))
        tomorrow = self.today + timedelta(days=1)
        self.assertEqual(self._classify(tomorrow, None)[0], "subscribe")
        # 后天不在窗口
        day_after = self.today + timedelta(days=2)
        self.assertIsNone(self._classify(day_after, None))

    def test_payment_stage_t1_and_t2(self) -> None:
        # 申购日期 = 今天-1（T+1 结果日）与 今天-2（T+2 缴款日）均命中
        self.assertEqual(self._classify(self.today - timedelta(days=1), None)[0], "payment")
        self.assertEqual(self._classify(self.today - timedelta(days=2), None)[0], "payment")
        # 今天-3 超出窗口
        self.assertIsNone(self._classify(self.today - timedelta(days=3), None))

    def test_listing_stage_today_and_tomorrow(self) -> None:
        # 申购日期已过，上市日在窗口内 → listing
        past_subscribe = self.today - timedelta(days=20)
        self.assertEqual(
            self._classify(past_subscribe, self.today)[0], "listing"
        )
        self.assertEqual(
            self._classify(past_subscribe, self.today + timedelta(days=1))[0], "listing"
        )
        self.assertIsNone(self._classify(past_subscribe, self.today + timedelta(days=2)))

    def test_naT_and_missing_dates_are_ignored(self) -> None:
        self.assertIsNone(self._classify(None, None))
        self.assertIsNone(self._classify(pd.NaT, pd.NaT))
        record = {"债券代码": "123283", "债券简称": "测试", "申购日期": pd.NaT}
        self.assertIsNone(self.service._classify(record, self.today))

    def test_subscribe_takes_priority_over_listing(self) -> None:
        # 同日既是申购日又是（异常的）上市日 → 申购优先（当前动作）
        self.assertEqual(self._classify(self.today, self.today)[0], "subscribe")

    def test_month_and_year_boundaries(self) -> None:
        """跨月/跨年翻转：月末与年末的窗口判定不受日历翻转影响。"""
        # 跨月：8-31 → 9-01（now=8-31，明日申购=9-01）
        month_end = date(2026, 8, 31)
        service = BondNewIssueCalendarService(
            state_path=Path(tempfile.mkdtemp()) / "s.json",
            now_provider=lambda: month_end,
        )
        record = {
            "债券代码": "123283",
            "债券简称": "测试转债",
            "申购日期": "2026-09-01",
            "上市时间": None,
        }
        self.assertEqual(service._classify(record, month_end)[0], "subscribe")

        # 跨年：12-31 → 01-01（now=12-31，缴款窗含 12-30 申购）
        year_end = date(2026, 12, 31)
        service2 = BondNewIssueCalendarService(
            state_path=Path(tempfile.mkdtemp()) / "s.json",
            now_provider=lambda: year_end,
        )
        record2 = {
            "债券代码": "123283",
            "债券简称": "测试转债",
            "申购日期": "2026-12-30",
            "上市时间": None,
        }
        self.assertEqual(service2._classify(record2, year_end)[0], "payment")
        # 跨年上市：now=2026-12-31，明日上市=2027-01-01
        record3 = {
            "债券代码": "123283",
            "债券简称": "测试转债",
            "申购日期": "2026-12-01",
            "上市时间": "2027-01-01",
        }
        self.assertEqual(service2._classify(record3, year_end)[0], "listing")

    def test_tushare_compact_date_format(self) -> None:
        """tushare 备源的 YYYYMMDD 紧凑日期串可被正确解析。"""
        record = {
            "债券代码": "123283",
            "债券简称": "丰茂转债",
            "申购日期": "20260818",  # tushare onl_date 格式
            "上市时间": "20260822",  # tushare list_date 格式
        }
        # now=2026-08-18：申购日命中
        self.assertEqual(self.service._classify(record, self.today)[0], "subscribe")
        # now=2026-08-22：上市日命中
        listing_day = date(2026, 8, 22)
        service2 = BondNewIssueCalendarService(
            state_path=Path(tempfile.mkdtemp()) / "s.json",
            now_provider=lambda: listing_day,
        )
        self.assertEqual(service2._classify(record, listing_day)[0], "listing")


class BondNewIssueRunOnceTestCase(unittest.TestCase):
    """run_once 集成行为。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = Path(self._tmp.name) / "state.json"
        self.today = date(2026, 8, 18)
        self.notifier = _make_notifier(success=True)

    def _calendar_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                # 申购日=今天 → subscribe
                {
                    "债券代码": "123283",
                    "债券简称": "丰茂转债",
                    "申购日期": "2026-08-18",
                    "上市时间": None,
                },
                # 申购日=今天-2 → payment（T+2 缴款日）
                {
                    "债券代码": "123281",
                    "债券简称": "中仑转债",
                    "申购日期": "2026-08-16",
                    "上市时间": None,
                },
                # 上市日=今天 → listing
                {
                    "债券代码": "123279",
                    "债券简称": "天脉转债",
                    "申购日期": "2026-08-03",
                    "上市时间": "2026-08-18",
                },
                # 无窗口命中（历史券）
                {
                    "债券代码": "113707",
                    "债券简称": "科博转债",
                    "申购日期": "2026-07-03",
                    "上市时间": "2026-07-31",
                },
            ]
        )

    def _make_service(self) -> BondNewIssueCalendarService:
        return BondNewIssueCalendarService(
            notifier=self.notifier,
            state_path=self.state_path,
            now_provider=lambda: self.today,
        )

    @patch("akshare.bond_zh_cov")
    def test_run_once_collects_three_stages(self, mock_cov: MagicMock) -> None:
        mock_cov.return_value = self._calendar_df()
        service = self._make_service()
        stats = service.run_once()

        self.assertEqual(stats["reminders"], 3)
        self.assertEqual(stats["notified"], 3)
        self.notifier.send_with_results.assert_called_once()
        message = self.notifier.send_with_results.call_args.args[0]
        self.assertIn("丰茂转债", message)
        self.assertIn("中仑转债", message)
        self.assertIn("天脉转债", message)
        self.assertNotIn("科博转债", message)
        # 分段标题存在
        self.assertIn("今日/明日申购", message)
        self.assertIn("缴款提醒", message)
        self.assertIn("上市卖出提醒", message)

    @patch("akshare.bond_zh_cov")
    def test_run_once_dedup_across_rounds(self, mock_cov: MagicMock) -> None:
        mock_cov.return_value = self._calendar_df()
        service = self._make_service()
        self.assertEqual(service.run_once()["notified"], 3)

        second = service.run_once()
        self.assertEqual(second["notified"], 0)
        self.assertEqual(second["suppressed"], 3)
        self.assertEqual(self.notifier.send_with_results.call_count, 1)

    @patch("akshare.bond_zh_cov")
    def test_run_once_network_failure_safely(self, mock_cov: MagicMock) -> None:
        mock_cov.side_effect = ConnectionError("akshare down")
        service = self._make_service()
        # 双源均失败 → 空结果（patch 备源避免测试环境真实调用 tushare）
        with patch.object(service, "_fetch_calendar_tushare", return_value=[]):
            stats = service.run_once()
        self.assertEqual(stats["reminders"], 0)
        self.assertEqual(stats["notified"], 0)

    @patch("akshare.bond_zh_cov")
    def test_run_once_missing_columns_safely(self, mock_cov: MagicMock) -> None:
        mock_cov.return_value = pd.DataFrame([{"债券代码": "123283"}])  # 缺 申购日期
        service = self._make_service()
        # 备源同样不可用（无 token）→ 空结果
        with patch.object(service, "_fetch_calendar_tushare", return_value=[]):
            stats = service.run_once()
        self.assertEqual(stats["reminders"], 0)

    @patch("akshare.bond_zh_cov")
    def test_tushare_fallback_when_akshare_fails(self, mock_cov: MagicMock) -> None:
        """akshare 断连 → tushare 备源接管，提醒链路不中断。"""
        mock_cov.side_effect = ConnectionError("akshare down")
        service = self._make_service()

        # 模拟 tushare 备源返回（YYYYMMDD 紧凑格式）
        tushare_records = [
            {
                "债券代码": "123284",
                "债券简称": "强达转债",
                "申购日期": "20260819",  # 明日申购
                "上市时间": None,
            },
            {
                "债券代码": "123279",
                "债券简称": "天脉转债",
                "申购日期": "20260803",
                "上市时间": "20260818",  # 今日上市
            },
        ]
        with patch.object(
            service, "_fetch_calendar_tushare", return_value=tushare_records
        ) as mock_ts:
            stats = service.run_once()

        mock_ts.assert_called_once()
        self.assertEqual(stats["reminders"], 2)
        self.assertEqual(stats["notified"], 2)
        message = self.notifier.send_with_results.call_args.args[0]
        self.assertIn("强达转债", message)
        self.assertIn("天脉转债", message)


if __name__ == "__main__":
    unittest.main()

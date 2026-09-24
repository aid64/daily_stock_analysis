# -*- coding: utf-8 -*-
"""P4 通知事件化测试：NotificationEvent 契约 + 非交易日门。"""
from __future__ import annotations

from datetime import date

from src.services.notification_events import (
    NotificationEvent, PRIORITY_HOLDING, ROUTE_ALERT, ROUTE_REPORT,
    is_trading_day, time_sensitive_suppressed_note)


class TestNotificationEvent:
    def test_to_send_kwargs_maps_logical_id(self):
        ev = NotificationEvent(
            logical_id="event_alert:000899:price_cross:below",
            route_type=ROUTE_ALERT, content="止损跌破",
            priority=PRIORITY_HOLDING, symbols=["000899"],
            time_sensitive=True)
        kw = ev.to_send_kwargs()
        assert kw["route_type"] == "alert"
        assert kw["dedup_key"] == kw["cooldown_key"]
        assert kw["dedup_key"] == "event_alert:000899:price_cross:below"
        assert kw["content"] == "止损跌破"

    def test_defaults(self):
        ev = NotificationEvent(
            logical_id="daily_operation_sheet:2026-09-23",
            route_type=ROUTE_REPORT, content="今日待办")
        assert ev.priority == 30 and ev.symbols == []
        assert ev.time_sensitive is False and ev.bypass_digest is False


class TestNonTradingGate:
    def test_trading_day_no_suppression(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.notification_events.is_market_open",
            lambda market, check: True)
        assert time_sensitive_suppressed_note("cn") is None

    def test_non_trading_day_suppresses(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.notification_events.is_market_open",
            lambda market, check: False)
        note = time_sensitive_suppressed_note("cn")
        assert note is not None
        assert "非交易日" in note and "下个交易日" in note

    def test_is_trading_day_fail_open(self, monkeypatch):
        """日历库异常 → fail-open True（不因日历缺失而静默不发）。"""
        def _boom(*a, **k):
            raise RuntimeError("no xcals")
        monkeypatch.setattr(
            "src.services.notification_events.is_market_open", _boom)
        assert is_trading_day("cn") is True


class TestDecisionPackageNonTradingGate:
    """决策包渲染在非交易日抑制挂单边界，加顶部说明（P4-4）。"""

    def _decision_and_sheet(self, monkeypatch):
        import src.services.account_decision_service as ads
        from src.services.account_decision_service import (
            build_account_decision, decision_to_sheet)

        monkeypatch.setattr(ads, "compute_e2_percentile", lambda: (None, ""))
        monkeypatch.setattr(
            ads, "_latest_ai_notes",
            lambda symbols, days=3: ({}, {"000899": "赣能股份"}))
        monkeypatch.setattr(
            "src.services.strategies.user_rules.diagnose_breach",
            lambda **kw: None)
        snap = {"total_cash": 1549.0, "accounts": [{"positions": [
            {"symbol": "000899", "quantity": 100, "avg_cost": 11.16,
             "last_price": 9.75, "price_date": "2026-09-14"}]}]}
        rules = {"000899": {"stop_loss": [10.87]}}
        decision = build_account_decision(snapshot=snap, rules=rules)
        sheet = decision_to_sheet(decision, available_cash=1549.0)
        return decision, sheet

    def test_non_trading_suppresses_boundary(self, monkeypatch):
        from src.services.account_decision_service import (
            render_decision_package)
        monkeypatch.setattr(
            "src.services.notification_events.is_trading_day",
            lambda market="cn", check=None: False)
        decision, sheet = self._decision_and_sheet(monkeypatch)
        text = render_decision_package(decision, sheet=sheet)
        assert "今日非交易日" in text
        assert "14:50 未成交撤单" not in text   # 时效边界被抑制
        # 回填命令仍在（成交后才发，不带时效）
        assert "/成交回填" in text

    def test_trading_day_shows_boundary(self, monkeypatch):
        from src.services.account_decision_service import (
            render_decision_package)
        monkeypatch.setattr(
            "src.services.notification_events.is_trading_day",
            lambda market="cn", check=None: True)
        decision, sheet = self._decision_and_sheet(monkeypatch)
        text = render_decision_package(decision, sheet=sheet)
        assert "今日非交易日" not in text
        assert "14:50 未成交撤单" in text        # 交易日正常展示边界


class TestAiNoteFreshness:
    """P4-5：ai_note 超窗口标陈旧，不冒充当前观点。"""

    def test_stale_note_flagged(self, monkeypatch):
        from datetime import date, timedelta
        import src.services.account_decision_service as ads

        old_day = (date.today() - timedelta(days=30)).isoformat()

        class _Rec:
            name = "赣能股份"
            operation_advice = "观望"
            analysis_summary = "弱势整理。后续观察"
            created_at = old_day

        class _Repo:
            def get_list(self, code, days, limit):
                return [_Rec()]

        monkeypatch.setattr(
            "src.repositories.analysis_repo.AnalysisRepository", _Repo)
        notes, _ = ads._latest_ai_notes(["000899"], days=3)
        assert "[陈旧·30天前]" in notes["000899"]

    def test_fresh_note_not_flagged(self, monkeypatch):
        from datetime import date
        import src.services.account_decision_service as ads

        class _Rec:
            name = "赣能股份"
            operation_advice = "观望"
            analysis_summary = "弱势整理。后续观察"
            created_at = date.today().isoformat()

        class _Repo:
            def get_list(self, code, days, limit):
                return [_Rec()]

        monkeypatch.setattr(
            "src.repositories.analysis_repo.AnalysisRepository", _Repo)
        notes, _ = ads._latest_ai_notes(["000899"], days=3)
        assert "[陈旧" not in notes["000899"]

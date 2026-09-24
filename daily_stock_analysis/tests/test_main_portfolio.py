from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import main
from src.services.runtime_scheduler import RuntimeSchedulerService


class MainPortfolioTest(unittest.TestCase):
    def test_resolve_portfolio_stock_codes_returns_none_when_disabled(self):
        self.assertIsNone(main._resolve_portfolio_stock_codes(SimpleNamespace()))

    def test_resolve_portfolio_stock_codes_rejects_any_broker(self):
        args = SimpleNamespace(portfolio="futu")
        with self.assertRaisesRegex(ValueError, "券商持仓导入已移除"):
            main._resolve_portfolio_stock_codes(args)

    def test_run_full_analysis_propagates_unsupported_portfolio(self):
        config = SimpleNamespace()
        args = SimpleNamespace(portfolio="futu")

        with patch.object(
            main,
            "_refresh_stock_index_cache_for_analysis",
        ), self.assertRaisesRegex(ValueError, "券商持仓导入已移除"):
            main.run_full_analysis(config, args)

    def test_run_full_analysis_returns_false_when_stock_analysis_generates_no_reports(self):
        args = SimpleNamespace(
            portfolio=None,
            single_notify=False,
            no_context_snapshot=True,
            no_market_review=True,
            workers=1,
            dry_run=False,
            no_notify=True,
            schedule=False,
        )
        config = SimpleNamespace(
            refresh_stock_list=MagicMock(),
            single_stock_notify=False,
            merge_email_notification=False,
            market_review_enabled=False,
            market_review_region="cn",
            daily_market_context_enabled=False,
            analysis_delay=0,
            backtest_enabled=False,
        )
        pipeline = MagicMock()
        pipeline.run.return_value = []

        with patch.object(main, "_refresh_stock_index_cache_for_analysis"), patch.object(
            main,
            "_compute_trading_day_filter",
            return_value=(["600519"], "cn", False),
        ), patch(
            "src.core.pipeline.StockAnalysisPipeline",
            return_value=pipeline,
        ), patch(
            "src.core.market_review.run_market_review",
        ), patch(
            "src.feishu_doc.FeishuDocManager",
        ) as feishu_manager, patch.object(
            main,
            "_run_auto_backtest",
        ) as run_auto_backtest:
            feishu_manager.return_value.is_configured.return_value = False
            result = main.run_full_analysis(config, args, ["600519"])

        self.assertFalse(result)
        self.assertEqual(main._LAST_ANALYSIS_FAILURE_REASON, "no_report")
        pipeline.run.assert_called_once()
        run_auto_backtest.assert_called_once_with(config)

    def test_run_full_analysis_returns_false_when_stock_list_is_empty_without_market_review(self):
        args = SimpleNamespace(
            portfolio=None,
            single_notify=False,
            no_context_snapshot=True,
            no_market_review=True,
            workers=1,
            dry_run=False,
            no_notify=True,
            schedule=False,
        )
        config = SimpleNamespace(
            refresh_stock_list=MagicMock(),
            stock_list=[],
            single_stock_notify=False,
            merge_email_notification=False,
            market_review_enabled=False,
            market_review_region="cn",
            daily_market_context_enabled=False,
            analysis_delay=0,
            backtest_enabled=False,
        )

        with patch.object(main, "_refresh_stock_index_cache_for_analysis"), patch.object(
            main,
            "_compute_trading_day_filter",
            side_effect=AssertionError("empty STOCK_LIST must fail before trading-day filter"),
        ), patch(
            "src.core.market_review.run_market_review",
        ), patch(
            "src.core.pipeline.StockAnalysisPipeline",
        ) as pipeline_cls, patch.object(
            main,
            "_run_auto_backtest",
        ) as run_auto_backtest:
            result = main.run_full_analysis(config, args)

        self.assertFalse(result)
        self.assertEqual(main._LAST_ANALYSIS_FAILURE_REASON, "empty_stock_list")
        pipeline_cls.assert_not_called()
        run_auto_backtest.assert_called_once_with(config)

    def test_run_full_analysis_returns_false_when_local_report_save_fails(self):
        args = SimpleNamespace(
            portfolio=None,
            single_notify=False,
            no_context_snapshot=True,
            no_market_review=True,
            workers=1,
            dry_run=False,
            no_notify=True,
            schedule=False,
        )
        config = SimpleNamespace(
            refresh_stock_list=MagicMock(),
            single_stock_notify=False,
            merge_email_notification=False,
            market_review_enabled=False,
            market_review_region="cn",
            daily_market_context_enabled=False,
            analysis_delay=0,
            backtest_enabled=False,
        )
        pipeline = MagicMock()
        pipeline.run.return_value = [
            SimpleNamespace(
                code="600519",
                success=True,
                sentiment_score=90,
                name="贵州茅台",
                operation_advice="持有",
                trend_prediction="震荡上行",
                get_emoji=MagicMock(return_value="📈"),
            )
        ]
        pipeline._last_local_report_path = None
        pipeline._last_local_report_error = "permission denied"

        with patch.object(main, "_refresh_stock_index_cache_for_analysis"), patch.object(
            main,
            "_compute_trading_day_filter",
            return_value=(["600519"], "cn", False),
        ), patch(
            "src.core.pipeline.StockAnalysisPipeline",
            return_value=pipeline,
        ), patch(
            "src.core.market_review.run_market_review",
        ), patch(
            "src.feishu_doc.FeishuDocManager",
        ) as feishu_manager, patch.object(
            main,
            "_run_auto_backtest",
        ) as run_auto_backtest:
            feishu_manager.return_value.is_configured.return_value = False
            result = main.run_full_analysis(config, args, ["600519"])

        self.assertFalse(result)
        self.assertEqual(main._LAST_ANALYSIS_FAILURE_REASON, "report_save_failed")
        pipeline.run.assert_called_once()
        run_auto_backtest.assert_called_once_with(config)

    def test_runtime_scheduler_does_not_carry_portfolio_override(self):
        scheduler = RuntimeSchedulerService(owns_schedule=False)
        args = scheduler._make_schedule_args()

        self.assertFalse(hasattr(args, "portfolio"))


if __name__ == "__main__":
    unittest.main()

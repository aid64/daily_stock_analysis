/**
 * HomeReportActions — 个股报告操作按钮栏。
 * 从 HomePage 提取，提供重分析 / AI 追问 / 历史趋势 / 完整报告等操作入口。
 */
import type React from 'react';
import { BarChart3, RefreshCw, MessageCircle, FileText } from 'lucide-react';
import { Button } from '@/components/common/Button';
import type { UiTextKey } from '@/i18n/uiText';

export interface HomeReportActionsProps {
  isMarketReviewHistoryReport: boolean;
  isAnalyzing: boolean;
  selectedReportId: unknown;
  isSubmittingMarketReview: boolean;
  isHistoryTrendUnavailable: boolean;
  isHistoryTrendOpen: boolean;
  onReanalyze: () => void;
  onAskFollowUp: () => void;
  onTriggerMarketReview: () => void;
  onToggleHistoryTrend: () => void;
  onOpenMarkdownDrawer: () => void;
  t: (key: UiTextKey) => string;
}

export const HomeReportActions: React.FC<HomeReportActionsProps> = ({
  isMarketReviewHistoryReport,
  isAnalyzing,
  selectedReportId,
  isSubmittingMarketReview,
  isHistoryTrendUnavailable,
  isHistoryTrendOpen,
  onReanalyze,
  onAskFollowUp,
  onTriggerMarketReview,
  onToggleHistoryTrend,
  onOpenMarkdownDrawer,
  t,
}) => {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {!isMarketReviewHistoryReport ? (
        <>
          <Button
            variant="home-action-ai"
            size="sm"
            disabled={isAnalyzing || selectedReportId === undefined}
            onClick={onReanalyze}
          >
            <RefreshCw className="h-4 w-4" />
            {t('home.reanalyze')}
          </Button>
          <Button
            variant="home-action-ai"
            size="sm"
            disabled={selectedReportId === undefined}
            onClick={onAskFollowUp}
          >
            <MessageCircle className="h-4 w-4" />
            {t('home.askAi')}
          </Button>
        </>
      ) : (
        <Button
          variant="home-action-ai"
          size="sm"
          disabled={isSubmittingMarketReview}
          isLoading={isSubmittingMarketReview}
          loadingText={t('home.submitMarketReview')}
          onClick={() => void onTriggerMarketReview()}
        >
          <BarChart3 className="h-4 w-4" />
          {t('home.rerunMarketReview')}
        </Button>
      )}
      <Button
        variant="home-action-ai"
        size="sm"
        disabled={selectedReportId === undefined || isHistoryTrendUnavailable}
        className={
          isHistoryTrendOpen
            ? 'border-primary/70 bg-primary/15 text-primary shadow-glow-cyan'
            : undefined
        }
        onClick={onToggleHistoryTrend}
      >
        <BarChart3 className="h-4 w-4" />
        {t('home.historyTrend')}
      </Button>
      <Button
        variant="home-action-ai"
        size="sm"
        disabled={selectedReportId === undefined}
        onClick={onOpenMarkdownDrawer}
      >
        <FileText className="h-4 w-4" />
        {t('home.fullReport')}
      </Button>
    </div>
  );
};

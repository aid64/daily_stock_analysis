import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { decisionSignalsApi } from '../api/decisionSignals';
import type { DecisionSignalOutcomeStatsResponse } from '../api/decisionSignals';
import { parseApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, AppPage, Card, EmptyState, PageHeader, StatCard } from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';

function buildParsedError(error: unknown): ParsedApiError {
  return parseApiError(error);
}

function pickNumber(value: unknown): number | null {
  return typeof value === 'number' ? value : null;
}

function hitRatePct(stats: DecisionSignalOutcomeStatsResponse): string {
  const pct = pickNumber(stats.hitRatePct);
  return pct !== null ? `${pct.toFixed(1)}%` : '-';
}

function avgReturnPct(stats: DecisionSignalOutcomeStatsResponse): string {
  const pct = pickNumber(stats.avgStockReturnPct);
  return pct !== null ? `${pct.toFixed(2)}%` : '-';
}

export default function SignalStatsPage() {
  const { t } = useUiLanguage();
  const [stats, setStats] = useState<Awaited<ReturnType<typeof decisionSignalsApi.getOutcomeStats>> | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const data = await decisionSignalsApi.getOutcomeStats();
      setStats(data);
    } catch (err) {
      setError(buildParsedError(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AppPage>
      <PageHeader
        title={t('assistant.stats.title')}
        description={t('assistant.stats.description')}
        actions={
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            {t('assistant.stats.refresh')}
          </button>
        }
      />
      {error ? <ApiErrorAlert error={error} /> : null}
      {loading ? (
        <Card className="p-6 text-sm text-muted-foreground">
          {t('assistant.stats.loading')}
        </Card>
      ) : null}
      {!loading && !stats ? <EmptyState title="-" /> : null}
      {stats ? (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label={t('assistant.stats.total')} value={stats.total} />
            <StatCard label={t('assistant.stats.hitRate')} value={hitRatePct(stats)} />
            <StatCard label={t('assistant.stats.avgReturn')} value={avgReturnPct(stats)} />
            <StatCard label={t('assistant.stats.evaluated')} value={stats.completed} />
          </div>
          <Card className="p-4 text-sm text-muted-foreground">
            {t('assistant.stats.note')}
          </Card>
        </div>
      ) : null}
    </AppPage>
  );
}

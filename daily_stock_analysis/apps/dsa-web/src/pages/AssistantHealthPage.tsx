import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react';
import {
  assistantApi,
  type AssistantHealthView,
} from '../api/assistant';
import { parseApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, AppPage, Card, PageHeader, StatCard } from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';

function buildParsedError(error: unknown): ParsedApiError {
  return parseApiError(error);
}

export default function AssistantHealthPage() {
  const { t } = useUiLanguage();
  const [view, setView] = useState<AssistantHealthView | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const { data } = await assistantApi.getHealthView();
      setView(data as unknown as AssistantHealthView);
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
        title={t('assistant.health.title')}
        description={t('assistant.health.description')}
        actions={
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            {t('assistant.health.refresh')}
          </button>
        }
      />
      {error ? <ApiErrorAlert error={error} /> : null}
      {loading && !view ? (
        <Card className="p-6 text-sm text-muted-foreground">
          {t('assistant.health.loading')}
        </Card>
      ) : null}
      {view ? (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            {view.healthy ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-600" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-amber-500" />
            )}
            <span className="font-medium">
              {view.healthy
                ? t('assistant.health.allNormal')
                : t('assistant.health.hasAnomalies')}
            </span>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label={t('assistant.health.signal')}
              value={
                view.signal.available
                  ? `${view.signal.n_targets ?? 0}`
                  : t('assistant.health.unavailable')
              }
              hint={
                view.signal.available
                  ? `${t('assistant.health.pitEnd')}: ${view.signal.pit_end ?? '-'}`
                  : view.signal.reason ?? ''
              }
            />
            <StatCard
              label={t('assistant.health.pushBus')}
              value={view.push.bus_count}
              hint={`${t('assistant.health.digest')}: ${view.push.digest_sent ? t('assistant.health.sent') : t('assistant.health.notSent')}`}
            />
            <StatCard
              label={t('assistant.health.activeSignals')}
              value={view.active_signals.count ?? '-'}
            />
            <StatCard
              label={t('assistant.health.orderSheet')}
              value={
                view.order_sheet.exists
                  ? t('assistant.health.orderSheetStatus', {
                      status: view.order_sheet.status ?? '-',
                      orders: view.order_sheet.n_orders ?? 0,
                    })
                  : t('assistant.health.orderSheetNone')
              }
            />
          </div>
          {view.anomalies.length > 0 ? (
            <Card className="border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950">
              <div className="font-medium text-amber-800 dark:text-amber-200">
                {t('assistant.health.anomalies')}
              </div>
              <ul className="mt-2 list-inside list-disc text-sm text-amber-700 dark:text-amber-300">
                {view.anomalies.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </Card>
          ) : null}
        </div>
      ) : null}
    </AppPage>
  );
}

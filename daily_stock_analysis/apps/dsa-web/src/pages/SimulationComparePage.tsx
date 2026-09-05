import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import {
  assistantApi,
  type SimulationComparison,
} from '../api/assistant';
import { parseApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, AppPage, Card, PageHeader, StatCard } from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';

function buildParsedError(error: unknown): ParsedApiError {
  return parseApiError(error);
}

export default function SimulationComparePage() {
  const { t } = useUiLanguage();
  const [comparison, setComparison] = useState<SimulationComparison | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const { data } = await assistantApi.getComparison();
      setComparison(data as unknown as SimulationComparison);
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
        title={t('assistant.compare.title')}
        description={t('assistant.compare.description')}
        actions={
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            {t('assistant.compare.refresh')}
          </button>
        }
      />
      {error ? <ApiErrorAlert error={error} /> : null}
      {loading ? <Card className="p-6 text-sm text-muted-foreground">加载中…</Card> : null}
      {comparison ? (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <StatCard label={t('assistant.compare.simCount')} value={comparison.n_sim} />
            <StatCard label={t('assistant.compare.matched')} value={comparison.n_matched} />
            <StatCard
              label={t('assistant.compare.avgDiff')}
              value={
                comparison.avg_price_diff_pct !== null
                  ? `${comparison.avg_price_diff_pct > 0 ? '+' : ''}${comparison.avg_price_diff_pct}%`
                  : '-'
              }
              hint={t('assistant.compare.avgDiffHint')}
            />
          </div>
          <Card className="overflow-x-auto p-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 pr-3">{t('assistant.compare.date')}</th>
                  <th className="py-2 pr-3">{t('assistant.compare.symbol')}</th>
                  <th className="py-2 pr-3">{t('assistant.compare.direction')}</th>
                  <th className="py-2 pr-3">{t('assistant.compare.simPrice')}</th>
                  <th className="py-2 pr-3">{t('assistant.compare.simCost')}</th>
                  <th className="py-2 pr-3">{t('assistant.compare.realPrice')}</th>
                  <th className="py-2">{t('assistant.compare.status')}</th>
                </tr>
              </thead>
              <tbody>
                {comparison.rows.map((row) => (
                  <tr key={`${row.trade_date}-${row.symbol}-${row.direction}`}
                      className="border-b last:border-0">
                    <td className="py-2 pr-3">{row.trade_date}</td>
                    <td className="py-2 pr-3 font-medium">{row.symbol}</td>
                    <td className="py-2 pr-3">
                      {row.direction === 'buy'
                        ? t('assistant.compare.buy')
                        : t('assistant.compare.sell')}
                    </td>
                    <td className="py-2 pr-3">{row.sim_price}</td>
                    <td className="py-2 pr-3">¥{row.sim_cost}</td>
                    <td className="py-2 pr-3">{row.real_price ?? '-'}</td>
                    <td className="py-2">
                      {row.matched
                        ? t('assistant.compare.filled')
                        : t('assistant.compare.unfilled')}
                    </td>
                  </tr>
                ))}
                {comparison.rows.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-4 text-center text-muted-foreground">
                      {t('assistant.compare.empty')}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </Card>
        </div>
      ) : null}
    </AppPage>
  );
}

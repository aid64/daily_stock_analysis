import type React from 'react';
import { useState, useCallback } from 'react';
import { CheckCircle, XCircle, Clock, AlertTriangle, AlertCircle, Play, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';
import { Button } from '../common';
import { SettingsSectionCard } from './SettingsSectionCard';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { systemStatusApi } from '../../api/systemStatus';
import { analysisApi } from '../../api/analysis';
import { historyApi } from '../../api/history';
import { systemConfigApi } from '../../api/systemConfig';
import { getParsedApiError } from '../../api/error';

type ApiTestType = 'health' | 'stocks' | 'history' | 'generation' | 'analysis';

interface ApiTestResult {
  type: ApiTestType;
  name: string;
  status: 'success' | 'failed' | 'warning';
  message: string;
  details?: Record<string, unknown>;
  latencyMs: number;
}

const TEST_TYPES = [
  { type: 'health', nameKey: 'settings.apiTestHealth' },
  { type: 'stocks', nameKey: 'settings.apiTestStocks' },
  { type: 'history', nameKey: 'settings.apiTestHistory' },
  { type: 'generation', nameKey: 'settings.apiTestGeneration' },
  { type: 'analysis', nameKey: 'settings.apiTestAnalysis' },
] as const;

export function ApiTestPanel(): React.ReactElement {
  const { t } = useUiLanguage();
  const [selectedTests, setSelectedTests] = useState<ApiTestType[]>(['health', 'stocks', 'history', 'generation']);
  const [results, setResults] = useState<ApiTestResult[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [expandedResult, setExpandedResult] = useState<ApiTestType | null>(null);

  const toggleTest = useCallback((type: ApiTestType) => {
    setSelectedTests((prev) =>
      prev.includes(type) ? prev.filter((t) => t !== type) : [...prev, type]
    );
  }, []);

  const toggleExpand = useCallback((type: ApiTestType | null) => {
    setExpandedResult(type);
  }, []);

  const runHealthCheck = useCallback(async (): Promise<ApiTestResult> => {
    const startTime = performance.now();
    try {
      const response = await systemStatusApi.getHealth();
      const latency = Math.round(performance.now() - startTime);
      return {
        type: 'health',
        name: t('settings.apiTestHealth'),
        status: response.status === 'ok' ? 'success' : 'failed',
        message: t('settings.apiTestHealthSuccess'),
        latencyMs: latency,
      };
    } catch (error) {
      const latency = Math.round(performance.now() - startTime);
      const parsedError = getParsedApiError(error);
      return {
        type: 'health',
        name: t('settings.apiTestHealth'),
        status: 'failed',
        message: parsedError.message || t('settings.apiTestHealthFailed'),
        latencyMs: latency,
      };
    }
  }, [t]);

  const runStocksTest = useCallback(async (): Promise<ApiTestResult> => {
    const startTime = performance.now();
    try {
      const watchlist = await systemConfigApi.getWatchlist();
      const latency = Math.round(performance.now() - startTime);
      if (watchlist.length > 0) {
        return {
          type: 'stocks',
          name: t('settings.apiTestStocks'),
          status: 'success',
          message: t('settings.apiTestStocksSuccess', { name: watchlist[0] }),
          details: { count: watchlist.length },
          latencyMs: latency,
        };
      }
      return {
        type: 'stocks',
        name: t('settings.apiTestStocks'),
        status: 'warning',
        message: t('common.noData'),
        latencyMs: latency,
      };
    } catch (error) {
      const latency = Math.round(performance.now() - startTime);
      const parsedError = getParsedApiError(error);
      return {
        type: 'stocks',
        name: t('settings.apiTestStocks'),
        status: 'failed',
        message: parsedError.message || t('settings.apiTestStocksFailed'),
        latencyMs: latency,
      };
    }
  }, [t]);

  const runHistoryTest = useCallback(async (): Promise<ApiTestResult> => {
    const startTime = performance.now();
    try {
      const response = await historyApi.getList({ limit: 1 });
      const latency = Math.round(performance.now() - startTime);
      return {
        type: 'history',
        name: t('settings.apiTestHistory'),
        status: 'success',
        message: t('settings.apiTestHistorySuccess', { count: response.total }),
        details: { total: response.total },
        latencyMs: latency,
      };
    } catch (error) {
      const latency = Math.round(performance.now() - startTime);
      const parsedError = getParsedApiError(error);
      return {
        type: 'history',
        name: t('settings.apiTestHistory'),
        status: 'failed',
        message: parsedError.message || t('settings.apiTestHistoryFailed'),
        latencyMs: latency,
      };
    }
  }, [t]);

  const runGenerationTest = useCallback(async (): Promise<ApiTestResult> => {
    const startTime = performance.now();
    try {
      const response = await systemConfigApi.getGenerationBackendStatus();
      const latency = Math.round(performance.now() - startTime);
      const primaryAvailable = response.primary?.available ?? false;
      const fallbackAvailable = response.fallback?.available ?? false;
      
      if (primaryAvailable || fallbackAvailable) {
        return {
          type: 'generation',
          name: t('settings.apiTestGeneration'),
          status: primaryAvailable ? 'success' : 'warning',
          message: t('settings.apiTestGenerationSuccess'),
          details: {
            primaryAvailable,
            fallbackAvailable,
            primaryBackend: response.primary?.backendId,
            fallbackBackend: response.fallback?.backendId,
          },
          latencyMs: latency,
        };
      }
      return {
        type: 'generation',
        name: t('settings.apiTestGeneration'),
        status: 'failed',
        message: t('settings.apiTestGenerationFailed'),
        details: {
          primaryAvailable,
          fallbackAvailable,
        },
        latencyMs: latency,
      };
    } catch (error) {
      const latency = Math.round(performance.now() - startTime);
      const parsedError = getParsedApiError(error);
      return {
        type: 'generation',
        name: t('settings.apiTestGeneration'),
        status: 'failed',
        message: parsedError.message || t('settings.apiTestGenerationFailed'),
        latencyMs: latency,
      };
    }
  }, [t]);

  const runAnalysisTest = useCallback(async (): Promise<ApiTestResult> => {
    const startTime = performance.now();
    try {
      const watchlist = await systemConfigApi.getWatchlist();
      if (watchlist.length === 0) {
        const latency = Math.round(performance.now() - startTime);
        return {
          type: 'analysis',
          name: t('settings.apiTestAnalysis'),
          status: 'warning',
          message: t('common.noData'),
          latencyMs: latency,
        };
      }
      
      const response = await analysisApi.analyzeAsync({
        stockCodes: [watchlist[0]],
        forceRefresh: false,
      });
      const latency = Math.round(performance.now() - startTime);
      
      const taskId = 'taskId' in response ? response.taskId : '';
      return {
        type: 'analysis',
        name: t('settings.apiTestAnalysis'),
        status: 'success',
        message: t('settings.apiTestAnalysisSuccess', { taskId }),
        details: { taskId },
        latencyMs: latency,
      };
    } catch (error) {
      const latency = Math.round(performance.now() - startTime);
      const parsedError = getParsedApiError(error);
      return {
        type: 'analysis',
        name: t('settings.apiTestAnalysis'),
        status: 'failed',
        message: parsedError.message || t('settings.apiTestAnalysisFailed'),
        latencyMs: latency,
      };
    }
  }, [t]);

  const runAllTests = useCallback(async () => {
    setResults([]);
    setIsRunning(true);

    const testMap: Record<ApiTestType, () => Promise<ApiTestResult>> = {
      health: runHealthCheck,
      stocks: runStocksTest,
      history: runHistoryTest,
      generation: runGenerationTest,
      analysis: runAnalysisTest,
    };

    const newResults: ApiTestResult[] = [];

    for (const testType of selectedTests) {
      const testFn = testMap[testType];
      if (testFn) {
        const result = await testFn();
        newResults.push(result);
        setResults([...newResults]);
      }
    }

    setIsRunning(false);
  }, [selectedTests, runHealthCheck, runStocksTest, runHistoryTest, runGenerationTest, runAnalysisTest]);

  const passedCount = results.filter((r) => r.status === 'success').length;
  const warningCount = results.filter((r) => r.status === 'warning').length;
  const failedCount = results.filter((r) => r.status === 'failed').length;
  const totalLatency = results.reduce((sum, r) => sum + r.latencyMs, 0);

  const renderStatusIcon = (status: ApiTestResult['status']) => {
    switch (status) {
      case 'success':
        return <CheckCircle className="h-5 w-5 text-green-500" aria-hidden="true" />;
      case 'warning':
        return <AlertTriangle className="h-5 w-5 text-amber-500" aria-hidden="true" />;
      case 'failed':
        return <XCircle className="h-5 w-5 text-red-500" aria-hidden="true" />;
    }
  };

  const renderResultDetails = (result: ApiTestResult) => {
    if (!result.details) return null;
    
    if (result.type === 'generation') {
      const details = result.details;
      return (
        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800">
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <span className="text-muted-text">主后端:</span>
              <span className={`ml-2 ${details.primaryAvailable ? 'text-green-500' : 'text-red-500'}`}>
                {details.primaryAvailable ? '可用' : '不可用'}
              </span>
            </div>
            <div>
              <span className="text-muted-text">备用后端:</span>
              <span className={`ml-2 ${details.fallbackAvailable ? 'text-green-500' : 'text-gray-500'}`}>
                {details.fallbackAvailable ? '可用' : '未配置'}
              </span>
            </div>
          </div>
        </div>
      );
    }

    return null;
  };

  return (
    <SettingsSectionCard
      title={t('settings.apiTestTitle')}
      description={t('settings.apiTestDescription')}
    >
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex flex-wrap gap-2">
            {TEST_TYPES.map((test) => (
              <button
                key={test.type}
                type="button"
                onClick={() => toggleTest(test.type)}
                className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                  selectedTests.includes(test.type)
                    ? 'bg-blue-100 text-blue-700'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {t(test.nameKey)}
              </button>
            ))}
          </div>
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={runAllTests}
            disabled={isRunning || selectedTests.length === 0}
            isLoading={isRunning}
            loadingText={t('settings.apiTestRunning')}
            className="shrink-0"
          >
            {isRunning ? (
              <RefreshCw className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Play className="mr-2 h-4 w-4" aria-hidden="true" />
            )}
            {t('settings.apiTestRun')}
          </Button>
        </div>

        {results.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800">
              <div className="flex items-center gap-4">
                {passedCount > 0 && (
                  <span className="flex items-center gap-1 text-sm">
                    <CheckCircle className="h-4 w-4 text-green-500" aria-hidden="true" />
                    <span className="text-green-600">{passedCount} 成功</span>
                  </span>
                )}
                {warningCount > 0 && (
                  <span className="flex items-center gap-1 text-sm">
                    <AlertTriangle className="h-4 w-4 text-amber-500" aria-hidden="true" />
                    <span className="text-amber-600">{warningCount} 警告</span>
                  </span>
                )}
                {failedCount > 0 && (
                  <span className="flex items-center gap-1 text-sm">
                    <XCircle className="h-4 w-4 text-red-500" aria-hidden="true" />
                    <span className="text-red-600">{failedCount} 失败</span>
                  </span>
                )}
              </div>
              <span className="flex items-center gap-1 text-sm text-muted-text">
                <Clock className="h-4 w-4" aria-hidden="true" />
                {totalLatency} ms
              </span>
            </div>

            {results.map((result) => (
              <div
                key={result.type}
                className="rounded-lg border border-gray-200 bg-white shadow-sm transition-colors hover:border-gray-300 dark:border-gray-700 dark:bg-gray-800"
              >
                <button
                  type="button"
                  onClick={() => toggleExpand(expandedResult === result.type ? null : result.type)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left"
                >
                  {renderStatusIcon(result.status)}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground">{result.name}</p>
                    <p className="text-xs text-muted-text">{result.message}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-xs text-muted-text">{result.latencyMs} ms</span>
                    {result.details ? (
                      expandedResult === result.type ? (
                        <ChevronUp className="h-4 w-4 text-muted-text" aria-hidden="true" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-muted-text" aria-hidden="true" />
                      )
                    ) : null}
                  </div>
                </button>
                {expandedResult === result.type && renderResultDetails(result)}
              </div>
            ))}
          </div>
        )}

        {results.length === 0 && !isRunning && (
          <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-center dark:border-gray-700 dark:bg-gray-800">
            <AlertCircle className="mx-auto h-8 w-8 text-muted-text" aria-hidden="true" />
            <p className="mt-3 text-sm font-medium text-foreground">
              {t('settings.apiTestNoResults')}
            </p>
            <p className="mt-1 text-xs text-muted-text">
              {t('settings.apiTestSelectAndRun')}
            </p>
          </div>
        )}

        {selectedTests.length > 0 && !isRunning && results.length > 0 && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => setResults([])}
            className="w-full"
          >
            清除结果
          </Button>
        )}
      </div>
    </SettingsSectionCard>
  );
}
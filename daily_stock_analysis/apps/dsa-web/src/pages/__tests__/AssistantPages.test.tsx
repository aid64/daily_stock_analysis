// -*- coding: utf-8 -*-
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';

const { get } = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../api/index', () => ({
  default: { get },
}));

function renderPage(node: React.ReactElement) {
  return render(<UiLanguageProvider>{node}</UiLanguageProvider>);
}

const healthResponse = {
  date: '2026-09-07',
  signal: {
    available: true, pit_end: '2026-09-04', age_days: 3, stale: false,
    n_targets: 5, latest_rebalance: '2026-07-01',
  },
  push: { bus_count: 4, holding_related_count: 1, digest_sent: false },
  order_sheet: {
    exists: true, sheet_id: '2026-09-07_x', status: 'pending',
    n_orders: 2, n_holds: 3, n_fills: 0,
    total_buy_value: 1165, total_sell_value: 1280,
  },
  active_signals: { count: 1 },
  anomalies: [],
  healthy: true,
};

const comparisonResponse = {
  rows: [
    {
      trade_date: '2026-09-07', symbol: 'SH113062', direction: 'sell',
      sim_quantity: 10, sim_price: 128, sim_cost: 5.03,
      real_price: 127.8, real_quantity: 10, matched: true,
    },
    {
      trade_date: '2026-09-07', symbol: 'SZ123089', direction: 'buy',
      sim_quantity: 10, sim_price: 116.5, sim_cost: 5.03,
      real_price: null, real_cost: null, matched: false,
    },
  ],
  n_sim: 2, n_matched: 1, n_unmatched: 1,
  avg_price_diff_pct: -0.16,
  comparison_date: '2026-09-07',
};

beforeEach(() => {
  localStorage.setItem('dsa.uiLanguage', 'zh');
  get.mockReset();
});

describe('AssistantHealthPage', () => {
  it('renders five health elements and no anomalies', async () => {
    get.mockResolvedValue({ data: healthResponse });
    const AssistantHealthPage = (await import('../AssistantHealthPage')).default;
    renderPage(<AssistantHealthPage />);
    await waitFor(() => expect(screen.getByText('一切正常')).toBeInTheDocument());
    expect(screen.getByText(/PIT 末端: 2026-09-04/)).toBeInTheDocument();
    expect(screen.getByText('今日推送总线')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText(/pending/)).toBeInTheDocument();
  });

  it('shows anomaly banner when unhealthy', async () => {
    get.mockResolvedValue({
      data: { ...healthResponse, healthy: false,
              anomalies: ['CB 信号数据超 7 天未更新'] },
    });
    const AssistantHealthPage = (await import('../AssistantHealthPage')).default;
    renderPage(<AssistantHealthPage />);
    expect(await screen.findByText('存在需要关注的异常')).toBeInTheDocument();
    expect(screen.getByText('CB 信号数据超 7 天未更新')).toBeInTheDocument();
  });
});

describe('SimulationComparePage', () => {
  it('renders per-trade comparison with priority stats', async () => {
    get.mockResolvedValue({ data: comparisonResponse });
    const SimulationComparePage = (await import('../SimulationComparePage')).default;
    renderPage(<SimulationComparePage />);
    expect(await screen.findByText('模拟笔数')).toBeInTheDocument();
    expect(screen.getByText('SH113062')).toBeInTheDocument();
    expect(screen.getByText('127.8')).toBeInTheDocument();
    expect(screen.getByText('-0.16%')).toBeInTheDocument();
    expect(screen.getByText('未回填')).toBeInTheDocument();
  });
});

describe('SignalStatsPage', () => {
  it('renders stats cards', async () => {
    get.mockResolvedValue({
      data: {
        engine_version: 'v1', total: 12, completed: 10, unable: 2,
        hit: 6, miss: 3, neutral: 1, hit_rate_pct: 60.0,
        avg_stock_return_pct: 1.25, unable_reasons: {},
      },
    });
    const SignalStatsPage = (await import('../SignalStatsPage')).default;
    renderPage(<SignalStatsPage />);
    expect(await screen.findByText('信号总数')).toBeInTheDocument();
    expect(screen.getByText('60.0%')).toBeInTheDocument();
    expect(screen.getByText('1.25%')).toBeInTheDocument();
  });
});

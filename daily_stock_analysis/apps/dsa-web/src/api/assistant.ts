/**
 * 半自动交易助手视图 API（蓝图 ③-C）：健康视图 / 模拟vs真实对比 / 操作单列表
 */

import apiClient from './index';

export interface AssistantSignalHealth {
  available: boolean;
  reason?: string;
  pit_end?: string;
  age_days?: number | null;
  stale?: boolean;
  n_targets?: number;
  latest_rebalance?: string | null;
}

export interface AssistantHealthView {
  date: string;
  signal: AssistantSignalHealth;
  push: {
    bus_count: number;
    holding_related_count: number;
    digest_sent: boolean;
  };
  order_sheet: {
    exists: boolean;
    sheet_id?: string;
    status?: string;
    n_orders?: number;
    n_holds?: number;
    n_fills?: number;
    total_buy_value?: number;
    total_sell_value?: number;
  };
  active_signals: { count: number | null; error?: string };
  anomalies: string[];
  healthy: boolean;
}

export interface SimulationComparisonRow {
  trade_date: string;
  symbol: string;
  direction: string;
  sim_quantity: number;
  sim_price: number;
  sim_cost: number;
  real_price: number | null;
  real_cost: number | null;
  real_quantity?: number;
  matched: boolean;
}

export interface SimulationComparison {
  rows: SimulationComparisonRow[];
  n_sim: number;
  n_matched: number;
  n_unmatched: number;
  avg_price_diff_pct: number | null;
  comparison_date: string;
}

export interface OrderSheetSummary {
  sheet_id: string;
  trade_date: string;
  status: string;
  n_orders: number;
  total_buy_value?: number;
  total_sell_value?: number;
}

export const assistantApi = {
  getHealthView: () =>
    apiClient.get<Record<string, unknown>>('/api/v1/assistant/health-view'),
  getComparison: () =>
    apiClient.get<Record<string, unknown>>('/api/v1/assistant/comparison'),
  getOrderSheets: (limit = 10) =>
    apiClient.get<Record<string, unknown>>('/api/v1/assistant/order-sheets', {
      params: { limit },
    }),
};

import apiClient from './index';

export interface HealthResponse {
  status: string;
  timestamp: string;
}

export interface ComponentStatus {
  status: string;
  message: string;
  [key: string]: unknown;
}

export interface DetailedHealthResponse {
  status: string;
  timestamp: string;
  components: Record<string, ComponentStatus>;
  latency_ms: number;
}

export const systemStatusApi = {
  getHealth: async (): Promise<HealthResponse> => {
    const response = await apiClient.get<HealthResponse>('/api/v1/health');
    return response.data;
  },

  getDetailedHealth: async (): Promise<DetailedHealthResponse> => {
    const response = await apiClient.get<DetailedHealthResponse>('/api/v1/health/detailed');
    return response.data;
  },
};
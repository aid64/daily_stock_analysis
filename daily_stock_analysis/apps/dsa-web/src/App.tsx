import type React from 'react';
import { lazy, useEffect } from 'react';
import { BrowserRouter, HashRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ApiErrorAlert } from './components/common';
import {
  PageLoadingFallback,
  RouteOutletBoundary,
  StandaloneRouteBoundary,
} from './components/layout/RouteBoundary';
import { EditorialShell } from './components/layout/EditorialShell';
import { DesktopTitleBar } from './components/layout/DesktopTitleBar';
import { BackendGate } from './components/BackendGate';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { UiLanguageProvider, useUiLanguage } from './contexts/UiLanguageContext';
import { useAgentChatStore } from './stores/agentChatStore';
import { isDesktopRuntime } from './utils/desktop';
import './App.css';

// 桌面端打包模式下前端通过 file:// 加载，BrowserRouter 的 pushState 会破坏
// 文件路径；改用 HashRouter（基于 location.hash）保证刷新与路由跳转稳定。
// Web 端仍用 BrowserRouter，保留干净的 URL 形态。
const Router = isDesktopRuntime ? HashRouter : BrowserRouter;

const HomePage = lazy(() => import('./pages/HomePage'));
const BacktestPage = lazy(() => import('./pages/BacktestPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'));
const ChatPage = lazy(() => import('./pages/ChatPage'));
const PortfolioPage = lazy(() => import('./pages/PortfolioPage'));
const DecisionSignalsPage = lazy(() => import('./pages/DecisionSignalsPage'));
const AlertsPage = lazy(() => import('./pages/AlertsPage'));
const TokenUsagePage = lazy(() => import('./pages/TokenUsagePage'));
const StockScreeningPage = lazy(() => import('./pages/StockScreeningPage'));

const AppContent: React.FC = () => {
  const location = useLocation();
  const { authEnabled, loggedIn, isLoading, loadError, refreshStatus } = useAuth();
  const { t } = useUiLanguage();

  useEffect(() => {
    useAgentChatStore.getState().setCurrentRoute(location.pathname);
  }, [location.pathname]);

  if (isLoading) {
    return <PageLoadingFallback />;
  }

  if (loadError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-base px-4">
        <div className="w-full max-w-lg">
          <ApiErrorAlert error={loadError} />
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => void refreshStatus()}
        >
          {t('common.retry')}
        </button>
      </div>
    );
  }

  if (authEnabled && !loggedIn) {
    if (location.pathname === '/login') {
      return (
        <StandaloneRouteBoundary>
          <LoginPage />
        </StandaloneRouteBoundary>
      );
    }
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  if (location.pathname === '/login') {
    return <Navigate to="/" replace />;
  }

  return (
    <Routes>
      <Route
        element={(
          <EditorialShell>
            <RouteOutletBoundary />
          </EditorialShell>
        )}
      >
        <Route path="/" element={<HomePage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/decision-signals" element={<DecisionSignalsPage />} />
        <Route path="/screening" element={<StockScreeningPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/usage" element={<TokenUsagePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
};

const DesktopTitleBarShell: React.FC = () => {
  useEffect(() => {
    if (!isDesktopRuntime) return;
    document.documentElement.classList.add('desktop-runtime');
    return () => {
      document.documentElement.classList.remove('desktop-runtime');
    };
  }, []);

  if (!isDesktopRuntime) return null;
  return <DesktopTitleBar />;
};

const App: React.FC = () => {
  return (
    <UiLanguageProvider>
      <DesktopTitleBarShell />
      <BackendGate>
        <Router>
          <AuthProvider>
            <AppContent />
          </AuthProvider>
        </Router>
      </BackendGate>
    </UiLanguageProvider>
  );
};

export default App;

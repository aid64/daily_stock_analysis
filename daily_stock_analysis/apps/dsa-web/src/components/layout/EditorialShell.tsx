/**
 * EditorialShell — 财经编辑风格布局容器。
 *
 * 设计意图：
 * - 左侧窄侧边栏（品牌 + 域分组导航 + 底部设置）
 * - 右侧宽内容区（编辑性留白，最大宽度约束）
 * - 告别毛玻璃卡片，用 0.5px 细线和暖灰表面分层
 */
import type React from 'react';
import { useState, useEffect } from 'react';
import { Outlet, NavLink } from 'react-router-dom';
import {
  Home, MessageSquareQuote, Search,
  BriefcaseBusiness, Activity, BarChart3,
  Bell, Gauge, Settings2, Menu,
} from 'lucide-react';
import { useUiLanguage } from '@/contexts/UiLanguageContext';
import { useAgentChatStore } from '@/stores/agentChatStore';
import { StatusDot } from '@/components/common/StatusDot';
import { ThemeToggle } from '@/components/theme/ThemeToggle';
import type { UiTextKey } from '@/i18n/uiText';

const NAV_GROUPS = [
  {
    labelKey: 'layout.group.analysis',
    items: [
      { key: 'home', labelKey: 'layout.nav.home', to: '/', icon: Home, exact: true },
      { key: 'chat', labelKey: 'layout.nav.chat', to: '/chat', icon: MessageSquareQuote, badge: 'completion' as const },
      { key: 'screening', labelKey: 'layout.nav.screening', to: '/screening', icon: Search },
    ],
  },
  {
    labelKey: 'layout.group.trading',
    items: [
      { key: 'portfolio', labelKey: 'layout.nav.portfolio', to: '/portfolio', icon: BriefcaseBusiness },
      { key: 'signals', labelKey: 'layout.nav.decisionSignals', to: '/decision-signals', icon: Activity },
      { key: 'backtest', labelKey: 'layout.nav.backtest', to: '/backtest', icon: BarChart3 },
    ],
  },
  {
    labelKey: 'layout.group.system',
    items: [
      { key: 'alerts', labelKey: 'layout.nav.alerts', to: '/alerts', icon: Bell },
      { key: 'usage', labelKey: 'layout.nav.usage', to: '/usage', icon: Gauge },
      { key: 'settings', labelKey: 'layout.nav.settings', to: '/settings', icon: Settings2 },
    ],
  },
];

export const EditorialShell: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  const { t } = useUiLanguage();
  const completionBadge = useAgentChatStore((s) => s.completionBadge);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (!mobileOpen) return;
    const onResize = () => { if (window.innerWidth >= 1024) setMobileOpen(false); };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [mobileOpen]);

  const sidebar = (
    <div className="editorial-sidebar">
      <a href="/" className="editorial-brand" aria-label="DSA Home">DSA</a>

      <nav aria-label={t('layout.mainNav')} style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
        {NAV_GROUPS.map((group) => (
          <div key={group.labelKey} className="editorial-nav-group">
            <div className="editorial-nav-group-label">{t(group.labelKey as UiTextKey)}</div>
            {group.items.map((item) => (
              <NavLink
                key={item.key}
                to={item.to}
                end={item.exact}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  `editorial-nav-link${isActive ? ' active' : ''}`
                }
              >
                <item.icon />
                <span>{t(item.labelKey as UiTextKey)}</span>
                {item.badge === 'completion' && completionBadge ? (
                  <StatusDot tone="info" style={{ marginLeft: 'auto' }} />
                ) : null}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="editorial-nav-spacer" />

      <div style={{ borderTop: '1px solid hsl(var(--ed-border) / 0.5)', paddingTop: '0.75rem' }}>
        <ThemeToggle
          variant="nav"
          wrapperClassName="w-full"
          triggerClassName="editorial-nav-link"
        />
      </div>
    </div>
  );

  return (
    <div className="editorial-shell">
      {/* Desktop sidebar */}
      <div style={{ display: 'contents' }}>
        {sidebar}
      </div>

      {/* Mobile hamburger */}
      <button
        type="button"
        onClick={() => setMobileOpen(true)}
        className="editorial-mobile-menu-btn"
        aria-label={t('layout.openNav')}
        style={{
          display: 'none',
          position: 'fixed',
          top: 12,
          left: 12,
          zIndex: 50,
          background: 'hsl(var(--ed-elevated))',
          border: '0.5px solid hsl(var(--ed-border))',
          borderRadius: 8,
          padding: '8px 10px',
          cursor: 'pointer',
        }}
      >
        <Menu size={20} />
      </button>

      {/* Main content */}
      <main className="editorial-main editorial-fade-in">
        {children ?? <Outlet />}
      </main>

      {/* Mobile drawer */}
      {mobileOpen ? (
        <>
          <div
            style={{
              position: 'fixed', inset: 0, zIndex: 40,
              background: 'hsl(var(--ed-bg) / 0.8)', backdropFilter: 'blur(4px)',
            }}
            onClick={() => setMobileOpen(false)}
          />
          <div
            style={{
              position: 'fixed', left: 0, top: 0, bottom: 0, zIndex: 45,
              width: 240, background: 'hsl(var(--ed-surface))',
              boxShadow: '0 0 24px hsl(30 8% 6% / 0.08)',
            }}
          >
            {sidebar}
          </div>
        </>
      ) : null}
    </div>
  );
};

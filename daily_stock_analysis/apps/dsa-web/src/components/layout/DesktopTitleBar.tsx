/**
 * DesktopTitleBar — 桌面端自绘标题栏。
 *
 * 替代 Windows 原生 titleBarOverlay，与 EditorialShell 主题融合。
 * - 整栏可拖拽（-webkit-app-region: drag），按钮/切换器显式 no-drag
 * - macOS 保留原生交通灯（hiddenInset），仅渲染品牌 + 工具区，不自绘窗口控制按钮
 * - Windows/Linux 自绘最小化/最大化/关闭按钮
 */
import type React from 'react';
import { useEffect, useState } from 'react';
import { Minus, Square, Copy, X } from 'lucide-react';
import { ThemeToggle } from '@/components/theme/ThemeToggle';
import { UiLanguageToggle } from '@/components/i18n/UiLanguageToggle';
import { desktopWindowControls, isMacDesktopRuntime } from '@/utils/desktop';

export const DesktopTitleBar: React.FC = () => {
  const [isMaximized, setIsMaximized] = useState(false);
  const isMac = isMacDesktopRuntime();

  useEffect(() => {
    if (!desktopWindowControls) return;
    void desktopWindowControls.isMaximized().then(setIsMaximized);
    const unsubscribe = desktopWindowControls.onMaximizeChange((maximized) => {
      setIsMaximized(maximized);
    });
    return () => {
      if (unsubscribe) unsubscribe();
    };
  }, []);

  const handleMinimize = () => {
    void desktopWindowControls?.minimize();
  };
  const handleToggleMaximize = () => {
    void desktopWindowControls?.toggleMaximize();
  };
  const handleClose = () => {
    void desktopWindowControls?.close();
  };

  return (
    <header className={`desktop-titlebar${isMac ? ' is-mac' : ''}`} role="banner">
      <div className="desktop-titlebar-left">
        <span className="desktop-titlebar-brand">DSA</span>
      </div>
      <div className="desktop-titlebar-right">
        <ThemeToggle
          variant="default"
          triggerClassName="desktop-titlebar-icon-btn"
          iconClassName="h-4 w-4"
        />
        <UiLanguageToggle
          variant="default"
          triggerClassName="desktop-titlebar-icon-btn"
          iconClassName="h-4 w-4"
        />
        {!isMac && (
          <div className="desktop-titlebar-controls">
            <button
              type="button"
              className="desktop-titlebar-icon-btn"
              onClick={handleMinimize}
              aria-label="Minimize"
              title="Minimize"
            >
              <Minus size={15} />
            </button>
            <button
              type="button"
              className="desktop-titlebar-icon-btn"
              onClick={handleToggleMaximize}
              aria-label={isMaximized ? 'Restore' : 'Maximize'}
              title={isMaximized ? 'Restore' : 'Maximize'}
            >
              {isMaximized ? <Copy size={13} /> : <Square size={13} />}
            </button>
            <button
              type="button"
              className="desktop-titlebar-icon-btn desktop-titlebar-close"
              onClick={handleClose}
              aria-label="Close"
              title="Close"
            >
              <X size={16} />
            </button>
          </div>
        )}
      </div>
    </header>
  );
};
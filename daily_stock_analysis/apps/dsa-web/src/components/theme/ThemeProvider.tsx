import type React from 'react';
import { useThemeStore } from '@/stores/themeStore';

type ThemeProviderProps = {
  children: React.ReactNode;
};

export const ThemeProvider: React.FC<ThemeProviderProps> = ({ children }) => {
  // 首次渲染时确保主题已应用
  useThemeStore.getState()._sync();
  return <>{children}</>;
};

/**
 * 主题 Store — 轻量 zustand 替代 next-themes (Next.js 专用库)。
 * 支持 light / dark / system 三模式，持久化到 localStorage。
 */
import { create } from 'zustand';

type ThemeMode = 'light' | 'dark' | 'system';
type ResolvedTheme = 'light' | 'dark';

interface ThemeState {
  theme: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: ThemeMode) => void;
  _sync: () => void;
}

const STORAGE_KEY = 'dsa-theme';

function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getStoredTheme(): ThemeMode {
  if (typeof window === 'undefined') return 'system';
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  return 'system';
}

function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === 'system') return getSystemTheme();
  return mode;
}

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.remove('light', 'dark');
  root.classList.add(resolved);
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: getStoredTheme(),
  resolvedTheme: resolveTheme(getStoredTheme()),

  setTheme: (theme: ThemeMode) => {
    localStorage.setItem(STORAGE_KEY, theme);
    const resolved = resolveTheme(theme);
    applyTheme(resolved);
    set({ theme, resolvedTheme: resolved });
  },

  _sync: () => {
    const resolved = resolveTheme(get().theme);
    applyTheme(resolved);
    set({ resolvedTheme: resolved });
  },
}));

// 初始化：应用主题到 DOM
if (typeof window !== 'undefined') {
  applyTheme(resolveTheme(getStoredTheme()));

  // 监听系统主题变化
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  mq.addEventListener('change', () => {
    useThemeStore.getState()._sync();
  });
}

// 导出便捷 Hook (next-themes 兼容 API)
export function useTheme() {
  return useThemeStore();
}

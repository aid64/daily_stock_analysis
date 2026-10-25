import { configDefaults, defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
    // Page-level suites mount large trees and can exceed the 5s default under
    // CI/parallel load; keep the limit generous so timeouts signal real hangs.
    testTimeout: 20000,
    hookTimeout: 20000,
    // Large page/drawer chunks otherwise starve each other on many-core
    // machines and in CI, producing lazy-import timeouts.
    maxWorkers: 4,
    minWorkers: 1,
    exclude: [...configDefaults.exclude, 'e2e/**', 'playwright.config.ts'],
  },
});

// @vitest-environment node

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('index.html theme bootstrap', () => {
  it('preloads dark mode before React mounts and respects stored theme values', () => {
    const indexHtml = readFileSync(resolve(__dirname, '..', 'index.html'), 'utf8');

    expect(indexHtml).toContain("localStorage.getItem('dsa-theme')");
    expect(indexHtml).toContain("stored === 'light' || stored === 'dark'");
    expect(indexHtml).toContain("window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'");
    expect(indexHtml).toContain("root.classList.remove('light', 'dark');");
    expect(indexHtml).toContain('root.classList.add(theme);');
    expect(indexHtml).toContain('root.style.colorScheme = theme;');
  });
});

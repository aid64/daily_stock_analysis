// @vitest-environment node

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const REQUIRED_LOGIN_TOKENS = [
  '--login-button-text',
  '--login-label-text',
  '--login-hint-text',
  '--login-input-icon',
  '--login-input-toggle-bg',
  '--login-input-toggle-border',
  '--login-input-toggle-text',
  '--login-input-toggle-border-hover',
  '--login-input-toggle-bg-hover',
  '--login-input-toggle-text-hover',
  '--login-input-toggle-ring',
  '--login-input-toggle-active-bg',
  '--login-input-toggle-active-border',
  '--login-input-toggle-active-text',
];

function collectBlocks(css: string, pattern: RegExp): string {
  const blocks: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(css)) !== null) {
    blocks.push(match[1]);
  }
  return blocks.join('\n');
}

describe('login theme tokens', () => {
  it('defines all login-specific tokens in the light theme root block', () => {
    const css = readFileSync(resolve(__dirname, '..', 'src', 'index.css'), 'utf8')
      + readFileSync(resolve(__dirname, '..', 'src', 'styles', 'editorial-theme.css'), 'utf8');
    const rootBlock = collectBlocks(css, /:root\s*\{([\s\S]*?)\n\}/g);

    expect(rootBlock).not.toBe('');
    for (const token of REQUIRED_LOGIN_TOKENS) {
      expect(rootBlock).toContain(token);
    }
  });

  it('defines all login-specific tokens in the dark theme block', () => {
    const css = readFileSync(resolve(__dirname, '..', 'src', 'index.css'), 'utf8')
      + readFileSync(resolve(__dirname, '..', 'src', 'styles', 'editorial-theme.css'), 'utf8');
    const darkBlock = collectBlocks(css, /\.dark\s*\{([\s\S]*?)\n\}/g);

    expect(darkBlock).not.toBe('');
    for (const token of REQUIRED_LOGIN_TOKENS) {
      expect(darkBlock).toContain(token);
    }
  });
});

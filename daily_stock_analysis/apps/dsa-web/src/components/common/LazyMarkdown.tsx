import type React from 'react';
import { Suspense, lazy } from 'react';

// `react-markdown` + `remark-gfm` together form a large (~145KB) vendor chunk
// that is only needed once the user actually views a markdown-rendered report
// or chat message. Lazy-loading a single inner module that imports both keeps
// `vendor-markdown` out of every route's initial chunk and only loads it on
// first render. See desktop-startup-perf L7.3.
const MarkdownRenderer = lazy(() => import('./MarkdownRenderer'));

interface LazyMarkdownProps {
  content: string;
  fallback?: React.ReactNode;
}

/**
 * Render markdown content with lazy-loaded react-markdown + remark-gfm.
 *
 * The fallback defaults to a plain `<pre>` so callers don't need to wire up a
 * Suspense boundary of their own; the raw content stays visible (just
 * unstyled) while the markdown parser chunk downloads.
 */
export const LazyMarkdown: React.FC<LazyMarkdownProps> = ({
  content,
  fallback,
}) => {
  const resolvedFallback = fallback ?? (
    <pre className="whitespace-pre-wrap break-words text-sm text-secondary-text">
      {content}
    </pre>
  );
  return (
    <Suspense fallback={resolvedFallback}>
      <MarkdownRenderer content={content} />
    </Suspense>
  );
};

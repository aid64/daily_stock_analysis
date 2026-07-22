import type React from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MarkdownRendererProps {
  content: string;
}

/**
 * Default-exported inner renderer used by `LazyMarkdown`.
 *
 * Kept in its own module so `react-markdown` + `remark-gfm` (+ their transitive
 * deps) land in a single dynamic import chunk instead of every route's initial
 * chunk. See desktop-startup-perf L7.3.
 */
const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content }) => (
  <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
);

export default MarkdownRenderer;

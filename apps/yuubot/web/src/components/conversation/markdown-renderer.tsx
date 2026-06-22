import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import { markdownPlugins } from "./markdown-renderer.ts";

export { markdownPlugins };

// Bordered table rendering for GFM pipe tables. The default `prose` styling
// strips cell borders entirely, so column-aligned content of varying lengths
// (the common LLM output) becomes unreadable — without borders it's unclear
// which cell belongs to which row. Override the table family with border
// utilities that work in both light and dark themes.
const tableComponents: Components = {
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm border border-border">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-muted/60">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="border border-border px-3 py-1.5 font-semibold align-top">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-border px-3 py-1.5 align-top">{children}</td>
  ),
};

export function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={markdownPlugins.remark}
        rehypePlugins={markdownPlugins.rehype}
        components={tableComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

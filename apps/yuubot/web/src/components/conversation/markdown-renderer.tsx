import ReactMarkdown from "react-markdown";
import { markdownPlugins } from "./markdown-renderer.ts";

export { markdownPlugins };

export function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={markdownPlugins.remark}
        rehypePlugins={markdownPlugins.rehype}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

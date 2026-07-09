import type { AnchorHTMLAttributes } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import { markdownPlugins } from "./markdown-renderer.ts";
import { WorkspaceRefView } from "./workspace-ref-view";
import { parseWorkspaceRefs } from "@/shared/lib/workspace-ref";

export { markdownPlugins };

// Bordered table rendering for GFM pipe tables. The default `prose` styling
// strips cell borders entirely, so column-aligned content of varying lengths
// (the common LLM output) becomes unreadable — without borders it's unclear
// which cell belongs to which row. Override the table family with border
// utilities that work in both light and dark themes.
function markdownLinkProps(href: string | undefined): AnchorHTMLAttributes<HTMLAnchorElement> {
  if (!href) {
    return {};
  }
  return {
    href,
    target: "_blank",
    rel: "noopener noreferrer",
  };
}

const markdownComponents: Components = {
  a: ({ href, children }) => (
    <a className="text-primary underline underline-offset-2" {...markdownLinkProps(href)}>
      {children}
    </a>
  ),
};

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

export function MarkdownRenderer({
  content,
  actorId = "",
  workspacePath: _workspacePath,
}: {
  content: string;
  actorId?: string;
  workspacePath?: string | null;
}) {
  const segments = parseWorkspaceRefs(content);
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none break-words whitespace-normal leading-[1.6] [&>:first-child]:mt-0 [&>:last-child]:mb-0 [&_blockquote]:my-2 [&_h1]:mt-4 [&_h1]:mb-2 [&_h2]:mt-4 [&_h2]:mb-2 [&_h3]:mt-3 [&_h3]:mb-2 [&_li]:my-1 [&_ol]:my-2 [&_p]:my-2 [&_pre]:whitespace-pre-wrap [&_ul]:my-2">
      {segments.map((segment, index) => segment.type === "text" ? (
        <ReactMarkdown
          key={index}
          remarkPlugins={markdownPlugins.remark}
          rehypePlugins={markdownPlugins.rehype}
          components={{ ...markdownComponents, ...tableComponents }}
        >
          {segment.value}
        </ReactMarkdown>
      ) : (
        <WorkspaceRefView key={index} actorId={actorId} path={segment.path} />
      ))}
    </div>
  );
}

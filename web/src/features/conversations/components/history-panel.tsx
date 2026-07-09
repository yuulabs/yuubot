import { Button } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/conversation/markdown-renderer.tsx";
import { formatWorkspaceRef } from "@/shared/lib/workspace-ref";
import type { HistoryItem } from "@/shared/types/api";
import { EmptyState, Panel } from "@/shared/components";
import type { ReactNode } from "react";

export function HistoryPanel({
  history,
  onReload,
}: {
  history: HistoryItem[];
  onReload: () => void;
}) {
  return (
    <Panel>
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Transcript</h2>
        <Button variant="outline" size="sm" onClick={onReload}>Reload</Button>
      </div>
      {!history.length ? <EmptyState>No history.</EmptyState> : (
        <div className="grid gap-3">
          {history.map((item) => <HistoryEntry key={item.seq} item={item} />)}
        </div>
      )}
    </Panel>
  );
}

function HistoryEntry({ item }: { item: HistoryItem }) {
  const rendered = renderHistory(item);
  return (
    <article className="rounded border p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>#{item.seq}</span>
        <span>{rendered.label}</span>
        {item.created_at && <span>{item.created_at}</span>}
      </div>
      {rendered.markdown ? <MarkdownRenderer content={rendered.markdown} /> : rendered.node}
      {rendered.raw && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted-foreground">Raw</summary>
          <pre className="mt-2 overflow-auto rounded border p-3 text-xs">{JSON.stringify(item.payload, null, 2)}</pre>
        </details>
      )}
    </article>
  );
}

function renderHistory(item: HistoryItem): { label: string; markdown?: string; node?: ReactNode; raw?: boolean } {
  if (item.kind === "input") {
    return { label: String(item.payload.role ?? "input"), markdown: contentText(item.payload.content), raw: true };
  }
  if (item.kind === "gen_text") {
    return { label: "assistant", markdown: String(item.payload.text ?? ""), raw: true };
  }
  if (item.kind === "gen_tool_call") {
    return {
      label: `tool call ${String(item.payload.name ?? "")}`,
      node: <pre className="overflow-auto rounded border p-3 text-xs">{JSON.stringify(item.payload, null, 2)}</pre>,
    };
  }
  if (item.kind === "tool_result") {
    return { label: "tool result", markdown: contentText(item.payload.content), raw: true };
  }
  if (item.kind === "cost") {
    return {
      label: "cost",
      node: <pre className="overflow-auto rounded border p-3 text-xs">{JSON.stringify(item.payload, null, 2)}</pre>,
    };
  }
  return {
    label: item.kind,
    node: <pre className="overflow-auto rounded border p-3 text-xs">{JSON.stringify(item.payload, null, 2)}</pre>,
  };
}

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map((item) => {
    if (!item || typeof item !== "object") return "";
    const payload = item as Record<string, unknown>;
    if (typeof payload.text === "string") return payload.text;
    if (typeof payload.path === "string") return formatWorkspaceRef(payload.path);
    if (typeof payload.url === "string") return `[${String(payload.kind ?? "url")}: ${payload.url}]`;
    return "";
  }).filter(Boolean).join("\n\n");
}

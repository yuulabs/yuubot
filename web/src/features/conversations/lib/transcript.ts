import { formatWorkspaceRef } from "@/shared/lib/workspace-ref";

export function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      const payload = item as Record<string, unknown>;
      if (typeof payload.text === "string") return payload.text;
      if (typeof payload.path === "string") return formatWorkspaceRef(payload.path);
      if (typeof payload.url === "string") return `[${String(payload.kind ?? "url")}: ${payload.url}]`;
      return "";
    })
    .filter(Boolean)
    .join("\n\n");
}

export function sumConversationTokens(items: Array<{ usage?: Record<string, unknown> }>): number {
  return items.reduce((total, item) => {
    const usage = item.usage ?? {};
    return total
      + numeric(usage.input_tokens)
      + numeric(usage.cached_input_tokens)
      + numeric(usage.cache_write_tokens)
      + numeric(usage.output_tokens);
  }, 0);
}

function numeric(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

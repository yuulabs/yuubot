export function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      const payload = item as Record<string, unknown>;
      if (typeof payload.text === "string") return payload.text;
      if (typeof payload.path === "string") return `[${String(payload.kind ?? "file")}: ${payload.path}]`;
      if (typeof payload.url === "string") return `[${String(payload.kind ?? "url")}: ${payload.url}]`;
      return "";
    })
    .filter(Boolean)
    .join("\n\n");
}

export function sumConversationCost(items: Array<{ usage?: Record<string, unknown> }>): number {
  return items.reduce((total, item) => {
    const usage = item.usage ?? {};
    const cost = usage.payg_cost ?? usage.cost_usd ?? usage.total_cost_usd ?? usage.usd;
    return total + (typeof cost === "number" ? cost : 0);
  }, 0);
}

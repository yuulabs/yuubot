import type { WsContentItem } from "@/shared/lib/api";

export interface ConversationPendingSend {
  actorId: string;
  content: WsContentItem[];
}

declare module "@tanstack/react-router" {
  interface HistoryState {
    pendingSend?: ConversationPendingSend;
  }
}

export function parsePendingSend(state: unknown): ConversationPendingSend | null {
  if (!state || typeof state !== "object") {
    return null;
  }
  const pending = (state as Record<string, unknown>).pendingSend;
  if (!pending || typeof pending !== "object") {
    return null;
  }
  const { actorId, content } = pending as Record<string, unknown>;
  if (typeof actorId !== "string" || !actorId) {
    return null;
  }
  if (!Array.isArray(content) || content.length === 0) {
    return null;
  }
  return { actorId, content: content as WsContentItem[] };
}

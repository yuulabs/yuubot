import type {
  ConversationUsageRecord,
  ConversationHistoryResponse,
  ConversationSummary,
  HistoryItem,
  ItemsResponse,
} from "@/shared/types/api";
import { BASE, request } from "./client";

export const CONVERSATION_HISTORY_PAGE_SIZE = 200;

export function getConversations(): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>(`${BASE}/conversations`);
}

export function createConversation(actorId: string): Promise<{ conversation_id: string }> {
  return request<{ conversation_id: string }>(`${BASE}/conversations`, {
    method: "POST",
    body: JSON.stringify({ actor_id: actorId }),
  });
}

export interface ConversationDetail {
  id: string;
  active: boolean;
  status?: string;
  actor_id?: string;
  message_count?: number;
  last_active_at?: string | null;
  last_error?: unknown;
}

export interface ConversationHistoryQuery {
  after_seq?: number;
  before_seq?: number;
  limit?: number;
}

export function getConversation(conversationId: string): Promise<ConversationDetail> {
  return request<ConversationDetail>(`${BASE}/conversations/${encodeURIComponent(conversationId)}`);
}

export function getConversationHistory(
  conversationId: string,
  query: ConversationHistoryQuery = {},
): Promise<ConversationHistoryResponse> {
  const params = new URLSearchParams();
  if (query.after_seq !== undefined) {
    params.set("after_seq", String(query.after_seq));
  }
  if (query.before_seq !== undefined) {
    params.set("before_seq", String(query.before_seq));
  }
  if (query.limit !== undefined) {
    params.set("limit", String(query.limit));
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return request<ConversationHistoryResponse>(
    `${BASE}/conversations/${encodeURIComponent(conversationId)}/history${suffix}`,
  );
}

export function deleteConversation(conversationId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

export function getConversationUsage(conversationId: string): Promise<ItemsResponse<ConversationUsageRecord>> {
  return request<ItemsResponse<ConversationUsageRecord>>(`${BASE}/conversations/${encodeURIComponent(conversationId)}/usage`);
}

export function mergeHistoryItems(current: HistoryItem[], incoming: HistoryItem[]): HistoryItem[] {
  const bySeq = new Map<number, HistoryItem>();
  for (const item of current) {
    bySeq.set(item.seq, item);
  }
  for (const item of incoming) {
    bySeq.set(item.seq, item);
  }
  return [...bySeq.values()].sort((left, right) => left.seq - right.seq);
}

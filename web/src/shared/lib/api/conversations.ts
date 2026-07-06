import type { ConversationCostRecord, HistoryItem, ItemsResponse } from "@/shared/types/api";
import { BASE, request } from "./client";

export function getConversationHistory(conversationId: string): Promise<HistoryItem[]> {
  return request<ItemsResponse<HistoryItem>>(`${BASE}/conversations/${encodeURIComponent(conversationId)}/history`).then((res) => res.items);
}

export function deleteConversation(conversationId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`${BASE}/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

export function getConversationCosts(conversationId: string): Promise<ItemsResponse<ConversationCostRecord>> {
  return request<ItemsResponse<ConversationCostRecord>>(`${BASE}/conversations/${encodeURIComponent(conversationId)}/costs`);
}

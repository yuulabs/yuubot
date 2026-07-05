import type { ConversationCostRecord, ConversationSummary, HistoryItem, ItemsResponse } from "@/shared/types/api";
import { BASE, request } from "./client";

export function listConversations(): Promise<ConversationSummary[]> {
  return request<ItemsResponse<ConversationSummary>>(`${BASE}/conversations`).then((res) => res.items);
}

export function getConversation(conversationId: string): Promise<ConversationSummary> {
  return request<ConversationSummary>(`${BASE}/conversations/${encodeURIComponent(conversationId)}`);
}

export function getConversationHistory(conversationId: string): Promise<HistoryItem[]> {
  return request<ItemsResponse<HistoryItem>>(`${BASE}/conversations/${encodeURIComponent(conversationId)}/history`).then((res) => res.items);
}

export function deleteConversation(conversationId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`${BASE}/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

export function getConversationCosts(conversationId: string): Promise<ItemsResponse<ConversationCostRecord>> {
  return request<ItemsResponse<ConversationCostRecord>>(`${BASE}/conversations/${encodeURIComponent(conversationId)}/costs`);
}

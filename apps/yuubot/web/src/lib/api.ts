/** API client functions for yuubot admin backend.
 *
 * Requests are proxied by vite to http://127.0.0.1:8781 in dev, or served
 * from the same origin in production.
 */

import type {
  ConversationListItem,
  ConversationListResponse,
  ConversationCreateResponse,
  ConversationData,
  ConversationMessagesResponse,
  CancelTurnResponse,
  ConversationMessage,
  HealthResponse,
  IntegrationKind,
  ListResponse,
  LiveCapability,
  LiveCapabilitiesResponse,
  ResourceType,
  SingleResponse,
  ErrorResponse,
  SendMessageResponse,
} from "@/types/api";

const BASE = "/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(errorMessage(body, response.status));
  }
  return response.json() as Promise<T>;
}

function errorMessage(body: unknown, status: number): string {
  if (!body || typeof body !== "object") {
    return `HTTP ${status}`;
  }
  const error = body as ErrorResponse;
  const detail = error.detail ?? error.reason;
  if (!detail) {
    return `HTTP ${status}`;
  }
  return error.hint ? `${detail} ${error.hint}` : detail;
}

// ---------------------------------------------------------------------------
// Resource CRUD (daemon API)
// ---------------------------------------------------------------------------

export async function listResources<T>(
  resourceType: ResourceType,
): Promise<T[]> {
  const res = await request<ListResponse<T>>(
    `${BASE}/resources/${resourceType}`,
  );
  return res.data;
}

export async function createResource<T>(
  resourceType: ResourceType,
  data: unknown,
): Promise<T> {
  const res = await request<SingleResponse<T>>(
    `${BASE}/resources/${resourceType}`,
    { method: "POST", body: JSON.stringify(data) },
  );
  return res.data;
}

export async function updateResource<T>(
  resourceType: ResourceType,
  id: string,
  data: unknown,
): Promise<T> {
  const res = await request<SingleResponse<T>>(
    `${BASE}/resources/${resourceType}/${id}`,
    { method: "PUT", body: JSON.stringify(data) },
  );
  return res.data;
}

export async function deleteResource(
  resourceType: ResourceType,
  id: string,
): Promise<void> {
  await request<void>(`${BASE}/resources/${resourceType}/${id}`, {
    method: "DELETE",
  });
}

export async function setResourceEnabled<T>(
  resourceType: ResourceType,
  id: string,
  enabled: boolean,
): Promise<T> {
  const action = enabled ? "enable" : "disable";
  const res = await request<SingleResponse<T>>(
    `${BASE}/resources/${resourceType}/${id}/${action}`,
    { method: "POST" },
  );
  return res.data;
}

// ---------------------------------------------------------------------------
// Admin meta endpoints
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/healthz");
}

export async function getIntegrationKinds(): Promise<IntegrationKind[]> {
  const res = await request<{ status: string; kinds: IntegrationKind[] }>(
    `${BASE}/integration-kinds`,
  );
  return res.kinds;
}

export async function getLiveCapabilities(): Promise<LiveCapability[]> {
  const res = await request<LiveCapabilitiesResponse>(
    `${BASE}/live-capabilities`,
  );
  return res.capabilities;
}

// ---------------------------------------------------------------------------
// Admin Conversation API
// ---------------------------------------------------------------------------

export async function getConversation(
  conversationId: string,
): Promise<ConversationData | null> {
  try {
    const res = await request<ConversationCreateResponse>(
      `${BASE}/admin/conversations/${conversationId}`,
    );
    return res.data;
  } catch (error) {
    if (error instanceof Error && error.message.includes("does not exist")) {
      return null;
    }
    throw error;
  }
}

/**
 * Send a user message to a conversation.
 *
 * On the first send to a freshly-minted conversation id, callers MUST pass
 * `actorId`: the daemon creates the conversation row, binds the agent,
 * persists the prompt prefix, appends the user Message and starts the turn
 * (returning 202). Subsequent sends MUST omit `actorId` — the persisted
 * binding is authoritative and mismatched actor ids are rejected.
 */
export async function sendConversationMessage(args: {
  conversationId: string;
  text: string;
  messageId?: string;
  actorId?: string;
}): Promise<SendMessageResponse["data"]> {
  const body: Record<string, unknown> = {
    text: args.text,
    message_id: args.messageId,
  };
  if (args.actorId) {
    body.actor_id = args.actorId;
  }
  const res = await request<SendMessageResponse>(
    `${BASE}/admin/conversations/${args.conversationId}/messages`,
    { method: "POST", body: JSON.stringify(body) },
  );
  return res.data;
}

export async function listConversations(): Promise<ConversationListItem[]> {
  const res = await request<ConversationListResponse>(`${BASE}/admin/conversations`);
  return res.data;
}

export async function getConversationMessages(
  conversationId: string,
): Promise<ConversationMessage[]> {
  const res = await request<ConversationMessagesResponse>(
    `${BASE}/admin/conversations/${conversationId}/messages`,
  );
  return res.data;
}

/**
 * Flush the in-flight turn for a conversation (POST /cancel).
 *
 * The daemon's `ConversationManager.cancel_turn` sets the cancel event (a
 * single-point safety trip so a CancelledError lands even if the loop is
 * between awaits) and calls `task.cancel()`. It does NOT await the task and
 * does NOT synthesise `turn_completed`. The loop's CancelledError handler
 * runs `drain_pending(agent)`: if the pending queue was non-empty the merged
 * user message is appended and the loop continues (turn stays live, the
 * `queue.flushed` SSE event later tears down the frontend queue band); if the
 * queue was empty the loop breaks and `turn_completed` fires naturally via
 * the loop's exit path.
 *
 * Returns `{ cancelled, drained }`. `drained` is always `false` at POST time
 * — the real drain outcome is disclosed via the `queue.flushed` SSE event.
 * `cancelled` is `true` when a turn task was actually signalled.
 *
 * The frontend caller should NOT locally mutate `isSending` on this call's
 * return; rely on the SSE `turn_completed` (Flush-with-empty-queue break) or
 * the continued stream (Flush-with-pending, turn stays live) for UI teardown.
 */
export async function cancelConversationTurn(
  conversationId: string,
): Promise<CancelTurnResponse["data"]> {
  const res = await request<CancelTurnResponse>(
    `${BASE}/admin/conversations/${conversationId}/cancel`,
    { method: "POST" },
  );
  return res.data;
}

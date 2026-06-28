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
  ConversationUploadedFile,
  ConversationUploadResponse,
  CancelTurnResponse,
  ConversationMessage,
  HealthResponse,
  IntegrationKind,
  ListResponse,
  LiveCapability,
  LiveCapabilitiesResponse,
  ActorSkillsView,
  SkillInfo,
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

export async function getActorSkills(actorId: string): Promise<ActorSkillsView> {
  const res = await request<SingleResponse<ActorSkillsView>>(
    `${BASE}/actors/${actorId}/skills`,
  );
  return res.data;
}

export async function updateService(): Promise<void> {
  await request<{ status: string; detail?: string }>(`${BASE}/admin/update`, {
    method: "POST",
  });
}

export async function importActorSkill(
  actorId: string,
  name: string,
): Promise<SkillInfo> {
  const res = await request<SingleResponse<SkillInfo>>(
    `${BASE}/actors/${actorId}/skills/import`,
    { method: "POST", body: JSON.stringify({ name }) },
  );
  return res.data;
}

export async function deleteActorSkill(
  actorId: string,
  name: string,
): Promise<void> {
  await request<void>(
    `${BASE}/actors/${actorId}/skills/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
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
  uploads?: ConversationUploadedFile[];
}): Promise<SendMessageResponse["data"]> {
  const body: Record<string, unknown> = {
    text: args.text,
    message_id: args.messageId,
  };
  if (args.actorId) {
    body.actor_id = args.actorId;
  }
  if (args.uploads?.length) {
    body.uploads = args.uploads;
  }
  const res = await request<SendMessageResponse>(
    `${BASE}/admin/conversations/${args.conversationId}/messages`,
    { method: "POST", body: JSON.stringify(body) },
  );
  return res.data;
}

export async function uploadConversationFiles(args: {
  conversationId: string;
  files: File[];
  actorId?: string;
}): Promise<ConversationUploadedFile[]> {
  const body = new FormData();
  if (args.actorId) {
    body.append("actor_id", args.actorId);
  }
  for (const file of args.files) {
    body.append("files", file, file.name || "upload");
  }
  const response = await fetch(
    `${BASE}/admin/conversations/${args.conversationId}/uploads`,
    { method: "POST", body },
  );
  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorMessage(errorBody, response.status));
  }
  const res = await response.json() as ConversationUploadResponse;
  return res.data;
}

export async function listConversations(): Promise<ConversationListItem[]> {
  const res = await request<ConversationListResponse>(`${BASE}/admin/conversations`);
  return res.data;
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await request<{ status: string; data: { conversation_id: string; deleted: boolean } }>(
    `${BASE}/admin/conversations/${conversationId}`,
    { method: "DELETE" },
  );
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
 * Stop the in-flight turn for a conversation (POST /cancel).
 *
 * The daemon's `ConversationManager.cancel_turn` sets the cancel event,
 * calls `task.cancel()`, and waits briefly for cleanup. If a provider SDK or
 * tool does not cooperate with cancellation, the HTTP stop receipt still
 * returns with `pending: true`.
 *
 * Returns `{ cancelled }`. `cancelled` is `true` when a turn task was
 * actually signalled; `false` when there was no live task to cancel. No
 * `drained` field — the per-conversation pending
 * queue mechanism is gone (the input box itself is the buffer; a send while
 * generating is not reachable from the UI because the Send button is
 * replaced by the Stop button during generation).
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

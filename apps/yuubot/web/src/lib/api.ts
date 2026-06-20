/** API client functions for yuubot admin backend.
 *
 * Requests are proxied by vite to http://127.0.0.1:8781 in dev, or served
 * from the same origin in production.
 */

import type {
  ConversationListItem,
  ConversationListResponse,
  ConversationMessagesResponse,
  ConversationMessage,
  HealthResponse,
  IntegrationKind,
  ListResponse,
  LiveCapability,
  LiveCapabilitiesResponse,
  ResourceType,
  SingleResponse,
  ErrorResponse,
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

export function githubOAuthStartUrl(integrationId: string): string {
  return `${BASE}/integrations/${encodeURIComponent(integrationId)}/github/oauth/start`;
}

// ---------------------------------------------------------------------------
// Admin Conversation API
// ---------------------------------------------------------------------------

export async function createConversation(args: {
  actorId: string;
  conversationId: string;
}): Promise<import("@/types/api").ConversationData> {
  const res = await request<import("@/types/api").ConversationCreateResponse>(
    `${BASE}/admin/conversations`,
    {
      method: "POST",
      body: JSON.stringify({
        actor_id: args.actorId,
        conversation_id: args.conversationId,
      }),
    },
  );
  return res.data;
}

export async function ensureConversationAgent(args: {
  conversationId: string;
}): Promise<import("@/types/api").ConversationData> {
  const res = await request<import("@/types/api").ConversationCreateResponse>(
    `${BASE}/admin/conversations/${args.conversationId}/agents`,
    { method: "POST" },
  );
  return res.data;
}

export async function sendConversationMessage(args: {
  conversationId: string;
  text: string;
  messageId?: string;
}): Promise<import("@/types/api").SendMessageResponse["data"]> {
  const res = await request<import("@/types/api").SendMessageResponse>(
    `${BASE}/admin/conversations/${args.conversationId}/messages`,
    {
      method: "POST",
      body: JSON.stringify({ text: args.text, message_id: args.messageId }),
    },
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

import type { AuthAttempt, ItemsResponse, McpServerBody, McpServerSnapshot, McpServerState } from "@/shared/types/api";
import { BASE, request } from "./client";

export function listMcpServers(): Promise<McpServerSnapshot[]> {
  return request<ItemsResponse<McpServerSnapshot>>(`${BASE}/mcp-servers`).then((res) => res.items);
}

export function putMcpServer(serverId: string, body: McpServerBody): Promise<McpServerSnapshot[]> {
  return request<McpServerSnapshot[]>(`${BASE}/mcp-servers/${encodeURIComponent(serverId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function enableMcpServer(serverId: string): Promise<{ id: string; state: McpServerState }> {
  return request<{ id: string; state: McpServerState }>(`${BASE}/mcp-servers/${encodeURIComponent(serverId)}/enable`, { method: "POST" });
}

export function disableMcpServer(serverId: string): Promise<{ id: string; disabled: boolean }> {
  return request<{ id: string; disabled: boolean }>(`${BASE}/mcp-servers/${encodeURIComponent(serverId)}/disable`, { method: "POST" });
}

export function refreshMcpServer(serverId: string): Promise<{ id: string; state: McpServerState }> {
  return request<{ id: string; state: McpServerState }>(`${BASE}/mcp-servers/${encodeURIComponent(serverId)}/refresh`, { method: "POST" });
}

export function startMcpOAuth(serverId: string): Promise<AuthAttempt> {
  return request<AuthAttempt>(`${BASE}/mcp-servers/${encodeURIComponent(serverId)}/auth/start`, { method: "POST" });
}

export function deleteMcpServer(serverId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/mcp-servers/${encodeURIComponent(serverId)}`, { method: "DELETE" });
}

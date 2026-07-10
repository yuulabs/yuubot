import { request, BASE } from "./client";

export type InputModality = "text" | "image" | "audio" | "video";

export interface EndpointStatus {
  id: string;
  name: string;
  base_url: string;
  connected: boolean;
  models: string[];
  checked_at: string;
  last_error: string | null;
  has_api_key: boolean;
  connect_timeout_s: number;
  request_timeout_s: number;
}

export type EndpointRecord = Omit<EndpointStatus, "connected" | "has_api_key">;

export interface EndpointInput {
  name: string;
  base_url: string;
  api_key: string;
  clear_api_key: boolean;
  connect_timeout_s: number;
  request_timeout_s: number;
  refresh_models: boolean;
}

export interface AliasTarget {
  endpoint_id: string;
  model: string;
}

export interface GatewayAlias {
  id: string;
  modalities: InputModality[];
  targets: AliasTarget[];
}

export interface AliasInput {
  modalities: InputModality[];
  targets: AliasTarget[];
}

export interface GatewayStatus {
  endpoints: EndpointStatus[];
  aliases: GatewayAlias[];
  fixer_gemini_enabled: boolean;
  fixer_grok_enabled: boolean;
  fast_delegate_enabled: boolean;
  intelligent_delegate_enabled: boolean;
}

export function getGateway(): Promise<GatewayStatus> {
  return request<GatewayStatus>(`${BASE}/gateway`);
}

export function putEndpoint(id: string, input: EndpointInput): Promise<EndpointRecord> {
  return request<EndpointRecord>(`${BASE}/gateway/endpoints/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function refreshEndpoint(id: string): Promise<EndpointStatus> {
  return request<EndpointStatus>(`${BASE}/gateway/endpoints/${encodeURIComponent(id)}/refresh`, { method: "POST" });
}

export function deleteEndpoint(id: string): Promise<void> {
  return request<void>(`${BASE}/gateway/endpoints/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function putAlias(id: string, input: AliasInput): Promise<GatewayAlias> {
  return request<GatewayAlias>(`${BASE}/gateway/aliases/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteAlias(id: string): Promise<void> {
  return request<void>(`${BASE}/gateway/aliases/${encodeURIComponent(id)}`, { method: "DELETE" });
}

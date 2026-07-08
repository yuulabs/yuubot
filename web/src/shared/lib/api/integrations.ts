import type { IntegrationConfigInput, IntegrationDetail, IntegrationSnapshot } from "@/shared/types/api";
import { BASE, request } from "./client";
import { getBootstrap } from "./bootstrap";

export function listIntegrations(): Promise<IntegrationSnapshot[]> {
  return getBootstrap().then((snapshot) => snapshot.integrations);
}

export function getIntegration(integrationType: string): Promise<IntegrationDetail> {
  return request<IntegrationDetail>(`${BASE}/integrations/${encodeURIComponent(integrationType)}`);
}

export function configureIntegration(integrationType: string, input: IntegrationConfigInput): Promise<IntegrationSnapshot> {
  return request<IntegrationSnapshot>(`${BASE}/integrations/${encodeURIComponent(integrationType)}/config`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function enableIntegration(integrationType: string): Promise<IntegrationSnapshot> {
  return request<IntegrationSnapshot>(`${BASE}/integrations/${encodeURIComponent(integrationType)}/enable`, { method: "POST" });
}

export function disableIntegration(integrationType: string): Promise<IntegrationSnapshot> {
  return request<IntegrationSnapshot>(`${BASE}/integrations/${encodeURIComponent(integrationType)}/disable`, { method: "POST" });
}

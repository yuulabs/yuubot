import type { IntegrationDetail, IntegrationRecord, IntegrationSnapshot } from "@/shared/types/api";
import { BASE, request } from "./client";
import { getBootstrap } from "./bootstrap";

export function listIntegrations(): Promise<IntegrationSnapshot[]> {
  return getBootstrap().then((snapshot) => snapshot.integrations);
}

export function getIntegration(integrationType: string): Promise<IntegrationDetail> {
  return request<IntegrationDetail>(`${BASE}/integrations/${encodeURIComponent(integrationType)}`);
}

export function configureIntegration(record: IntegrationRecord): Promise<IntegrationSnapshot> {
  return request<IntegrationSnapshot>(`${BASE}/integrations/${encodeURIComponent(record.type)}/config`, {
    method: "PUT",
    body: JSON.stringify(record),
  });
}

export function enableIntegration(integrationType: string): Promise<IntegrationSnapshot> {
  return request<IntegrationSnapshot>(`${BASE}/integrations/${encodeURIComponent(integrationType)}/enable`, { method: "POST" });
}

export function disableIntegration(integrationType: string): Promise<IntegrationSnapshot> {
  return request<IntegrationSnapshot>(`${BASE}/integrations/${encodeURIComponent(integrationType)}/disable`, { method: "POST" });
}

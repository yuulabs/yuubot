import type { CredentialRecord, ItemsResponse } from "@/shared/types/api";
import { BASE, request } from "./client";

export function listCredentials(): Promise<CredentialRecord[]> {
  return request<ItemsResponse<CredentialRecord>>(`${BASE}/credentials`).then((res) => res.items);
}

export function deleteCredential(credentialId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/credentials/${encodeURIComponent(credentialId)}`, { method: "DELETE" });
}

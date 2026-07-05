import type { ItemsResponse, ShareGrant } from "@/shared/types/api";
import { BASE, request } from "./client";

export function createShare(actorId: string, sourcePath: string, expiresAt?: string | null): Promise<ShareGrant> {
  return request<ShareGrant>(`${BASE}/shares`, {
    method: "POST",
    body: JSON.stringify({ actor_id: actorId, source_path: sourcePath, expires_at: expiresAt ?? null }),
  });
}

export function listShares(): Promise<ShareGrant[]> {
  return request<ItemsResponse<ShareGrant>>(`${BASE}/shares`).then((res) => res.items);
}

export function revokeShare(shareId: string): Promise<{ id: string; revoked: boolean }> {
  return request<{ id: string; revoked: boolean }>(`${BASE}/shares/${encodeURIComponent(shareId)}`, { method: "DELETE" });
}

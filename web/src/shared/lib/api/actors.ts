import type {
  ActorInboundBody,
  ActorInboundResponse,
  ActorInput,
  ActorRecord,
  ActorSnapshot,
  EtagResponse,
  KvDocument,
  KvPutBody,
  UploadResponse,
  WorkspaceDirectorySnapshot,
} from "@/shared/types/api";
import { authenticatedFetch, BASE, request } from "./client";
import { getBootstrap } from "./bootstrap";

export function listActors(): Promise<ActorSnapshot[]> {
  return getBootstrap().then((snapshot) => snapshot.actors);
}

export function getActor(actorId: string): Promise<ActorRecord> {
  return request<ActorRecord>(`${BASE}/actors/${encodeURIComponent(actorId)}`);
}

export function putActor(actorId: string, input: ActorInput): Promise<ActorSnapshot> {
  return request<ActorSnapshot>(`${BASE}/actors/${encodeURIComponent(actorId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function enableActor(actorId: string): Promise<ActorSnapshot> {
  return request<ActorSnapshot>(`${BASE}/actors/${encodeURIComponent(actorId)}/enable`, { method: "POST" });
}

export function disableActor(actorId: string): Promise<ActorSnapshot> {
  return request<ActorSnapshot>(`${BASE}/actors/${encodeURIComponent(actorId)}/disable`, { method: "POST" });
}

export function deleteActor(actorId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/actors/${encodeURIComponent(actorId)}`, { method: "DELETE" });
}

export function browseActor(actorId: string, path = ""): Promise<WorkspaceDirectorySnapshot> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return request<WorkspaceDirectorySnapshot>(`${BASE}/actors/${encodeURIComponent(actorId)}/browse${query}`);
}

export function getActorFileUrl(actorId: string, path: string): string {
  const encodedPath = path.split("/").map((part) => encodeURIComponent(part)).join("/");
  return `${BASE}/actors/${encodeURIComponent(actorId)}/files/${encodedPath}`;
}

export async function uploadActorFile(actorId: string, files: File[], path?: string): Promise<UploadResponse> {
  const body = new FormData();
  for (const file of files) {
    body.append("file", file, file.name);
  }
  const query = path === undefined ? "" : `?path=${encodeURIComponent(path)}`;
  return request<UploadResponse>(`${BASE}/actors/${encodeURIComponent(actorId)}/uploads${query}`, {
    method: "POST",
    body,
  });
}

export function createWorkspaceDirectory(actorId: string, path: string): Promise<WorkspaceDirectorySnapshot> {
  return request<WorkspaceDirectorySnapshot>(`${BASE}/actors/${encodeURIComponent(actorId)}/workspace/directories`, {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function renameWorkspaceEntry(actorId: string, path: string, name: string): Promise<WorkspaceDirectorySnapshot> {
  return request<WorkspaceDirectorySnapshot>(`${BASE}/actors/${encodeURIComponent(actorId)}/workspace/rename`, {
    method: "POST",
    body: JSON.stringify({ path, name }),
  });
}

export function moveWorkspaceEntries(
  actorId: string,
  sources: string[],
  destination: string,
): Promise<WorkspaceDirectorySnapshot> {
  return request<WorkspaceDirectorySnapshot>(`${BASE}/actors/${encodeURIComponent(actorId)}/workspace/move`, {
    method: "POST",
    body: JSON.stringify({ sources, destination }),
  });
}

export function deleteWorkspaceEntries(actorId: string, paths: string[]): Promise<WorkspaceDirectorySnapshot> {
  return request<WorkspaceDirectorySnapshot>(`${BASE}/actors/${encodeURIComponent(actorId)}/workspace/entries`, {
    method: "DELETE",
    body: JSON.stringify({ paths }),
  });
}

export function sendActorInbound(actorId: string, body: ActorInboundBody): Promise<ActorInboundResponse> {
  return request<ActorInboundResponse>(`${BASE}/actors/${encodeURIComponent(actorId)}/inbound`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getActorKv(actorId: string, key: string): Promise<EtagResponse<KvDocument>> {
  const response = await authenticatedFetch(`${BASE}/actors/${encodeURIComponent(actorId)}/kv/${encodeURIComponent(key)}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body.message ?? body.reason ?? response.statusText;
    throw new Error(`${response.status} ${detail}`);
  }
  return { data: await response.json() as KvDocument, etag: response.headers.get("ETag") };
}

export async function putActorKv(
  actorId: string,
  key: string,
  body: KvPutBody,
  etag?: string | null,
): Promise<EtagResponse<KvDocument>> {
  const response = await authenticatedFetch(`${BASE}/actors/${encodeURIComponent(actorId)}/kv/${encodeURIComponent(key)}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...(etag ? { "If-Match": etag } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    const detail = errorBody.detail ?? errorBody.message ?? errorBody.reason ?? response.statusText;
    throw new Error(`${response.status} ${detail}`);
  }
  return { data: await response.json() as KvDocument, etag: response.headers.get("ETag") };
}

export function deleteActorKv(actorId: string, key: string): Promise<{ actor_id: string; key: string; deleted: boolean }> {
  return request<{ actor_id: string; key: string; deleted: boolean }>(
    `${BASE}/actors/${encodeURIComponent(actorId)}/kv/${encodeURIComponent(key)}`,
    { method: "DELETE" },
  );
}

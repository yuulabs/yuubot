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
  WorkspaceFileContent,
  WorkspaceFileMetadata,
  WorkspaceSkillSummary,
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

export function listActorSkills(actorId: string): Promise<{ items: WorkspaceSkillSummary[] }> {
  return request(`${BASE}/actors/${encodeURIComponent(actorId)}/skills`);
}

export function setActorSkillLoaded(actorId: string, skillId: string, loaded: boolean): Promise<WorkspaceSkillSummary> {
  return request(`${BASE}/actors/${encodeURIComponent(actorId)}/skills/${encodeURIComponent(skillId)}/loaded`, {
    method: "PUT", body: JSON.stringify({ loaded }),
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

export function getActorFileDownloadUrl(actorId: string, path: string): string {
  return `${getActorFileUrl(actorId, path)}?download=true`;
}

export async function getActorFileMetadata(actorId: string, path: string): Promise<WorkspaceFileMetadata> {
  const response = await authenticatedFetch(getActorFileUrl(actorId, path), { method: "HEAD" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return {
    size: Number(response.headers.get("Content-Length") ?? 0),
    mime: response.headers.get("Content-Type") ?? "",
    mtime: response.headers.get("Last-Modified") ?? "",
    etag: response.headers.get("ETag"),
  };
}

export async function downloadWorkspaceEntries(actorId: string, paths: string[]): Promise<void> {
  const response = await authenticatedFetch(`${BASE}/actors/${encodeURIComponent(actorId)}/workspace/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(`${response.status} ${body.detail ?? response.statusText}`);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = "workspace.zip";
  link.click();
  URL.revokeObjectURL(url);
}

export async function getActorFileContent(actorId: string, path: string): Promise<WorkspaceFileContent> {
  const response = await authenticatedFetch(getActorFileUrl(actorId, path));
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new Error(`${response.status} ${message}`);
  }
  return {
    path,
    content: await response.text(),
    mime: response.headers.get("Content-Type") ?? "text/plain",
    size: Number(response.headers.get("Content-Length") ?? 0),
    mtime: response.headers.get("Last-Modified") ?? "",
    etag: response.headers.get("ETag"),
  };
}

export async function putActorFileContent(
  actorId: string,
  path: string,
  content: string,
  etag: string,
): Promise<WorkspaceFileMetadata> {
  const response = await authenticatedFetch(getActorFileUrl(actorId, path), {
    method: "PUT",
    headers: { "Content-Type": "text/plain; charset=utf-8", "If-Match": etag },
    body: content,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = body.error && typeof body.error === "object" ? body.error as Record<string, unknown> : null;
    const message = typeof error?.message === "string" ? error.message : response.statusText;
    throw new Error(response.status === 412 ? `Conflict: ${message}` : `${response.status} ${message}`);
  }
  const data = await response.json() as Omit<WorkspaceFileMetadata, "etag">;
  return { ...data, etag: response.headers.get("ETag") };
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

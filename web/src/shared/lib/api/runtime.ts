import type { ItemsResponse, RuntimeSnapshot, TaskRecord } from "@/shared/types/api";
import { BASE, request } from "./client";

export function getRuntime(): Promise<RuntimeSnapshot> {
  return request<RuntimeSnapshot>(`${BASE}/runtime`);
}

export function listTasks(filters: { owner?: string; nameGlob?: string } = {}): Promise<TaskRecord[]> {
  const params = new URLSearchParams();
  if (filters.owner) params.set("owner", filters.owner);
  if (filters.nameGlob) params.set("name_glob", filters.nameGlob);
  const query = params.toString();
  return request<ItemsResponse<TaskRecord>>(`${BASE}/tasks${query ? `?${query}` : ""}`).then((res) => res.items);
}

export function getTask(taskId: string): Promise<TaskRecord> {
  return request<TaskRecord>(`${BASE}/tasks/${encodeURIComponent(taskId)}`);
}

export function cancelTask(taskId: string): Promise<TaskRecord> {
  return request<TaskRecord>(`${BASE}/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
}

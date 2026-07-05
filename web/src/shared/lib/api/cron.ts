import type { CronJobRecord, ItemsResponse } from "@/shared/types/api";
import { BASE, request } from "./client";

export function listCronJobs(filters: { owner?: string; status?: string; nameGlob?: string } = {}): Promise<CronJobRecord[]> {
  const params = new URLSearchParams();
  if (filters.owner) params.set("owner", filters.owner);
  if (filters.status) params.set("status", filters.status);
  if (filters.nameGlob) params.set("name_glob", filters.nameGlob);
  const query = params.toString();
  return request<ItemsResponse<CronJobRecord>>(`${BASE}/cron-jobs${query ? `?${query}` : ""}`).then((res) => res.items);
}

export function getCronJob(jobId: string): Promise<CronJobRecord> {
  return request<CronJobRecord>(`${BASE}/cron-jobs/${encodeURIComponent(jobId)}`);
}

export function createCronJob(body: {
  name: string;
  owner: string;
  schedule: CronJobRecord["schedule"];
  action: CronJobRecord["action"];
  once?: boolean;
}): Promise<CronJobRecord> {
  return request<CronJobRecord>(`${BASE}/cron-jobs`, { method: "POST", body: JSON.stringify(body) });
}

export function pauseCronJob(jobId: string): Promise<CronJobRecord> {
  return request<CronJobRecord>(`${BASE}/cron-jobs/${encodeURIComponent(jobId)}/pause`, { method: "POST" });
}

export function resumeCronJob(jobId: string): Promise<CronJobRecord> {
  return request<CronJobRecord>(`${BASE}/cron-jobs/${encodeURIComponent(jobId)}/resume`, { method: "POST" });
}

export function deleteCronJob(jobId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/cron-jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
}

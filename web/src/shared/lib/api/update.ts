import type { UpdateApplyResult, UpdateStatus } from "@/shared/types/api";
import { BASE, request } from "./client";

export function getUpdateStatus(): Promise<UpdateStatus> {
  return request<UpdateStatus>(`${BASE}/admin/update/status`);
}

export function applyUpdate(): Promise<UpdateApplyResult> {
  return request<UpdateApplyResult>(`${BASE}/admin/update/apply`, { method: "POST" });
}

export async function checkHealthz(): Promise<boolean> {
  const response = await fetch("/healthz");
  return response.ok;
}

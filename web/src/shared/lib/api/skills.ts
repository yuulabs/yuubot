import type { SkillCopyPreview, SkillInput, SkillPackageBody, SkillPackageResult, SkillRecord, SkillSummary } from "@/shared/types/api";
import { BASE, request } from "./client";

export interface SkillCatalogResponse {
  items: SkillSummary[];
  warning: string;
}

export function listSkills(): Promise<SkillCatalogResponse> {
  return request<SkillCatalogResponse>(`${BASE}/skills`);
}

export function refreshSkills(): Promise<SkillCatalogResponse> {
  return request<SkillCatalogResponse>(`${BASE}/skills/refresh`, { method: "POST" });
}

export function getSkill(skillId: string): Promise<SkillRecord> {
  return request<SkillRecord>(`${BASE}/skills/${encodeURIComponent(skillId)}`);
}

export function createSkill(input: Pick<SkillRecord, "id" | "name" | "description" | "body">): Promise<{ record: SkillRecord; summary: SkillSummary }> {
  return request<{ record: SkillRecord; summary: SkillSummary }>(`${BASE}/skills`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function putSkill(skillId: string, input: SkillInput): Promise<{ record: SkillRecord; summary: SkillSummary }> {
  return request<{ record: SkillRecord; summary: SkillSummary }>(`${BASE}/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function addSkillPackage(input: SkillPackageBody): Promise<SkillPackageResult> {
  return request<SkillPackageResult>(`${BASE}/skills/packages`, { method: "POST", body: JSON.stringify(input) });
}

export function updateSkillPackages(): Promise<SkillPackageResult> {
  return request<SkillPackageResult>(`${BASE}/skills/packages/update`, { method: "POST" });
}

export function updateSkill(skillId: string): Promise<SkillPackageResult> {
  return request<SkillPackageResult>(`${BASE}/skills/${encodeURIComponent(skillId)}/update`, { method: "POST" });
}

export function deleteSkill(skillId: string, source: SkillSummary["source"]): Promise<{ id: string; deleted: boolean }> {
  const query = new URLSearchParams({ source });
  return request<{ id: string; deleted: boolean }>(`${BASE}/skills/${encodeURIComponent(skillId)}?${query}`, { method: "DELETE" });
}

export function getSkillCopyPreview(skillId: string, actorId: string): Promise<SkillCopyPreview> {
  const query = new URLSearchParams({ actor_id: actorId });
  return request<SkillCopyPreview>(`${BASE}/skills/${encodeURIComponent(skillId)}/copy-preview?${query}`);
}

export function copySkill(skillId: string, actorId: string, replace: boolean): Promise<SkillCopyPreview> {
  return request<SkillCopyPreview>(`${BASE}/skills/${encodeURIComponent(skillId)}/copy`, {
    method: "POST",
    body: JSON.stringify({ actor_id: actorId, replace }),
  });
}

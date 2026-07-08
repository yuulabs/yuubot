import type { ItemsResponse, SkillCliAction, SkillCliCommandResult, SkillInput, SkillRecord, SkillSummary } from "@/shared/types/api";
import { BASE, request } from "./client";

export function listSkills(): Promise<SkillSummary[]> {
  return request<ItemsResponse<SkillSummary>>(`${BASE}/skills`).then((res) => res.items);
}

export function listInstalledSkills(): Promise<SkillSummary[]> {
  return request<ItemsResponse<SkillSummary>>(`${BASE}/skills/installed`).then((res) => res.items);
}

export function runSkillCommand(action: SkillCliAction, target = ""): Promise<SkillCliCommandResult> {
  return request<SkillCliCommandResult>(`${BASE}/skills/commands`, {
    method: "POST",
    body: JSON.stringify({ action, target }),
  });
}

export function getSkill(skillId: string): Promise<SkillRecord> {
  return request<SkillRecord>(`${BASE}/skills/${encodeURIComponent(skillId)}`);
}

export function putSkill(skillId: string, input: SkillInput): Promise<{ record: SkillRecord; summary: SkillSummary }> {
  return request<{ record: SkillRecord; summary: SkillSummary }>(`${BASE}/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteSkill(skillId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/skills/${encodeURIComponent(skillId)}`, { method: "DELETE" });
}

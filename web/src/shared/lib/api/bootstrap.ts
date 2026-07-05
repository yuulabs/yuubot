import type { BootstrapSnapshot, HealthResponse } from "@/shared/types/api";
import { BASE, request } from "./client";

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/healthz");
}

export function getBootstrap(): Promise<BootstrapSnapshot> {
  return request<BootstrapSnapshot>(`${BASE}/bootstrap`);
}

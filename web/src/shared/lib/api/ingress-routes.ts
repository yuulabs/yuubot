import type { RouteCreateInput, RouteRecord, RouteUpdateInput } from "@/shared/types/api";
import { BASE, request } from "./client";
import { getBootstrap } from "./bootstrap";

export function listRoutes(): Promise<RouteRecord[]> {
  return getBootstrap().then((snapshot) => snapshot.routes);
}

export function createRoute(record: RouteCreateInput): Promise<RouteRecord> {
  return request<RouteRecord>(`${BASE}/routes`, { method: "POST", body: JSON.stringify(record) });
}

export function updateRoute(routeId: string, input: RouteUpdateInput): Promise<RouteRecord> {
  return request<RouteRecord>(`${BASE}/routes/${encodeURIComponent(routeId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function deleteRoute(routeId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`${BASE}/routes/${encodeURIComponent(routeId)}`, { method: "DELETE" });
}

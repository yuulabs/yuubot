import type { BootstrapSnapshot, RouteRecord } from "@/shared/types/api";
import { BASE, request } from "./client";
import { getBootstrap } from "./bootstrap";

export function listRoutes(): Promise<RouteRecord[]> {
  return getBootstrap().then((snapshot) => snapshot.routes);
}

export function createRoute(record: RouteRecord): Promise<BootstrapSnapshot> {
  return request<BootstrapSnapshot>(`${BASE}/routes`, { method: "POST", body: JSON.stringify(record) });
}

export function updateRoute(record: RouteRecord): Promise<BootstrapSnapshot> {
  return request<BootstrapSnapshot>(`${BASE}/routes/${encodeURIComponent(record.id)}`, {
    method: "PUT",
    body: JSON.stringify(record),
  });
}

export function deleteRoute(routeId: string): Promise<BootstrapSnapshot> {
  return request<BootstrapSnapshot>(`${BASE}/routes/${encodeURIComponent(routeId)}`, { method: "DELETE" });
}

import { useQuery } from "@tanstack/react-query";

import { getBootstrap, getHealth } from "@/shared/lib/api";

export const queryKeys = {
  bootstrap: () => ["bootstrap"] as const,
  health: () => ["health"] as const,
};

export function useHealth() {
  return useQuery({ queryKey: queryKeys.health(), queryFn: getHealth });
}

export function useBootstrapResource() {
  return useQuery({ queryKey: queryKeys.bootstrap(), queryFn: getBootstrap });
}

export { useBootstrap, useRefreshBootstrap, useApiMutation, requireBootstrap } from "./use-bootstrap";
export { useNotificationListener } from "./use-notification-listener";
export { useSidebar } from "./use-sidebar";

import { useQuery } from "@tanstack/react-query";

import { getHealth } from "@/shared/lib/api";

export const queryKeys = {
  bootstrap: () => ["bootstrap"] as const,
  health: () => ["health"] as const,
};

export function useHealth() {
  return useQuery({ queryKey: queryKeys.health(), queryFn: getHealth });
}

export { useBootstrap, useRefreshBootstrap, useSetBootstrapSnapshot, useApiMutation, requireBootstrap } from "./use-bootstrap";
export { useNotificationListener } from "./use-notification-listener";
export { useSidebar } from "./use-sidebar";

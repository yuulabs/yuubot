/** TanStack Query v5 hooks for yuubot resource CRUD.
 *
 * All mutations auto-invalidate the affected resource list on settle so the UI
 * stays consistent without manual cache management.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ResourceType } from "@/types/api";
import {
  createResource,
  deleteResource,
  getHealth,
  getIntegrationKinds,
  getLiveCapabilities,
  getPresetActors,
  listResources,
  setResourceEnabled,
  updateResource,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const resourceKeys = {
  all: ["resources"] as const,
  list: (type: ResourceType) => ["resources", "list", type] as const,
  health: () => ["health"] as const,
  integrationKinds: () => ["integration-kinds"] as const,
  liveCapabilities: () => ["live-capabilities"] as const,
  presetActors: () => ["preset-actors"] as const,
};

// ---------------------------------------------------------------------------
// Read hooks (queries)
// ---------------------------------------------------------------------------

/** Fetch a paginated list of resources by type. */
export function useResourceList<T>(resourceType: ResourceType) {
  return useQuery({
    queryKey: resourceKeys.list(resourceType),
    queryFn: () => listResources<T>(resourceType),
  });
}

/** Admin + daemon health status. */
export function useHealth() {
  return useQuery({
    queryKey: resourceKeys.health(),
    queryFn: getHealth,
    retry: false,
    // Health is static; refetch only on window focus.
    staleTime: 30_000,
  });
}

/** Available integration kinds with capability metadata. */
export function useIntegrationKinds() {
  return useQuery({
    queryKey: resourceKeys.integrationKinds(),
    queryFn: getIntegrationKinds,
    staleTime: 60_000,
  });
}

/** Capabilities from existing integration instances (enabled + disabled). */
export function useLiveCapabilities() {
  return useQuery({
    queryKey: resourceKeys.liveCapabilities(),
    queryFn: getLiveCapabilities,
    staleTime: 30_000, // 30s — reflects integration create/disable/enable
  });
}

export function usePresetActors() {
  return useQuery({
    queryKey: resourceKeys.presetActors(),
    queryFn: getPresetActors,
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Write hooks (mutations)
// ---------------------------------------------------------------------------

/** Create a resource and invalidate its list. */
export function useCreateResource<T>(resourceType: ResourceType) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: unknown) => createResource<T>(resourceType, data),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: resourceKeys.list(resourceType) });
      if (resourceType === "integrations") {
        queryClient.invalidateQueries({ queryKey: resourceKeys.liveCapabilities() });
      }
    },
  });
}

/** Update a resource by id and invalidate its list. */
export function useUpdateResource<T>(resourceType: ResourceType) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: unknown }) =>
      updateResource<T>(resourceType, id, data),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: resourceKeys.list(resourceType) });
      if (resourceType === "integrations") {
        queryClient.invalidateQueries({ queryKey: resourceKeys.liveCapabilities() });
      }
    },
  });
}

/** Delete a resource by id and invalidate its list. */
export function useDeleteResource(resourceType: ResourceType) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteResource(resourceType, id),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: resourceKeys.list(resourceType) });
      if (resourceType === "integrations") {
        queryClient.invalidateQueries({ queryKey: resourceKeys.liveCapabilities() });
      }
    },
  });
}

/** Toggle a resource's enabled state and invalidate its list. */
export function useSetResourceEnabled(resourceType: ResourceType) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setResourceEnabled(resourceType, id, enabled),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: resourceKeys.list(resourceType) });
      if (resourceType === "integrations") {
        queryClient.invalidateQueries({ queryKey: resourceKeys.liveCapabilities() });
      }
    },
  });
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getBootstrap } from "@/shared/lib/api";
import type { BootstrapSnapshot } from "@/shared/types/api";

export const bootstrapQueryKey = ["bootstrap"] as const;

export function useBootstrap() {
  return useQuery({ queryKey: bootstrapQueryKey, queryFn: getBootstrap });
}

export function useRefreshBootstrap() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: bootstrapQueryKey });
}

export function useSetBootstrapSnapshot() {
  const client = useQueryClient();
  return (snapshot: BootstrapSnapshot) => {
    client.setQueryData(bootstrapQueryKey, snapshot);
  };
}

export function useApiMutation<TArgs>(fn: (args: TArgs) => Promise<unknown>) {
  const refresh = useRefreshBootstrap();
  return useMutation({
    mutationFn: fn,
    onSuccess: refresh,
  });
}

export function requireBootstrap<T extends NonNullable<Awaited<ReturnType<typeof getBootstrap>>>>(snapshot: T | undefined): T {
  if (!snapshot) {
    throw new Error("bootstrap snapshot is not loaded");
  }
  return snapshot;
}

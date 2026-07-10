import { useQuery } from "@tanstack/react-query";

import { getConversations } from "@/shared/lib/api";

export const conversationsQueryKey = ["conversations"] as const;

export function useConversations() {
  return useQuery({ queryKey: conversationsQueryKey, queryFn: getConversations });
}

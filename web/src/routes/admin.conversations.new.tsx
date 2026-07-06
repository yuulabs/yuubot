import { createFileRoute } from "@tanstack/react-router";

import { ConversationDetailPage } from "@/features/conversations";

export const Route = createFileRoute("/admin/conversations/new")({
  validateSearch: (search: Record<string, unknown>) => ({
    actor: typeof search.actor === "string" ? search.actor : "",
  }),
  component: () => {
    const { actor } = Route.useSearch();
    return <ConversationDetailPage conversationId="new" draftActorId={actor} />;
  },
});

import { createFileRoute } from "@tanstack/react-router";

import { ConversationDetailPage } from "@/features/conversations";

export const Route = createFileRoute("/admin/conversations/new")({
  validateSearch: (search: Record<string, unknown>) => ({
    actor: typeof search.actor === "string" ? search.actor : "",
    prompt: typeof search.prompt === "string" ? search.prompt : "",
  }),
  component: () => {
    const { actor, prompt } = Route.useSearch();
    return <ConversationDetailPage conversationId="new" draftActorId={actor} draftPrompt={prompt} />;
  },
});

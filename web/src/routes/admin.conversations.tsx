import { createFileRoute } from "@tanstack/react-router";

import { ConversationsListPage } from "@/features/conversations/conversations-list-page";

export const Route = createFileRoute("/admin/conversations")({
  component: ConversationsListPage,
});

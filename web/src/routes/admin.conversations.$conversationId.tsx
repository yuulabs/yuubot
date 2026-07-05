import { createFileRoute } from "@tanstack/react-router";

import { ConversationDetailPage } from "@/features/conversations";

export const Route = createFileRoute("/admin/conversations/$conversationId")({
  component: () => {
    const { conversationId } = Route.useParams();
    return <ConversationDetailPage conversationId={conversationId} />;
  },
});

import { Link, Outlet, useRouterState } from "@tanstack/react-router";

import { deleteConversation } from "@/shared/lib/api";
import {
  DeleteButton,
  EmptyState,
  ErrorState,
  LoadingState,
  Page,
  ResourceCard,
  ResourceCardGrid,
  ResourceMeta,
  Status,
} from "@/shared/components";
import { useApiMutation, useBootstrap } from "@/shared/hooks";

export function ConversationsListPage() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const { data, error, isLoading } = useBootstrap();
  const remove = useApiMutation((id: string) => deleteConversation(id));

  if (pathname !== "/admin/conversations") {
    return <Outlet />;
  }
  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  return (
    <Page title="Conversations" sub="Persisted conversations and history from the new backend.">
      {!data?.conversations.length ? <EmptyState>No conversations yet.</EmptyState> : (
        <ResourceCardGrid>
          {data.conversations.map((conversation) => (
            <ResourceCard
              key={conversation.id}
              variant="conversation"
              label={conversation.actor_id ?? "unknown actor"}
              title={<Link className="font-medium underline-offset-4 hover:underline" to="/admin/conversations/$conversationId" params={{ conversationId: conversation.id }}>{conversation.title || conversation.id}</Link>}
              subtitle={`${conversation.message_count ?? 0} history items / ${formatTime(conversation.last_active_at ?? conversation.created_at)}`}
              status={<Status enabled={!conversation.last_error} label={conversation.status ?? "idle"} />}
              actions={
                <>
                  <Link className="font-medium underline-offset-4 hover:underline" to="/admin/conversations/$conversationId" params={{ conversationId: conversation.id }}>Open</Link>
                  <DeleteButton onDelete={() => remove.mutate(conversation.id)} />
                </>
              }
            >
              <ResourceMeta
                items={[
                  { label: "Actor", value: conversation.actor_id ?? "unknown", tone: conversation.actor_id ? "default" : "warning" },
                  { label: "Status", value: conversation.status ?? "idle" },
                  { label: "Messages", value: conversation.message_count ?? 0 },
                  { label: "Last seq", value: conversation.last_seq ?? "none", tone: conversation.last_seq ? "default" : "muted" },
                ]}
              />
              {conversation.last_error && <pre className="resource-preview">{JSON.stringify(conversation.last_error, null, 2)}</pre>}
            </ResourceCard>
          ))}
        </ResourceCardGrid>
      )}
    </Page>
  );
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "no activity yet";
  return new Date(value).toLocaleString();
}

import { useEffect, useMemo, useState } from "react";
import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { MessageSquare } from "lucide-react";
import { PageShell, StatusPill } from "@/components/baseline";
import { useResourceList } from "@/hooks/use-resources";
import { listConversations } from "@/lib/api";
import type { ActorResource, ConversationListItem } from "@/types/api";

// ISSUE-0010: "start a conversation with an Actor" is the sole creation
// path. The top-level Conversation list page, its top-level New-conversation
// creator, and the welcome card are gone. Conversations are reached only
// from an Actor (row action on /actors + Actor detail page history list).
//
// The route is both:
// - a parent outlet for /admin/conversations/$conversationId
// - a history-only list for bare /admin/conversations
//
// There is still no top-level "new conversation" creator. Creation remains
// actor-scoped via /admin/conversations/actor-<actor.id>.
export const Route = createFileRoute("/admin/conversations")({
  component: ConversationsRoute,
});

function ConversationsRoute() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname !== "/admin/conversations") {
    return <Outlet />;
  }
  return <ConversationHistoryPage />;
}

function ConversationHistoryPage() {
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const [items, setItems] = useState<ConversationListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const rows = await listConversations();
        if (cancelled) return;
        setItems(rows.sort((left, right) => conversationTime(right) - conversationTime(left)));
        setError("");
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : "读取对话历史失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const actorById = useMemo(
    () => new Map(actors.map((actor) => [actor.id, actor])),
    [actors],
  );

  return (
    <PageShell
      title="Conversations"
      sub="从 Actor 发起的历史对话。新的对话仍从 Actor 页面创建。"
      actions={
        <Link to="/actors">
          <button type="button" className="btn btn--primary">
            <MessageSquare size={15} />
            <span>选择 Actor</span>
          </button>
        </Link>
      }
    >
      {loading ? (
        <div className="chat__empty-mini">正在读取历史对话…</div>
      ) : error ? (
        <div className="chat__empty-mini">{error}</div>
      ) : items.length === 0 ? (
        <div className="chat__empty">
          <div className="chat__empty-inner">
            <div className="chat__empty-icon"><MessageSquare size={22} /></div>
            <div className="chat__empty-title">还没有对话</div>
            <div className="chat__empty-sub">进入 Actors，选择一个 Actor 后发起第一轮对话。</div>
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="detail-conv-list">
            {items.map((item) => {
              const actor = actorById.get(item.actor_id);
              const shortId = shortConversationId(item.conversation_id);
              return (
                <Link
                  key={item.conversation_id}
                  to="/admin/conversations/$conversationId"
                  params={{ conversationId: item.conversation_id }}
                  className="detail-conv-item"
                  title={item.conversation_id}
                >
                  <span className="detail-conv-item__name">{actor?.name ?? item.actor_id}</span>
                  <span className="detail-conv-item__preview">ID {shortId}</span>
                  <span className="detail-conv-item__time">{formatConversationTime(item.updated_at ?? item.created_at)}</span>
                </Link>
              );
            })}
          </div>
        </div>
      )}
      {items.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <StatusPill variant="default" dot={false}>{items.length} 条历史</StatusPill>
        </div>
      )}
    </PageShell>
  );
}

function conversationTime(conversation: ConversationListItem): number {
  const value = conversation.updated_at ?? conversation.created_at;
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function formatConversationTime(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function shortConversationId(value: string): string {
  return value.replace(/^conversation-/, "").slice(0, 8);
}

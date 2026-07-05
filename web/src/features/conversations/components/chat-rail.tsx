import { Link } from "@tanstack/react-router";
import { Plus, Trash2 } from "lucide-react";

import type { ConversationSummary } from "@/shared/types/api";

export function ChatRail({
  actorId,
  actorName,
  conversations,
  activeConversationId,
  onDelete,
}: {
  actorId: string;
  actorName: string;
  conversations: ConversationSummary[];
  activeConversationId: string;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="chat__rail">
      <div className="chat__rail-head">
        <span>{actorName || "Conversations"}</span>
        <div className="chat__rail-actions">
          {actorId && (
            <Link className="btn btn--sm" to="/admin/conversations/$conversationId" params={{ conversationId: `actor-${actorId}` }}>
              <Plus size={13} />
            </Link>
          )}
        </div>
      </div>
      <div className="chat__list">
        {!conversations.length ? (
          <p className="conv-item__preview" style={{ padding: "var(--sp-3)" }}>No saved conversations yet.</p>
        ) : (
          conversations.map((conversation) => {
            const active = conversation.id === activeConversationId;
            return (
              <div key={conversation.id} className={`conv-item${active ? " is-active" : ""}`}>
                <Link
                  className="conv-item__link"
                  to="/admin/conversations/$conversationId"
                  params={{ conversationId: conversation.id }}
                >
                  <div className="conv-item__top">
                    <span className="conv-item__name">{conversation.title || conversation.id}</span>
                    <span className="conv-item__time">{formatTime(conversation.last_active_at ?? conversation.created_at)}</span>
                  </div>
                  <span className="conv-item__preview">
                    {conversation.message_count ?? 0} messages · {conversation.status ?? "idle"}
                  </span>
                  {conversation.actor_id && <span className="conv-item__actor">{conversation.actor_id}</span>}
                </Link>
                <button
                  type="button"
                  className="conv-item__delete"
                  aria-label={`Delete ${conversation.id}`}
                  onClick={() => onDelete(conversation.id)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

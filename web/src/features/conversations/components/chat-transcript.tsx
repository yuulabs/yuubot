import { useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";

import type { ConversationPhase } from "../lib/conversation-transcript";
import type { DisplayItem } from "../lib/conversation-transcript";
import { ChatTurn } from "./chat-turn";

export function ChatTranscript({
  items,
  phase,
  waitingForResponse,
}: {
  items: DisplayItem[];
  phase: ConversationPhase;
  waitingForResponse: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [items, waitingForResponse]);

  if (!items.length && !waitingForResponse) {
    return (
      <div className="chat__scroll" ref={scrollRef}>
        <div className="chat__empty">
          <div className="chat__empty-inner">
            <div className="chat__empty-icon">
              <svg viewBox="0 0 24 24"><path d="M4 5h16v12H8l-4 4V5z" /></svg>
            </div>
            <div className="chat__empty-title">Start a conversation</div>
            <div className="chat__empty-sub">Send a message to begin chatting with this actor.</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="chat__scroll" ref={scrollRef}>
      <div className="chat__transcript">
        {items.map((item) => (
          <ChatTurn key={item.key} item={item} />
        ))}
        {waitingForResponse && phase === "sending" && (
          <div className="msg msg--assistant msg--pending">
            <div className="msg__avatar">A</div>
            <div className="msg__body">
              <div className="msg__bubble msg__bubble--pending">
                <Loader2 size={14} className="msg__tool-spinner" />
                <span>Waiting for response…</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

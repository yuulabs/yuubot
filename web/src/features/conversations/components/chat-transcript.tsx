import { useCallback, useLayoutEffect, useRef } from "react";
import { Loader2 } from "lucide-react";

import type { ConversationPhase } from "../lib/conversation-transcript";
import type { DisplayItem } from "../lib/conversation-transcript";
import { ChatTurn } from "./chat-turn";

export function ChatTranscript({
  items,
  phase,
  scrollResetKey,
  waitingForResponse,
}: {
  items: DisplayItem[];
  phase: ConversationPhase;
  scrollResetKey: string;
  waitingForResponse: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const shouldFollowBottomRef = useRef(true);
  const didInitialScrollRef = useRef(false);

  useLayoutEffect(() => {
    shouldFollowBottomRef.current = true;
    didInitialScrollRef.current = false;
  }, [scrollResetKey]);

  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    if (!didInitialScrollRef.current) {
      node.scrollTop = node.scrollHeight;
      didInitialScrollRef.current = true;
      return;
    }
    if (shouldFollowBottomRef.current) {
      node.scrollTop = node.scrollHeight;
    }
  }, [items, scrollResetKey, waitingForResponse]);

  const updateShouldFollowBottom = useCallback(() => {
    const node = scrollRef.current;
    if (!node) return;
    shouldFollowBottomRef.current = isNearBottom(node);
  }, []);

  if (!items.length && !waitingForResponse) {
    return (
      <div className="chat__scroll" ref={scrollRef} onScroll={updateShouldFollowBottom}>
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
    <div className="chat__scroll" ref={scrollRef} onScroll={updateShouldFollowBottom}>
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

function isNearBottom(node: HTMLDivElement): boolean {
  return node.scrollHeight - node.scrollTop - node.clientHeight <= 48;
}

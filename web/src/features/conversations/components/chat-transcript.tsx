import { useCallback, useLayoutEffect, useRef } from "react";
import { Loader2 } from "lucide-react";

import type { ConversationPhase } from "../lib/conversation-transcript";
import type { DisplayItem } from "../lib/conversation-transcript";
import type { AskUserAnswerInput } from "@/shared/lib/api";
import { ChatTurn } from "./chat-turn";

export function ChatTranscript({
  actorId,
  items,
  phase,
  scrollResetKey,
  waitingForResponse,
  onAnswerQuestion,
  hasOlder,
  loadingOlder,
  onLoadOlder,
}: {
  actorId: string;
  items: DisplayItem[];
  phase: ConversationPhase;
  scrollResetKey: string;
  waitingForResponse: boolean;
  onAnswerQuestion: (toolCallId: string, answers: AskUserAnswerInput[], skipped?: boolean) => boolean;
  hasOlder?: boolean;
  loadingOlder?: boolean;
  onLoadOlder?: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const shouldFollowBottomRef = useRef(true);
  const didInitialScrollRef = useRef(false);
  const prependAnchorRef = useRef<{ height: number; top: number } | null>(null);

  useLayoutEffect(() => {
    shouldFollowBottomRef.current = true;
    didInitialScrollRef.current = false;
  }, [scrollResetKey]);

  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const prependAnchor = prependAnchorRef.current;
    if (prependAnchor) {
      node.scrollTop = prependAnchor.top + (node.scrollHeight - prependAnchor.height);
      prependAnchorRef.current = null;
      return;
    }
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

  const loadOlder = useCallback(() => {
    const node = scrollRef.current;
    if (node) {
      prependAnchorRef.current = { height: node.scrollHeight, top: node.scrollTop };
      shouldFollowBottomRef.current = false;
    }
    onLoadOlder?.();
  }, [onLoadOlder]);

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
        {hasOlder && (
          <button className="button chat__load-older" type="button" disabled={loadingOlder} onClick={loadOlder}>
            {loadingOlder ? "Loading…" : "Load earlier messages"}
          </button>
        )}
        {items.map((item) => (
          <ChatTurn key={item.key} actorId={actorId} item={item} onAnswerQuestion={onAnswerQuestion} />
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

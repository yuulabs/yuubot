import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  connectWs,
  interruptConversation,
  sendConversation,
  type WsContentItem,
} from "@/shared/lib/api";

import {
  appendRenderBlocks,
  isTerminalStreamStop,
  markToolBlocksCompleted,
  renderBlocksFromStreamEvent,
  renderBlocksFromToolResults,
  type ConversationPhase,
  type RenderBlock,
} from "../lib/conversation-transcript";

interface WsFrame {
  id?: string;
  type?: string;
  payload?: Record<string, unknown>;
  error?: { code?: string; message?: string };
}

interface PendingSend {
  actorId: string;
  content: WsContentItem[];
  conversationId?: string;
  userText: string;
}

export function useConversationSession({
  conversationId,
  isDraft,
  development,
  onConversationAccepted,
  onStreamStop,
}: {
  conversationId: string;
  isDraft: boolean;
  development: boolean;
  onConversationAccepted: (id: string) => void;
  onStreamStop: () => void;
}) {
  const wsRef = useRef<WebSocket | null>(null);
  const pendingRef = useRef<PendingSend | null>(null);
  const onAcceptedRef = useRef(onConversationAccepted);
  const onStreamStopRef = useRef(onStreamStop);
  const conversationIdRef = useRef(conversationId);
  const prevConversationIdRef = useRef(conversationId);
  const liveBlockIndexRef = useRef(0);
  const turnKeyRef = useRef("");
  const inFlightRef = useRef(false);

  const [wsReady, setWsReady] = useState(false);
  const [phase, setPhase] = useState<ConversationPhase>("idle");
  const [liveBlocks, setLiveBlocks] = useState<RenderBlock[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [optimisticUserText, setOptimisticUserText] = useState<string | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [activeConversationId, setActiveConversationId] = useState(isDraft ? "" : conversationId);
  const [turnKey, setTurnKey] = useState("");

  onAcceptedRef.current = onConversationAccepted;
  onStreamStopRef.current = onStreamStop;
  conversationIdRef.current = conversationId;

  const resetLiveTurn = useCallback(() => {
    turnKeyRef.current = "";
    liveBlockIndexRef.current = 0;
    setTurnKey("");
    setLiveBlocks([]);
  }, []);

  const finishTurn = useCallback(() => {
    inFlightRef.current = false;
  }, []);

  const beginTurn = useCallback(() => {
    const nextTurnKey = `turn-${Date.now()}`;
    inFlightRef.current = true;
    turnKeyRef.current = nextTurnKey;
    liveBlockIndexRef.current = 0;
    setTurnKey(nextTurnKey);
    setLiveBlocks([]);
  }, []);

  const appendStreamEvent = useCallback((event: Record<string, unknown>) => {
    const groupId = typeof event.group_id === "string" ? event.group_id : "stream";
    const kind = typeof event.kind === "string" ? event.kind : "";
    const payload = event.payload && typeof event.payload === "object"
      ? (event.payload as Record<string, unknown>)
      : {};
    const keyPrefix = turnKeyRef.current || "live";
    const blocks = renderBlocksFromStreamEvent(
      { group_id: groupId, kind, payload },
      keyPrefix,
      () => liveBlockIndexRef.current++,
    );
    if (!blocks.length) {
      return;
    }
    setLiveBlocks((current) => appendRenderBlocks(current, blocks));
  }, []);

  const appendToolResults = useCallback((results: unknown[]) => {
    const keyPrefix = turnKeyRef.current || "live";
    const blocks = renderBlocksFromToolResults(results, keyPrefix, () => liveBlockIndexRef.current++);
    if (!blocks.length) {
      return;
    }
    setLiveBlocks((current) => appendRenderBlocks(current, blocks));
  }, []);

  const flushPending = useCallback((ws: WebSocket) => {
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    sendConversation(ws, pending.actorId, pending.content, pending.conversationId);
    setOptimisticUserText(pending.userText);
    beginTurn();
    setPhase("sending");
    setError(null);
  }, [beginTurn]);

  useEffect(() => {
    const previousId = prevConversationIdRef.current;
    prevConversationIdRef.current = conversationId;
    const draftAcceptedTransition = previousId.startsWith("actor-") && !conversationId.startsWith("actor-");

    if (!isDraft) {
      setActiveConversationId(conversationId);
    }

    if (draftAcceptedTransition) {
      return;
    }

    setPhase("idle");
    finishTurn();
    resetLiveTurn();
    setOptimisticUserText(null);
    setError(null);
    pendingRef.current = null;
  }, [conversationId, finishTurn, isDraft, resetLiveTurn]);

  useEffect(() => {
    let disposed = false;
    const ws = connectWs();
    wsRef.current = ws;
    setWsReady(false);

    ws.onopen = () => {
      if (disposed) return;
      setWsReady(true);
      setError(null);
      setPhase((current) => (current === "sending" || current === "streaming" ? current : "idle"));
      flushPending(ws);
    };

    ws.onerror = () => {
      if (disposed) return;
      setError("WebSocket connection failed.");
      setPhase("error");
      finishTurn();
    };

    ws.onclose = () => {
      if (disposed) return;
      setWsReady(false);
    };

    ws.onmessage = (event) => {
      if (disposed) return;
      if (development) {
        setEvents((items) => [...items.slice(-100), event.data]);
      }
      const frame = parseFrame(event.data);
      if (!frame) return;

      if (frame.type === "conversation.send.accepted") {
        const acceptedId = frame.payload?.conversation_id;
        if (typeof acceptedId === "string" && acceptedId) {
          setActiveConversationId(acceptedId);
          if (acceptedId !== conversationIdRef.current) {
            onAcceptedRef.current(acceptedId);
          }
        }
        return;
      }

      if (frame.type === "conversation.tool_results") {
        const results = Array.isArray(frame.payload?.results) ? frame.payload.results : [];
        appendToolResults(results);
        return;
      }

      if (frame.type === "conversation.stream") {
        const streamPayload = frame.payload;
        const streamEvent = streamPayload?.event as Record<string, unknown> | undefined;
        const kind = streamEvent?.kind;
        if (kind === "text_delta" || kind === "reasoning_delta" || kind === "tool_name" || kind === "tool_arguments_delta" || kind === "tool_arguments_end") {
          setPhase("streaming");
          appendStreamEvent(streamEvent ?? {});
          return;
        }
        if (kind === "stream_stop") {
          const stopPayload = streamEvent?.payload && typeof streamEvent.payload === "object"
            ? (streamEvent.payload as Record<string, unknown>)
            : {};
          if (isTerminalStreamStop(stopPayload)) {
            setPhase("idle");
            finishTurn();
            onStreamStopRef.current();
          } else {
            setLiveBlocks((current) => markToolBlocksCompleted(current));
            setPhase("streaming");
          }
          return;
        }
        return;
      }

      if (frame.type === "error") {
        const message = frame.error?.message ?? "Conversation request failed.";
        setError(message);
        setPhase("error");
        finishTurn();
        setOptimisticUserText(null);
        resetLiveTurn();
        toast.error(message);
      }
    };

    return () => {
      disposed = true;
      ws.close();
      wsRef.current = null;
    };
  }, [appendStreamEvent, appendToolResults, development, finishTurn, flushPending, resetLiveTurn]);

  const send = useCallback(
    (actorId: string, content: WsContentItem[], durableId?: string) => {
      if (inFlightRef.current) {
        return false;
      }
      const userText = content
        .filter((item) => item.kind === "text" && item.text)
        .map((item) => item.text ?? "")
        .join("\n\n");

      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        pendingRef.current = { actorId, content, conversationId: durableId, userText };
        setOptimisticUserText(userText);
        beginTurn();
        setPhase("sending");
        setError(null);
        return true;
      }

      sendConversation(ws, actorId, content, durableId);
      setOptimisticUserText(userText);
      beginTurn();
      setPhase("sending");
      setError(null);
      return true;
    },
    [beginTurn],
  );

  const interrupt = useCallback((targetConversationId: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !targetConversationId) return;
    interruptConversation(ws, targetConversationId);
  }, []);

  const waitingForResponse = phase === "sending" && liveBlocks.length === 0;

  return {
    wsReady,
    phase,
    liveBlocks,
    error,
    optimisticUserText,
    events,
    activeConversationId,
    turnKey,
    waitingForResponse,
    send,
    interrupt,
  };
}

function parseFrame(raw: string): WsFrame | null {
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as WsFrame) : null;
  } catch {
    return null;
  }
}

export type { ConversationPhase };

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { toast } from "sonner";

import {
  connectWs,
  interruptConversation,
  sendConversation,
  subscribeConversationHistory,
  type WsContentItem,
} from "@/shared/lib/api";
import { describeWsError } from "@/shared/lib/api-errors";
import type { HistoryItem } from "@/shared/types/api";

import {
  contentItemsToText,
  createTranscriptState,
  isTerminalStreamStop,
  renderBlocksFromStreamEvent,
  renderBlocksFromToolResults,
  transcriptDisplayItems,
  transcriptReducer,
  type ConversationPhase,
} from "../lib/conversation-transcript";
import { shouldProcessConversationFrame } from "../lib/ws-frame";

const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

export type WsConnectionState = "connecting" | "connected" | "reconnecting";

interface WsFrame {
  id?: string;
  type?: string;
  payload?: Record<string, unknown>;
  error?: { code?: string; message?: string; detail?: Record<string, unknown> };
}

interface PendingSend {
  actorId: string;
  content: WsContentItem[];
  conversationId?: string;
}

export function useConversationSession({
  conversationId,
  history,
  isDraft,
  development,
  onHistoryAppend,
  onTurnComplete,
  onReconnect,
  onHistoryFallback,
}: {
  conversationId: string;
  history: HistoryItem[];
  isDraft: boolean;
  development: boolean;
  onHistoryAppend: (conversationId: string, item: HistoryItem) => void;
  /** Called after a terminal stream stop; must not re-fetch conversation history. */
  onTurnComplete: () => void;
  onReconnect?: () => void;
  onHistoryFallback?: () => void;
}) {
  const wsRef = useRef<WebSocket | null>(null);
  const pendingRef = useRef<PendingSend | null>(null);
  const onHistoryAppendRef = useRef(onHistoryAppend);
  const onTurnCompleteRef = useRef(onTurnComplete);
  const onReconnectRef = useRef(onReconnect);
  const onHistoryFallbackRef = useRef(onHistoryFallback);
  const historyRef = useRef(history);
  const conversationIdRef = useRef(conversationId);
  const liveBlockIndexRef = useRef(0);
  const turnKeyRef = useRef("");
  const activeCommandIdRef = useRef<string | null>(null);
  const inFlightRef = useRef(false);
  const terminalHandledRef = useRef(false);
  const historySubscriptionRef = useRef<string | null>(null);
  const turnAppendReceivedRef = useRef(false);
  const reconnectAttemptRef = useRef(0);
  const hadConnectedRef = useRef(false);
  const lastLiveSeqRef = useRef(0);

  const [transcript, dispatchTranscript] = useReducer(transcriptReducer, history, createTranscriptState);
  const [wsReady, setWsReady] = useState(false);
  const [wsConnectionState, setWsConnectionState] = useState<WsConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [activeConversationId, setActiveConversationId] = useState(isDraft ? "" : conversationId);

  onHistoryAppendRef.current = onHistoryAppend;
  onTurnCompleteRef.current = onTurnComplete;
  onReconnectRef.current = onReconnect;
  onHistoryFallbackRef.current = onHistoryFallback;
  historyRef.current = history;
  conversationIdRef.current = conversationId;

  const subscribedConversationId = activeConversationId || conversationId;

  const resetLiveTurn = useCallback(() => {
    turnKeyRef.current = "";
    liveBlockIndexRef.current = 0;
    dispatchTranscript({ type: "clear_live" });
  }, []);

  const finishTurn = useCallback(() => {
    inFlightRef.current = false;
  }, []);

  const finishTerminalTurn = useCallback(() => {
    if (terminalHandledRef.current) {
      return;
    }
    terminalHandledRef.current = true;
    dispatchTranscript({ type: "finish_turn" });
    finishTurn();
    activeCommandIdRef.current = null;
    resetLiveTurn();
    if (!turnAppendReceivedRef.current) {
      onHistoryFallbackRef.current?.();
    }
    turnAppendReceivedRef.current = false;
    onTurnCompleteRef.current();
  }, [finishTurn, resetLiveTurn]);

  const beginTurn = useCallback(() => {
    const nextTurnKey = `turn-${Date.now()}`;
    inFlightRef.current = true;
    terminalHandledRef.current = false;
    turnAppendReceivedRef.current = false;
    turnKeyRef.current = nextTurnKey;
    liveBlockIndexRef.current = 0;
    dispatchTranscript({ type: "begin_turn", turnKey: nextTurnKey, now: Date.now() });
  }, []);

  const beginRemoteTurn = useCallback(() => {
    if (inFlightRef.current) {
      return;
    }
    beginTurn();
  }, [beginTurn]);

  const markRemoteTurnActive = useCallback(() => {
    if (inFlightRef.current) {
      return;
    }
    const nextTurnKey = `remote-${Date.now()}`;
    inFlightRef.current = true;
    terminalHandledRef.current = false;
    turnAppendReceivedRef.current = false;
    turnKeyRef.current = nextTurnKey;
    liveBlockIndexRef.current = 0;
    dispatchTranscript({ type: "set_phase", phase: "streaming" });
  }, []);

  const beginReplayTurn = useCallback(() => {
    const nextTurnKey = `replay-${Date.now()}`;
    inFlightRef.current = true;
    terminalHandledRef.current = false;
    turnAppendReceivedRef.current = false;
    turnKeyRef.current = nextTurnKey;
    liveBlockIndexRef.current = 0;
    lastLiveSeqRef.current = 0;
    dispatchTranscript({ type: "begin_turn", turnKey: nextTurnKey, now: Date.now() });
    dispatchTranscript({ type: "set_phase", phase: "streaming" });
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
    dispatchTranscript({ type: "append_blocks", blocks });
  }, []);

  const appendToolResults = useCallback((results: unknown[]) => {
    const keyPrefix = turnKeyRef.current || "live";
    const blocks = renderBlocksFromToolResults(results, keyPrefix, () => liveBlockIndexRef.current++);
    if (!blocks.length) {
      return;
    }
    dispatchTranscript({ type: "append_blocks", blocks });
  }, []);

  const ensureHistorySubscription = useCallback((ws: WebSocket, targetConversationId: string | undefined) => {
    if (!targetConversationId || historySubscriptionRef.current === targetConversationId) {
      return;
    }
    historySubscriptionRef.current = targetConversationId;
    subscribeConversationHistory(ws, targetConversationId);
  }, []);

  const queueOptimisticUser = useCallback((content: WsContentItem[]) => {
    const text = contentItemsToText(content);
    if (!text) {
      return;
    }
    dispatchTranscript({
      type: "pending_user",
      clientKey: `pending-${Date.now()}`,
      text,
      now: Date.now(),
    });
  }, []);

  const flushPending = useCallback((ws: WebSocket) => {
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    ensureHistorySubscription(ws, pending.conversationId);
    const commandId = sendConversation(ws, pending.actorId, pending.content, pending.conversationId);
    activeCommandIdRef.current = commandId;
    beginTurn();
    setError(null);
  }, [beginTurn, ensureHistorySubscription]);

  const shouldProcessFrame = useCallback((frame: WsFrame): boolean => {
    const frameConversationId = typeof frame.payload?.conversation_id === "string"
      ? frame.payload.conversation_id
      : undefined;
    return shouldProcessConversationFrame(
      frameConversationId,
      subscribedConversationId,
      frame.id,
      activeCommandIdRef.current,
    );
  }, [subscribedConversationId]);

  const shouldProcessLiveFrame = useCallback((frame: WsFrame): boolean => {
    const liveSeq = numericPayloadValue(frame.payload?.live_seq);
    if (!liveSeq) {
      return true;
    }
    if (liveSeq <= lastLiveSeqRef.current) {
      return false;
    }
    lastLiveSeqRef.current = liveSeq;
    return true;
  }, []);

  useEffect(() => {
    if (!isDraft) {
      setActiveConversationId(conversationId);
    }

    historySubscriptionRef.current = null;
    dispatchTranscript({ type: "reset", history: historyRef.current });
    finishTurn();
    resetLiveTurn();
    setError(null);
    pendingRef.current = null;
    activeCommandIdRef.current = null;
    terminalHandledRef.current = false;
    turnAppendReceivedRef.current = false;
    lastLiveSeqRef.current = 0;
  }, [conversationId, finishTurn, isDraft, resetLiveTurn]);

  useEffect(() => {
    for (const item of history) {
      dispatchTranscript({ type: "history_append", item });
    }
  }, [history]);

  useEffect(() => {
    if (isDraft) {
      return;
    }
    const ws = wsRef.current;
    const target = activeConversationId || conversationId;
    if (!ws || ws.readyState !== WebSocket.OPEN || !target || !wsReady) {
      return;
    }
    if (historySubscriptionRef.current === target) {
      return;
    }
    ensureHistorySubscription(ws, target);
  }, [activeConversationId, conversationId, ensureHistorySubscription, isDraft, wsReady]);

  useEffect(() => {
    if (isDraft) {
      return;
    }

    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleReconnect = () => {
      if (disposed) return;
      const delay = Math.min(RECONNECT_BASE_MS * 2 ** reconnectAttemptRef.current, RECONNECT_MAX_MS);
      reconnectAttemptRef.current += 1;
      setWsConnectionState("reconnecting");
      setWsReady(false);
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        openSocket();
      }, delay);
    };

    const handleMessage = (event: MessageEvent) => {
      if (disposed) return;
      if (development) {
        setEvents((items) => [...items.slice(-100), String(event.data)]);
      }
      const frame = parseFrame(String(event.data));
      if (!frame) return;

      if (frame.type === "conversation.send.accepted") {
        const acceptedId = frame.payload?.conversation_id;
        if (typeof acceptedId === "string" && acceptedId) {
          setActiveConversationId(acceptedId);
        }
        return;
      }

      if (frame.type === "conversation.history.append") {
        const targetId = typeof frame.payload?.conversation_id === "string"
          ? frame.payload.conversation_id
          : "";
        const item = parseHistoryItem(frame.payload?.item);
        if (targetId && item) {
          turnAppendReceivedRef.current = true;
          dispatchTranscript({ type: "history_append", item });
          onHistoryAppendRef.current(targetId, item);
        }
        return;
      }

      if (frame.type === "conversation.interrupt.result") {
        if (frame.payload?.interrupted !== true) {
          toast.error("Could not interrupt conversation.");
        }
        return;
      }

      if (frame.type === "conversation.replay.start") {
        if (!shouldProcessFrame(frame)) {
          return;
        }
        beginReplayTurn();
        return;
      }

      if (frame.type === "conversation.replay.end") {
        return;
      }

      if (frame.type === "conversation.tool_results") {
        if (!shouldProcessFrame(frame)) {
          return;
        }
        if (!shouldProcessLiveFrame(frame)) {
          return;
        }
        if (!inFlightRef.current) {
          beginRemoteTurn();
        }
        const results = Array.isArray(frame.payload?.results) ? frame.payload.results : [];
        appendToolResults(results);
        return;
      }

      if (frame.type === "conversation.output") {
        if (!shouldProcessFrame(frame)) {
          return;
        }
        if (!shouldProcessLiveFrame(frame)) {
          return;
        }
        return;
      }

      if (frame.type === "conversation.stream") {
        if (!shouldProcessFrame(frame)) {
          return;
        }
        if (!shouldProcessLiveFrame(frame)) {
          return;
        }
        if (!inFlightRef.current) {
          beginRemoteTurn();
        }
        const streamPayload = frame.payload;
        const streamEvent = streamPayload?.event as Record<string, unknown> | undefined;
        const kind = streamEvent?.kind;
        if (
          kind === "text_delta" ||
          kind === "reasoning_delta" ||
          kind === "tool_name" ||
          kind === "tool_arguments_delta" ||
          kind === "tool_arguments_end" ||
          kind === "tool_result_delta" ||
          kind === "tool_result_end"
        ) {
          appendStreamEvent(streamEvent ?? {});
          return;
        }
        if (kind === "stream_stop") {
          const stopPayload = streamEvent?.payload && typeof streamEvent.payload === "object"
            ? (streamEvent.payload as Record<string, unknown>)
            : {};
          if (isTerminalStreamStop(stopPayload)) {
            finishTerminalTurn();
          } else {
            dispatchTranscript({ type: "mark_tools_completed" });
          }
          return;
        }
        return;
      }

      if (frame.type === "error") {
        if (!shouldProcessFrame(frame)) {
          return;
        }
        const message = describeWsError(frame.error);
        const turnAlreadyFinished = terminalHandledRef.current;
        if (turnAlreadyFinished) {
          toast.warning(message);
          onTurnCompleteRef.current();
          return;
        }
        setError(message);
        dispatchTranscript({ type: "set_phase", phase: "error" });
        finishTurn();
        activeCommandIdRef.current = null;
        resetLiveTurn();
        toast.error(message);
        onTurnCompleteRef.current();
      }
    };

    const openSocket = () => {
      if (disposed) return;
      const ws = connectWs();
      wsRef.current = ws;
      setWsReady(false);
      setWsConnectionState(reconnectAttemptRef.current > 0 ? "reconnecting" : "connecting");

      ws.onopen = () => {
        if (disposed) return;
        const reconnected = hadConnectedRef.current;
        hadConnectedRef.current = true;
        reconnectAttemptRef.current = 0;
        setWsReady(true);
        setWsConnectionState("connected");
        setError(null);
        historySubscriptionRef.current = null;
        ensureHistorySubscription(ws, conversationIdRef.current);
        flushPending(ws);
        if (reconnected) {
          onReconnectRef.current?.();
        }
      };

      ws.onerror = () => {
        if (disposed) return;
        setError("WebSocket connection failed.");
        dispatchTranscript({ type: "set_phase", phase: "error" });
        finishTurn();
      };

      ws.onclose = () => {
        if (disposed) return;
        wsRef.current = null;
        setWsReady(false);
        scheduleReconnect();
      };

      ws.onmessage = handleMessage;
    };

    openSocket();

    return () => {
      disposed = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      wsRef.current?.close();
      wsRef.current = null;
      hadConnectedRef.current = false;
      reconnectAttemptRef.current = 0;
    };
  }, [
    appendStreamEvent,
    appendToolResults,
    beginRemoteTurn,
    beginReplayTurn,
    development,
    ensureHistorySubscription,
    finishTerminalTurn,
    finishTurn,
    flushPending,
    isDraft,
    resetLiveTurn,
    shouldProcessLiveFrame,
    shouldProcessFrame,
  ]);

  const send = useCallback(
    (actorId: string, content: WsContentItem[], durableId?: string) => {
      if (isDraft) {
        return false;
      }
      if (inFlightRef.current) {
        return false;
      }

      queueOptimisticUser(content);

      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        pendingRef.current = { actorId, content, conversationId: durableId };
        beginTurn();
        setError(null);
        return true;
      }

      ensureHistorySubscription(ws, durableId);
      const commandId = sendConversation(ws, actorId, content, durableId);
      activeCommandIdRef.current = commandId;
      beginTurn();
      setError(null);
      return true;
    },
    [beginTurn, ensureHistorySubscription, isDraft, queueOptimisticUser],
  );

  const interrupt = useCallback((targetConversationId: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !targetConversationId) return;
    interruptConversation(ws, targetConversationId);
  }, []);

  const displayItems = transcriptDisplayItems(transcript);
  const waitingForResponse = transcript.phase === "sending" && transcript.liveBlocks.length === 0;

  return {
    wsReady: isDraft ? false : wsReady,
    wsConnectionState: isDraft ? "connecting" as const : wsConnectionState,
    phase: transcript.phase,
    liveBlocks: transcript.liveBlocks,
    error,
    events,
    activeConversationId,
    turnKey: transcript.turnKey ?? "",
    displayItems,
    waitingForResponse,
    send,
    interrupt,
    markRemoteTurnActive,
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

function parseHistoryItem(value: unknown): HistoryItem | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const item = value as Record<string, unknown>;
  if (typeof item.seq !== "number" || typeof item.kind !== "string") {
    return null;
  }
  return {
    seq: item.seq,
    kind: item.kind,
    payload: item.payload && typeof item.payload === "object"
      ? (item.payload as Record<string, unknown>)
      : {},
    created_at: typeof item.created_at === "string" ? item.created_at : null,
  };
}

function numericPayloadValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export type { ConversationPhase };

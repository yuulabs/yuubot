import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { toast } from "sonner";

import {
  connectWs,
  interruptConversation,
  openConversation,
  sendConversation,
  type WsContentItem,
} from "@/shared/lib/api";
import { describeWsError } from "@/shared/lib/api-errors";
import type { HistoryItem } from "@/shared/types/api";

import {
  contentItemsToText,
  createTranscriptState,
  transcriptDisplayItems,
  transcriptReducer,
  type ConversationPhase,
  type StreamEventFrame,
} from "../lib/conversation-transcript";

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
  conversationId: string;
}

export function useConversationSession({
  conversationId,
  isDraft,
  development,
  onTurnComplete,
}: {
  conversationId: string;
  isDraft: boolean;
  development: boolean;
  onTurnComplete: () => void;
}) {
  const wsRef = useRef<WebSocket | null>(null);
  const pendingRef = useRef<PendingSend | null>(null);
  const localVersionRef = useRef(0);
  const conversationIdRef = useRef(conversationId);
  const disposedRef = useRef(false);
  const resubscribingRef = useRef(false);
  const onTurnCompleteRef = useRef(onTurnComplete);

  const [transcript, dispatch] = useReducer(transcriptReducer, undefined, createTranscriptState);
  const [wsReady, setWsReady] = useState(false);
  const [wsConnectionState, setWsConnectionState] = useState<WsConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<string[]>([]);

  conversationIdRef.current = conversationId;
  onTurnCompleteRef.current = onTurnComplete;

  const requestSnapshot = useCallback((ws: WebSocket) => {
    if (resubscribingRef.current || ws.readyState !== WebSocket.OPEN) return;
    resubscribingRef.current = true;
    setWsReady(false);
    dispatch({ type: "gap" });
    openConversation(ws, conversationIdRef.current);
  }, []);

  const flushPending = useCallback((ws: WebSocket) => {
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    sendConversation(ws, pending.actorId, pending.content, pending.conversationId);
  }, []);

  useEffect(() => {
    if (isDraft) return;
    disposedRef.current = false;
    localVersionRef.current = 0;
    resubscribingRef.current = false;
    let reconnectAttempt = 0;
    let reconnectTimer: number | undefined;

    const handleMessage = (message: MessageEvent<string>) => {
      const frame = parseFrame(message.data);
      if (!frame) return;
      if (development) {
        setEvents((current) => [...current.slice(-99), JSON.stringify(frame, null, 2)]);
      }
      const payload = frame.payload;
      if (payload?.conversation_id !== conversationIdRef.current) {
        if (frame.type === "error" && frame.id?.startsWith("open-")) {
          setError(describeWsError(frame.error));
          dispatch({ type: "error" });
        }
        return;
      }

      if (frame.type === "conversation.snapshot") {
        const prefix = parseHistoryItems(payload.prefix);
        const livingChunks = parseStreamChunks(payload.living_chunks);
        const version = numericVersion(payload.version);
        localVersionRef.current = version;
        resubscribingRef.current = false;
        dispatch({ type: "snapshot", prefix, livingChunks, version });
        setWsReady(true);
        setError(null);
        const ws = wsRef.current;
        if (ws) flushPending(ws);
        return;
      }

      if (frame.type === "conversation.delta" || frame.type === "conversation.commit") {
        const version = numericVersion(payload.version);
        if (version <= localVersionRef.current) return;
        if (version !== localVersionRef.current + 1) {
          const ws = wsRef.current;
          if (ws) requestSnapshot(ws);
          return;
        }
        localVersionRef.current = version;
        if (frame.type === "conversation.delta") {
          const chunk = parseStreamChunk(payload.chunk);
          if (chunk) dispatch({ type: "delta", chunk, version });
          return;
        }
        const continues = payload.continues === true;
        dispatch({ type: "commit", append: parseHistoryItems(payload.append), continues, version });
        if (!continues) onTurnCompleteRef.current();
        return;
      }

      if (frame.type === "conversation.error") {
        const message = typeof payload.error === "string" ? payload.error : "Conversation failed.";
        setError(message);
        dispatch({ type: "error" });
        toast.error(message);
        return;
      }

      if (frame.type === "error") {
        const message = describeWsError(frame.error);
        setError(message);
        dispatch({ type: "error" });
        toast.error(message);
      }
    };

    const openSocket = () => {
      if (disposedRef.current) return;
      const ws = connectWs();
      wsRef.current = ws;
      setWsReady(false);
      setWsConnectionState(reconnectAttempt ? "reconnecting" : "connecting");
      ws.onopen = () => {
        if (disposedRef.current) return;
        reconnectAttempt = 0;
        resubscribingRef.current = false;
        setWsConnectionState("connected");
        requestSnapshot(ws);
      };
      ws.onmessage = handleMessage;
      ws.onerror = () => {
        if (!disposedRef.current) setError("WebSocket connection failed.");
      };
      ws.onclose = () => {
        if (disposedRef.current) return;
        wsRef.current = null;
        setWsReady(false);
        resubscribingRef.current = false;
        reconnectAttempt += 1;
        const delay = Math.min(RECONNECT_BASE_MS * 2 ** (reconnectAttempt - 1), RECONNECT_MAX_MS);
        reconnectTimer = window.setTimeout(openSocket, delay);
      };
    };

    openSocket();
    return () => {
      disposedRef.current = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [development, flushPending, isDraft, requestSnapshot, conversationId]);

  const send = useCallback((actorId: string, content: WsContentItem[], durableId?: string) => {
    const targetId = durableId || conversationIdRef.current;
    if (isDraft || !targetId || transcript.continues || transcript.pendingUser) return false;
    dispatch({
      type: "pending_user",
      clientKey: `pending-${Date.now()}`,
      text: contentItemsToText(content),
      now: Date.now(),
    });
    setError(null);
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !wsReady) {
      pendingRef.current = { actorId, content, conversationId: targetId };
      return true;
    }
    sendConversation(ws, actorId, content, targetId);
    return true;
  }, [isDraft, transcript.continues, transcript.pendingUser, wsReady]);

  const interrupt = useCallback((targetId: string) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN && targetId) interruptConversation(ws, targetId);
  }, []);

  const liveBlocks = transcriptDisplayItems({ ...transcript, prefix: [] })
    .flatMap((item) => item.blocks);
  const phase: ConversationPhase = transcript.pendingUser
    ? "sending"
    : transcript.continues
      ? "streaming"
      : transcript.phase === "error" ? "error" : "idle";

  return {
    wsReady: isDraft ? false : wsReady,
    wsConnectionState: isDraft ? "connecting" as const : wsConnectionState,
    phase,
    liveBlocks,
    error,
    events,
    activeConversationId: isDraft ? "" : conversationId,
    turnKey: "",
    displayItems: transcriptDisplayItems(transcript),
    waitingForResponse: phase === "sending" && liveBlocks.length === 0,
    send,
    interrupt,
  };
}

function parseFrame(raw: string): WsFrame | null {
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" ? parsed as WsFrame : null;
  } catch {
    return null;
  }
}

function parseHistoryItems(value: unknown): HistoryItem[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    if (typeof record.seq !== "number" || typeof record.kind !== "string") return [];
    return [{
      seq: record.seq,
      kind: record.kind,
      payload: record.payload && typeof record.payload === "object"
        ? record.payload as Record<string, unknown>
        : {},
      created_at: typeof record.created_at === "string" ? record.created_at : null,
    }];
  });
}

function parseStreamChunks(value: unknown): StreamEventFrame[] {
  return Array.isArray(value)
    ? value.flatMap((chunk) => parseStreamChunk(chunk) ?? [])
    : [];
}

function parseStreamChunk(value: unknown): StreamEventFrame | null {
  if (!value || typeof value !== "object") return null;
  const chunk = value as Record<string, unknown>;
  if (typeof chunk.group_id !== "string" || typeof chunk.kind !== "string") return null;
  return {
    group_id: chunk.group_id,
    kind: chunk.kind,
    payload: chunk.payload && typeof chunk.payload === "object"
      ? chunk.payload as Record<string, unknown>
      : {},
  };
}

function numericVersion(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : 0;
}

export type { ConversationPhase };

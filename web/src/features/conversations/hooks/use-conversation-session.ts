import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { toast } from "sonner";

import {
  connectWs,
  CONVERSATION_HISTORY_PAGE_SIZE,
  getConversationHistory,
  answerConversation,
  interruptConversation,
  openConversation,
  sendConversation,
  type WsContentItem,
  type AskUserAnswerInput,
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
import {
  conversationCommandReducer,
  createConversationCommandState,
  type ConversationCommandEvent,
  type PendingSendCommand,
} from "../lib/conversation-command-state";

const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

export type WsConnectionState = "connecting" | "connected" | "reconnecting";

interface WsFrame {
  id?: string;
  type?: string;
  payload?: Record<string, unknown>;
  error?: { code?: string; message?: string; detail?: Record<string, unknown> };
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
  const pendingRef = useRef<PendingSendCommand | null>(null);
  const commandStateRef = useRef(createConversationCommandState());
  const localVersionRef = useRef(0);
  const conversationIdRef = useRef(conversationId);
  const disposedRef = useRef(false);
  const resubscribingRef = useRef(false);
  const onTurnCompleteRef = useRef(onTurnComplete);

  const [transcript, dispatch] = useReducer(transcriptReducer, undefined, createTranscriptState);
  const [commandState, setCommandState] = useState(commandStateRef.current);
  const [wsReady, setWsReady] = useState(false);
  const [wsConnectionState, setWsConnectionState] = useState<WsConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [loadingOlder, setLoadingOlder] = useState(false);

  conversationIdRef.current = conversationId;
  onTurnCompleteRef.current = onTurnComplete;

  const applyCommandEvent = useCallback((event: ConversationCommandEvent) => {
    const next = conversationCommandReducer(commandStateRef.current, event);
    commandStateRef.current = next;
    setCommandState(next);
    return next;
  }, []);

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
    sendConversation(
      ws,
      pending.actorId,
      pending.content,
      pending.conversationId,
      pending.commandId,
    );
  }, []);

  useEffect(() => {
    if (isDraft) return;
    disposedRef.current = false;
    pendingRef.current = null;
    commandStateRef.current = createConversationCommandState();
    setCommandState(commandStateRef.current);
    localVersionRef.current = 0;
    resubscribingRef.current = false;
    let reconnectAttempt = 0;
    let reconnectTimer: number | undefined;

    const handleMessage = (message: MessageEvent<string>) => {
      const frame = parseFrame(message.data);
      if (!frame) return;
      if (development) {
        setEvents((current) => [...current.slice(-99), summarizeFrame(frame, message.data.length)]);
      }
      if (frame.type === "error") {
        const message = describeWsError(frame.error);
        const pending = commandStateRef.current.pending;
        if (pending && frame.id === pending.commandId) {
          applyCommandEvent({ type: "send_rejected", commandId: pending.commandId });
          pendingRef.current = null;
          dispatch({ type: "clear_pending" });
        } else if (frame.id?.startsWith("open-")) {
          dispatch({ type: "error" });
        }
        setError(message);
        toast.error(message);
        return;
      }
      const payload = frame.payload;
      if (payload?.conversation_id !== conversationIdRef.current) {
        return;
      }

      if (frame.type === "conversation.send.accepted") {
        if (frame.id) {
          applyCommandEvent({ type: "send_accepted", commandId: frame.id });
        }
        return;
      }

      if (frame.type === "conversation.interrupt.result") {
        if (frame.id) {
          applyCommandEvent({
            type: "interrupt_result",
            commandId: frame.id,
            accepted: payload.interrupted === true,
          });
        }
        return;
      }

      if (frame.type === "conversation.snapshot") {
        const prefix = parseHistoryItems(payload.history);
        const livingChunks = parseStreamChunks(payload.living_chunks);
        const pending = commandStateRef.current.pending;
        const inputCommitted = pending !== null && prefix.some(
          (item) => item.seq > pending.baselineSeq && isUserInputItem(item),
        );
        if (inputCommitted) {
          applyCommandEvent({ type: "user_input_committed" });
        }
        const serviceContinues = typeof payload.continues === "boolean"
          ? payload.continues
          : livingChunks.length > 0;
        if (!serviceContinues) {
          applyCommandEvent({ type: "terminal_commit" });
        }
        const version = numericVersion(payload.version);
        localVersionRef.current = version;
        resubscribingRef.current = false;
        dispatch({
          type: "snapshot",
          prefix,
          livingChunks,
          version,
          continues: serviceContinues,
          preservePending: pending !== null && !inputCommitted,
          hasOlder: payload.has_older === true,
          firstSeq: typeof payload.first_seq === "number" ? payload.first_seq : null,
        });
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
        const append = parseHistoryItems(payload.append);
        if (append.some(isUserInputItem)) {
          applyCommandEvent({ type: "user_input_committed" });
        }
        dispatch({ type: "commit", append, continues, version });
        if (!continues) {
          applyCommandEvent({ type: "terminal_commit" });
          onTurnCompleteRef.current();
        }
        return;
      }

      if (frame.type === "conversation.error") {
        const message = typeof payload.error === "string" ? payload.error : "Conversation failed.";
        setError(message);
        dispatch({ type: "error" });
        toast.error(message);
        return;
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
  }, [applyCommandEvent, development, flushPending, isDraft, requestSnapshot, conversationId]);

  const send = useCallback((actorId: string, content: WsContentItem[], durableId?: string) => {
    const targetId = durableId || conversationIdRef.current;
    if (
      isDraft
      || !targetId
      || transcript.continues
      || transcript.pendingUser
      || commandStateRef.current.pending
      || commandStateRef.current.interrupting
    ) return false;
    const commandId = `send-${Date.now()}-${nextCommandSequence()}`;
    const baselineSeq = transcript.prefix.reduce(
      (highest, item) => Math.max(highest, item.seq),
      0,
    );
    const payload = { actorId, content, conversationId: targetId };
    applyCommandEvent({
      type: "send_local",
      commandId,
      baselineSeq,
      payload,
    });
    dispatch({
      type: "pending_user",
      clientKey: `pending-${Date.now()}`,
      text: contentItemsToText(content),
      now: Date.now(),
    });
    setError(null);
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !wsReady) {
      pendingRef.current = {
        ...payload,
        commandId,
        baselineSeq,
        stage: "local_pending",
      };
      return true;
    }
    sendConversation(ws, actorId, content, targetId, commandId);
    return true;
  }, [applyCommandEvent, isDraft, transcript.continues, transcript.pendingUser, transcript.prefix, wsReady]);

  const interrupt = useCallback((targetId: string) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN && targetId) {
      const commandId = interruptConversation(ws, targetId);
      applyCommandEvent({ type: "interrupt_requested", commandId });
    }
  }, [applyCommandEvent]);

  const answerQuestion = useCallback((toolCallId: string, answers: AskUserAnswerInput[], skipped = false) => {
    const ws = wsRef.current;
    const targetId = conversationIdRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !wsReady || !targetId) return false;
    setError(null);
    answerConversation(ws, targetId, toolCallId, answers, skipped);
    return true;
  }, [wsReady]);

  const clearRetrySend = useCallback(() => {
    applyCommandEvent({ type: "retry_cleared" });
  }, [applyCommandEvent]);

  const loadOlder = useCallback(async () => {
    const targetId = conversationIdRef.current;
    if (!targetId || transcript.firstSeq === null || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const page = await getConversationHistory(targetId, {
        before_seq: transcript.firstSeq,
        limit: CONVERSATION_HISTORY_PAGE_SIZE,
      });
      dispatch({ type: "prepend", items: page.items, hasOlder: page.has_more, firstSeq: page.first_seq });
    } finally {
      setLoadingOlder(false);
    }
  }, [loadingOlder, transcript.firstSeq]);

  const liveBlocks = transcriptDisplayItems({ ...transcript, prefix: [] })
    .flatMap((item) => item.blocks);
  const displayItems = useMemo(() => transcriptDisplayItems(transcript), [transcript]);
  const awaitingInput = displayItems.some((item) => item.blocks.some(
    (block) => block.toolName === "ask_user" && block.toolStatus !== "completed",
  ));
  const phase: ConversationPhase = commandState.interrupting
    ? "interrupting"
    : transcript.pendingUser || commandState.pending
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
    displayItems,
    hasOlder: transcript.hasOlder,
    loadingOlder,
    loadOlder,
    awaitingInput,
    waitingForResponse: phase === "sending" && liveBlocks.length === 0,
    send,
    interrupt,
    answerQuestion,
    retrySend: commandState.retry,
    clearRetrySend,
  };
}

function summarizeFrame(frame: WsFrame, bytes: number): string {
  const payload = frame.payload ?? {};
  const version = typeof payload.version === "number" ? ` v${payload.version}` : "";
  const count = Array.isArray(payload.history)
    ? ` history=${payload.history.length}`
    : Array.isArray(payload.append) ? ` append=${payload.append.length}` : "";
  return `${frame.type ?? "unknown"}${version}${count} (${bytes} bytes)`;
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

function isUserInputItem(item: HistoryItem): boolean {
  return item.kind === "input" && String(item.payload.role ?? "user") === "user";
}

let commandSequence = 0;

function nextCommandSequence(): number {
  commandSequence += 1;
  return commandSequence;
}

export type { ConversationPhase };

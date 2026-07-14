import type { WsContentItem } from "@/shared/lib/api";

export interface SendPayload {
  actorId: string;
  content: WsContentItem[];
  conversationId: string;
}

export interface PendingSendCommand extends SendPayload {
  commandId: string;
  baselineSeq: number;
  stage: "local_pending" | "accepted";
}

export interface ConversationCommandState {
  pending: PendingSendCommand | null;
  retry: SendPayload | null;
  interruptCommandId: string | null;
  interrupting: boolean;
}

export type ConversationCommandEvent =
  | { type: "send_local"; commandId: string; baselineSeq: number; payload: SendPayload }
  | { type: "send_accepted"; commandId: string }
  | { type: "send_rejected"; commandId: string }
  | { type: "user_input_committed" }
  | { type: "retry_cleared" }
  | { type: "interrupt_requested"; commandId: string }
  | { type: "interrupt_result"; commandId: string; accepted: boolean }
  | { type: "terminal_commit" };

export function createConversationCommandState(): ConversationCommandState {
  return {
    pending: null,
    retry: null,
    interruptCommandId: null,
    interrupting: false,
  };
}

export function conversationCommandReducer(
  state: ConversationCommandState,
  event: ConversationCommandEvent,
): ConversationCommandState {
  if (event.type === "send_local") {
    return {
      ...state,
      pending: {
        ...event.payload,
        commandId: event.commandId,
        baselineSeq: event.baselineSeq,
        stage: "local_pending",
      },
      retry: null,
    };
  }
  if (event.type === "send_accepted") {
    if (state.pending?.commandId !== event.commandId) return state;
    return { ...state, pending: { ...state.pending, stage: "accepted" } };
  }
  if (event.type === "send_rejected") {
    if (state.pending?.commandId !== event.commandId) return state;
    const { actorId, content, conversationId } = state.pending;
    return {
      ...state,
      pending: null,
      retry: { actorId, content, conversationId },
    };
  }
  if (event.type === "user_input_committed") {
    return { ...state, pending: null, retry: null };
  }
  if (event.type === "retry_cleared") {
    return { ...state, retry: null };
  }
  if (event.type === "interrupt_requested") {
    return {
      ...state,
      interruptCommandId: event.commandId,
      interrupting: true,
    };
  }
  if (event.type === "interrupt_result") {
    if (state.interruptCommandId !== event.commandId) return state;
    return {
      ...state,
      interruptCommandId: event.accepted ? event.commandId : null,
      interrupting: event.accepted,
    };
  }
  if (event.type === "terminal_commit") {
    return { ...state, interruptCommandId: null, interrupting: false };
  }
  return state;
}


import { BASE } from "./client";

export function connectWs(): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return new WebSocket(`${protocol}//${window.location.host}${BASE}/ws`);
}

export interface WsContentItem {
  kind: string;
  text?: string;
  path?: string;
  url?: string;
  mime?: string;
  meta?: Record<string, unknown>;
}

export function sendConversation(
  ws: WebSocket,
  actorId: string,
  content: WsContentItem[],
  conversationId?: string,
  commandId = `send-${Date.now()}`,
): string {
  ws.send(
    JSON.stringify({
      id: commandId,
      type: "conversation.send",
      payload: {
        actor_id: actorId,
        conversation_id: conversationId,
        content,
      },
    }),
  );
  return commandId;
}

export function interruptConversation(ws: WebSocket, conversationId: string, commandId = `interrupt-${Date.now()}`) {
  ws.send(
    JSON.stringify({
      id: commandId,
      type: "conversation.interrupt",
      payload: { conversation_id: conversationId },
    }),
  );
  return commandId;
}

export function subscribeConversationHistory(
  ws: WebSocket,
  conversationId: string,
  commandId = `history-${Date.now()}`,
): string {
  ws.send(
    JSON.stringify({
      id: commandId,
      type: "conversation.history.subscribe",
      payload: { conversation_id: conversationId },
    }),
  );
  return commandId;
}

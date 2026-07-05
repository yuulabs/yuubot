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

export function sendConversation(ws: WebSocket, actorId: string, content: WsContentItem[], conversationId?: string) {
  ws.send(
    JSON.stringify({
      id: `send-${Date.now()}`,
      type: "conversation.send",
      payload: {
        actor_id: actorId,
        conversation_id: conversationId,
        content,
      },
    }),
  );
}

export function interruptConversation(ws: WebSocket, conversationId: string) {
  ws.send(
    JSON.stringify({
      type: "conversation.interrupt",
      payload: { conversation_id: conversationId },
    }),
  );
}

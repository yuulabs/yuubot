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

export function openConversation(
  ws: WebSocket,
  conversationId: string,
  commandId = `open-${Date.now()}`,
): string {
  ws.send(
    JSON.stringify({
      id: commandId,
      type: "conversation.open",
      payload: { conversation_id: conversationId },
    }),
  );
  return commandId;
}

export function closeConversation(ws: WebSocket, conversationId: string, commandId = `close-${Date.now()}`): string {
  ws.send(JSON.stringify({
    id: commandId,
    type: "conversation.close",
    payload: { conversation_id: conversationId },
  }));
  return commandId;
}

export function subscribeTask(
  ws: WebSocket,
  taskId: string,
  commandId = `task-sub-${Date.now()}`,
): string {
  ws.send(
    JSON.stringify({
      id: commandId,
      type: "task.subscribe",
      payload: { task_id: taskId },
    }),
  );
  return commandId;
}

export function sendTaskStdinWs(
  ws: WebSocket,
  taskId: string,
  text: string,
  commandId = `task-stdin-${Date.now()}`,
): string {
  ws.send(
    JSON.stringify({
      id: commandId,
      type: "task.stdin",
      payload: { task_id: taskId, text },
    }),
  );
  return commandId;
}

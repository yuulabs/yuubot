import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { connectWs } from "@/shared/lib/api";

export function useNotificationListener() {
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let disposed = false;
    const ws = connectWs();
    wsRef.current = ws;
    ws.onopen = () => {
      if (disposed) return;
      ws.send(JSON.stringify({ type: "runtime.events.subscribe", payload: { kinds: ["notification.delivered"] } }));
    };
    ws.onmessage = (event) => {
      if (disposed) return;
      let frame: { type?: string; payload?: { kind?: string; event?: Record<string, unknown> } };
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      if (frame.type !== "runtime.event" || frame.payload?.kind !== "notification.delivered") return;
      const data = frame.payload.event ?? {};
      const title = typeof data.title === "string" ? data.title : "Reminder";
      const body = typeof data.body === "string" ? data.body : "";
      toast(title, { description: body });
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification(title, { body });
      }
    };
    return () => {
      disposed = true;
      ws.close();
      wsRef.current = null;
    };
  }, []);
}

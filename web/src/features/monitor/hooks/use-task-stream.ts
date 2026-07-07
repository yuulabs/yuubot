import { useEffect, useRef, useState } from "react";

import { connectWs, subscribeTask } from "@/shared/lib/api";

export function useTaskStream(taskId: string | undefined) {
  const [liveStdout, setLiveStdout] = useState("");
  const [liveStatus, setLiveStatus] = useState<string | undefined>(undefined);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!taskId) {
      setLiveStdout("");
      setLiveStatus(undefined);
      return;
    }

    const ws = connectWs();
    wsRef.current = ws;
    let closed = false;

    ws.addEventListener("open", () => {
      if (!closed) subscribeTask(ws, taskId);
    });

    ws.addEventListener("message", (event) => {
      if (closed) return;
      const frame = JSON.parse(String(event.data)) as {
        type?: string;
        payload?: { task_id?: string; status?: string; stdout?: string };
      };
      if (frame.type !== "task.event") return;
      const payload = frame.payload;
      if (!payload || payload.task_id !== taskId) return;
      if (payload.stdout) {
        setLiveStdout((current) => current + payload.stdout);
      }
      if (payload.status) {
        setLiveStatus(payload.status);
      }
    });

    return () => {
      closed = true;
      ws.close();
      wsRef.current = null;
    };
  }, [taskId]);

  return { liveStdout, liveStatus, ws: wsRef.current };
}

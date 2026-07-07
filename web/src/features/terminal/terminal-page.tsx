import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { Plug, PlugZap, SquareTerminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DenseSection, Page } from "@/shared/components";

type TerminalStatus = "idle" | "connecting" | "open" | "closed" | "error";

export function TerminalPage() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<TerminalStatus>("idle");

  useEffect(() => {
    const terminal = new Terminal({
      cursorBlink: true,
      convertEol: true,
      rows: 28,
      cols: 100,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      fontSize: 13,
      theme: {
        background: "#111827",
        foreground: "#e5e7eb",
        cursor: "#ffffff",
      },
    });
    terminalRef.current = terminal;
    if (containerRef.current) {
      terminal.open(containerRef.current);
    }
    terminal.onData((data) => {
      socketRef.current?.send(JSON.stringify({ type: "terminal.input", payload: { data } }));
    });
    return () => {
      socketRef.current?.close();
      terminal.dispose();
    };
  }, []);

  function openTerminal() {
    const terminal = terminalRef.current;
    if (!terminal) return;
    socketRef.current?.close();
    terminal.clear();
    setStatus("connecting");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/terminal/ws`);
    socketRef.current = socket;
    socket.addEventListener("open", () => {
      setStatus("open");
      socket.send(JSON.stringify({ type: "terminal.open", payload: { command: "", cwd: "~", rows: terminal.rows, cols: terminal.cols } }));
    });
    socket.addEventListener("message", (event) => {
      const frame = parseFrame(event.data);
      if (!frame) return;
      if (frame.type === "terminal.output" && typeof frame.payload.data === "string") {
        terminal.write(frame.payload.data);
      } else if (frame.type === "terminal.opened") {
        terminal.writeln("\r\n[terminal opened]");
      } else if (frame.type === "terminal.exited" || frame.type === "terminal.closed") {
        setStatus("closed");
        terminal.writeln(`\r\n[${frame.type}]`);
      } else if (frame.type === "terminal.error") {
        setStatus("error");
        terminal.writeln(`\r\n[terminal error] ${String(frame.payload.message ?? "")}`);
      }
    });
    socket.addEventListener("close", () => {
      setStatus((current) => current === "error" ? "error" : "closed");
    });
    socket.addEventListener("error", () => {
      setStatus("error");
    });
  }

  function closeTerminal() {
    socketRef.current?.send(JSON.stringify({ type: "terminal.close", payload: {} }));
    socketRef.current?.close();
    setStatus("closed");
  }

  return (
    <Page title="Terminal" sub="Server-side admin PTY for native CLI checks and diagnostics.">
      <div className="dense-stack">
        <DenseSection
          title="Server terminal"
          description="Connects to an interactive shell from the home directory."
          actions={
            <>
              <Button onClick={openTerminal} disabled={status === "connecting" || status === "open"}>
                <PlugZap size={14} />
                <span>Connect</span>
              </Button>
              <Button variant="outline" onClick={closeTerminal} disabled={status !== "open"}>
                <Plug size={14} />
                <span>Disconnect</span>
              </Button>
            </>
          }
        >
          <div className="terminal-status-row">
            <SquareTerminal size={16} />
            <span className={`dense-chip${status === "open" ? " dense-chip--ok" : status === "error" ? " dense-chip--danger" : " dense-chip--muted"}`}>
              {status}
            </span>
          </div>
        </DenseSection>
        <div ref={containerRef} className="terminal-frame" />
      </div>
    </Page>
  );
}

function parseFrame(raw: string): { type: string; payload: Record<string, unknown> } | null {
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const frame = parsed as Record<string, unknown>;
    const payload = frame.payload;
    return {
      type: typeof frame.type === "string" ? frame.type : "",
      payload: payload && typeof payload === "object" && !Array.isArray(payload) ? payload as Record<string, unknown> : {},
    };
  } catch {
    return null;
  }
}

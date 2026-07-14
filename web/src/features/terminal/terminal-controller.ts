import { ClipboardAddon, type IClipboardProvider } from "@xterm/addon-clipboard";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon, type ISearchResultChangeEvent } from "@xterm/addon-search";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";

import { terminalOptions } from "./terminal-options";
import { terminalShortcut, type TerminalShortcut } from "./terminal-shortcuts";

export type TerminalStatus = "idle" | "connecting" | "open" | "closing" | "closed" | "error";

interface ControllerEvents {
  status(status: TerminalStatus): void;
  search(): void;
  searchResults(results: ISearchResultChangeEvent): void;
  fontSize(size: number): void;
  notify(message: string): void;
}

const DEFAULT_FONT_SIZE = 13;
const MIN_FONT_SIZE = 10;
const MAX_FONT_SIZE = 22;
const FONT_STORAGE_KEY = "yuubot.terminal.font-size";

export class TerminalController {
  readonly terminal: Terminal;
  private readonly fitAddon = new FitAddon();
  private readonly searchAddon = new SearchAddon();
  private readonly events: ControllerEvents;
  private socket: WebSocket | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private status: TerminalStatus = "idle";
  private disposed = false;

  constructor(container: HTMLElement, events: ControllerEvents) {
    this.events = events;
    this.terminal = new Terminal(terminalOptions(savedFontSize()));
    this.terminal.loadAddon(this.fitAddon);
    this.terminal.loadAddon(this.searchAddon);
    this.terminal.loadAddon(new Unicode11Addon());
    this.terminal.unicode.activeVersion = "11";
    this.terminal.loadAddon(new ClipboardAddon(undefined, osc52Clipboard(events.notify)));
    this.terminal.loadAddon(new WebLinksAddon((event, uri) => {
      if (!event.ctrlKey && !event.metaKey) return;
      window.open(uri, "_blank", "noopener,noreferrer");
    }));
    this.terminal.open(container);
    this.terminal.onData((data) => this.send("terminal.input", { data }));
    this.terminal.onResize(({ rows, cols }) => this.send("terminal.resize", { rows, cols }));
    this.searchAddon.onDidChangeResults((result) => this.events.searchResults(result));
    this.terminal.attachCustomKeyEventHandler((event) => this.handleKey(event));
    this.resizeObserver = new ResizeObserver(() => this.fit());
    this.resizeObserver.observe(container);
    requestAnimationFrame(() => { this.fit(); this.terminal.focus(); });
    this.events.fontSize(this.terminal.options.fontSize ?? DEFAULT_FONT_SIZE);
  }

  connect(): void {
    if (this.status === "connecting" || this.status === "open" || this.status === "closing") return;
    this.socket?.close();
    this.terminal.clear();
    this.setStatus("connecting");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/terminal/ws`);
    this.socket = socket;
    socket.addEventListener("open", () => {
      if (socket !== this.socket) return;
      this.fit();
      socket.send(JSON.stringify({ type: "terminal.open", payload: { command: "", cwd: "~", rows: this.terminal.rows, cols: this.terminal.cols } }));
    });
    socket.addEventListener("message", (event) => this.onMessage(socket, event.data));
    socket.addEventListener("close", () => {
      if (socket !== this.socket) return;
      this.socket = null;
      if (this.status !== "error") this.setStatus("closed");
    });
    socket.addEventListener("error", () => {
      if (socket !== this.socket) return;
      this.setStatus("error");
      this.events.notify("Terminal WebSocket connection failed.");
    });
  }

  disconnect(): void {
    if (this.status !== "open") return;
    this.setStatus("closing");
    this.send("terminal.close", {});
  }

  async copy(): Promise<void> {
    if (!this.terminal.hasSelection()) return;
    try {
      await navigator.clipboard.writeText(this.terminal.getSelection());
      this.terminal.clearSelection();
    } catch {
      this.events.notify("Clipboard write was denied. Your terminal selection is still available.");
    }
  }

  async paste(): Promise<void> {
    if (this.status !== "open") return;
    try {
      this.terminal.paste(await navigator.clipboard.readText());
    } catch {
      this.events.notify("Clipboard read was denied. Allow clipboard access and try again.");
    }
  }

  selectAll(): void { this.terminal.selectAll(); }
  clear(): void { this.terminal.clear(); this.terminal.focus(); }
  hasSelection(): boolean { return this.terminal.hasSelection(); }
  isOpen(): boolean { return this.status === "open"; }
  focus(): void { this.terminal.focus(); }

  search(term: string, direction: "next" | "previous" = "next", incremental = false): void {
    const options = { incremental, decorations: {
      matchOverviewRuler: "#64748b", activeMatchColorOverviewRuler: "#f59e0b",
      matchBackground: "#334155", activeMatchBackground: "#b45309",
    } };
    if (!term) { this.searchAddon.clearDecorations(); this.events.searchResults({ resultIndex: -1, resultCount: 0 }); return; }
    if (direction === "next") this.searchAddon.findNext(term, options);
    else this.searchAddon.findPrevious(term, options);
  }

  closeSearch(): void { this.searchAddon.clearDecorations(); this.terminal.focus(); }
  zoom(delta: number): void { this.setFontSize((this.terminal.options.fontSize ?? DEFAULT_FONT_SIZE) + delta); }
  resetZoom(): void { this.setFontSize(DEFAULT_FONT_SIZE); }

  dispose(): void {
    this.disposed = true;
    this.resizeObserver?.disconnect();
    this.socket?.close();
    this.socket = null;
    this.terminal.dispose();
  }

  private handleKey(event: KeyboardEvent): boolean {
    const action = terminalShortcut(event, this.terminal.hasSelection(), /Mac|iPhone|iPad/.test(navigator.platform));
    if (!action) return true;
    event.preventDefault();
    this.runShortcut(action);
    return false;
  }

  private runShortcut(action: TerminalShortcut): void {
    if (action === "copy") void this.copy();
    else if (action === "paste") void this.paste();
    else if (action === "search") this.events.search();
    else if (action === "zoom-in") this.zoom(1);
    else if (action === "zoom-out") this.zoom(-1);
    else this.resetZoom();
  }

  private setFontSize(size: number): void {
    const next = Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, size));
    this.terminal.options.fontSize = next;
    localStorage.setItem(FONT_STORAGE_KEY, String(next));
    this.events.fontSize(next);
    this.fit();
  }

  private fit(): void {
    if (this.disposed) return;
    try { this.fitAddon.fit(); } catch { /* xterm may not be measurable during layout teardown */ }
  }

  private send(type: string, payload: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type, payload }));
  }

  private onMessage(socket: WebSocket, raw: unknown): void {
    if (socket !== this.socket || typeof raw !== "string") return;
    const frame = parseFrame(raw);
    if (!frame) return;
    if (frame.type === "terminal.output" && typeof frame.payload.data === "string") this.terminal.write(frame.payload.data);
    else if (frame.type === "terminal.opened") { this.setStatus("open"); this.terminal.focus(); }
    else if (frame.type === "terminal.exited") { this.setStatus("closed"); this.terminal.writeln("\r\n[terminal exited]"); }
    else if (frame.type === "terminal.closed") { this.setStatus("closed"); socket.close(1000, "terminal closed"); }
    else if (frame.type === "terminal.error") {
      this.setStatus("error");
      const message = String(frame.payload.message ?? "Unknown terminal error");
      this.terminal.writeln(`\r\n[terminal error] ${message}`);
      this.events.notify(message);
    }
  }

  private setStatus(status: TerminalStatus): void { this.status = status; this.events.status(status); }
}

function osc52Clipboard(notify: (message: string) => void): IClipboardProvider {
  return {
    readText: () => "",
    writeText: async (selection, text) => {
      if (selection !== "c") return;
      try { await navigator.clipboard.writeText(text); }
      catch { notify("The shell requested clipboard access, but the browser denied it."); }
    },
  };
}

function savedFontSize(): number {
  const value = Number(localStorage.getItem(FONT_STORAGE_KEY));
  return Number.isFinite(value) && value >= MIN_FONT_SIZE && value <= MAX_FONT_SIZE ? value : DEFAULT_FONT_SIZE;
}

function parseFrame(raw: string): { type: string; payload: Record<string, unknown> } | null {
  try {
    const frame = JSON.parse(raw) as Record<string, unknown>;
    const payload = frame?.payload;
    return { type: typeof frame?.type === "string" ? frame.type : "", payload: payload && typeof payload === "object" && !Array.isArray(payload) ? payload as Record<string, unknown> : {} };
  } catch { return null; }
}

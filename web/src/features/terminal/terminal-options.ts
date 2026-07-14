import type { ITerminalOptions } from "@xterm/xterm";

export function terminalOptions(fontSize: number): ITerminalOptions {
  return {
    allowProposedApi: true,
    cursorBlink: true,
    scrollback: 10_000,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    fontSize,
    theme: { background: "#111827", foreground: "#e5e7eb", cursor: "#ffffff" },
  };
}

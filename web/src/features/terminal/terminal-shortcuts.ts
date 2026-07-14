export type TerminalShortcut = "copy" | "paste" | "search" | "zoom-in" | "zoom-out" | "zoom-reset";

export interface TerminalKey {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

export function terminalShortcut(event: TerminalKey, hasSelection: boolean, isMac: boolean): TerminalShortcut | null {
  const key = event.key.toLowerCase();
  const command = isMac ? event.metaKey : event.ctrlKey;
  if (!command) return null;

  if (key === "f") return "search";
  if (key === "+" || key === "=") return "zoom-in";
  if (key === "-") return "zoom-out";
  if (key === "0") return "zoom-reset";
  if (isMac && key === "c") return "copy";
  if (isMac && key === "v") return "paste";
  if (!isMac && key === "c" && (event.shiftKey || hasSelection)) return "copy";
  if (!isMac && key === "v" && event.shiftKey) return "paste";
  return null;
}

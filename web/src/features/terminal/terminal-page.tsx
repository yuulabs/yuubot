import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Copy, Minus, Plus, Plug, PlugZap, Search, SquareTerminal, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { DenseSection, Page } from "@/shared/components";

import { TerminalController, type TerminalStatus } from "./terminal-controller";

export function TerminalPage() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const controllerRef = useRef<TerminalController | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [status, setStatus] = useState<TerminalStatus>("idle");
  const [fontSize, setFontSize] = useState(13);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [results, setResults] = useState({ resultIndex: -1, resultCount: 0 });
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const controller = new TerminalController(containerRef.current, {
      status: setStatus,
      search: () => setSearchOpen(true),
      searchResults: setResults,
      fontSize: setFontSize,
      notify: (message) => toast.error(message),
    });
    controllerRef.current = controller;
    return () => { controller.dispose(); controllerRef.current = null; };
  }, []);

  useEffect(() => { if (searchOpen) requestAnimationFrame(() => searchRef.current?.focus()); }, [searchOpen]);

  function closeSearch() {
    setSearchOpen(false);
    controllerRef.current?.closeSearch();
  }

  function contextAction(action: "copy" | "paste" | "select" | "search" | "clear") {
    const controller = controllerRef.current;
    setMenu(null);
    if (!controller) return;
    if (action === "copy") void controller.copy();
    else if (action === "paste") void controller.paste();
    else if (action === "select") controller.selectAll();
    else if (action === "search") setSearchOpen(true);
    else controller.clear();
  }

  const controller = controllerRef.current;
  return (
    <Page title="Terminal" sub="Server-side admin PTY for native CLI checks and diagnostics.">
      <div className="dense-stack" onClick={() => setMenu(null)}>
        <DenseSection
          title="Server terminal"
          description="Connects to a fresh interactive shell from the home directory."
          actions={<>
            <Button onClick={() => controllerRef.current?.connect()} disabled={status === "connecting" || status === "open" || status === "closing"}><PlugZap size={14} />Connect</Button>
            <Button variant="outline" onClick={() => controllerRef.current?.disconnect()} disabled={status !== "open"}><Plug size={14} />Disconnect</Button>
          </>}
        >
          <div className="terminal-toolbar">
            <div className="terminal-status-row"><SquareTerminal size={16} /><span className={`dense-chip${status === "open" ? " dense-chip--ok" : status === "error" ? " dense-chip--danger" : " dense-chip--muted"}`}>{status}</span></div>
            <div className="terminal-toolbar__tools">
              <Button size="icon-xs" variant="ghost" aria-label="Search terminal" onClick={() => setSearchOpen(true)}><Search /></Button>
              <Button size="icon-xs" variant="ghost" aria-label="Decrease font size" onClick={() => controllerRef.current?.zoom(-1)}><Minus /></Button>
              <button className="terminal-font-size" onClick={() => controllerRef.current?.resetZoom()} title="Reset font size">{fontSize}px</button>
              <Button size="icon-xs" variant="ghost" aria-label="Increase font size" onClick={() => controllerRef.current?.zoom(1)}><Plus /></Button>
            </div>
          </div>
        </DenseSection>
        <div className="terminal-shell">
          {searchOpen && <div className="terminal-search" role="search">
            <Search size={14} />
            <input ref={searchRef} value={searchTerm} placeholder="Search terminal" aria-label="Search terminal output" onChange={(event) => { setSearchTerm(event.target.value); controllerRef.current?.search(event.target.value, "next", true); }} onKeyDown={(event) => { if (event.key === "Enter") controllerRef.current?.search(searchTerm, event.shiftKey ? "previous" : "next"); else if (event.key === "Escape") closeSearch(); }} />
            <span className="terminal-search__count">{results.resultCount ? `${results.resultIndex + 1}/${results.resultCount}` : "0/0"}</span>
            <Button size="icon-xs" variant="ghost" aria-label="Previous result" onClick={() => controllerRef.current?.search(searchTerm, "previous")}><ChevronUp /></Button>
            <Button size="icon-xs" variant="ghost" aria-label="Next result" onClick={() => controllerRef.current?.search(searchTerm, "next")}><ChevronDown /></Button>
            <Button size="icon-xs" variant="ghost" aria-label="Close search" onClick={closeSearch}><X /></Button>
          </div>}
          <div ref={containerRef} className="terminal-frame" onContextMenu={(event) => { event.preventDefault(); event.stopPropagation(); setMenu({ x: event.clientX, y: event.clientY }); }} />
          {menu && <div className="terminal-context-menu" style={{ left: menu.x, top: menu.y }} onClick={(event) => event.stopPropagation()} role="menu">
            <button disabled={!controller?.hasSelection()} onClick={() => contextAction("copy")}><Copy size={13} />Copy</button>
            <button disabled={!controller?.isOpen()} onClick={() => contextAction("paste")}>Paste</button>
            <button onClick={() => contextAction("select")}>Select all</button>
            <button onClick={() => contextAction("search")}>Search</button>
            <button onClick={() => contextAction("clear")}>Clear screen</button>
          </div>}
        </div>
      </div>
    </Page>
  );
}

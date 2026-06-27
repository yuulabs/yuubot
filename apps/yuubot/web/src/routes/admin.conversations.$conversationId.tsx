import { useEffect, useMemo, useRef, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Send, Loader2, Brain, Hammer, PanelLeft, X, UserRound, SquareTerminal, BookOpen, FileEdit, FilePen, Play, Square, Plus, Trash2, Paperclip } from "lucide-react";
import { useResourceList } from "@/hooks/use-resources";
import { sendConversationMessage, cancelConversationTurn, deleteConversation, getConversation, getConversationMessages, listConversations, uploadConversationFiles } from "@/lib/api";
import {
  appendRenderBlocks,
  historyItemsFromMessages,
  markToolBlocksCompleted,
  rememberConversationSseEvent,
  renderBlocksFromEvent,
  toolDisplay,
  type DisplayItem,
  type RenderBlock,
} from "@/lib/conversation-transcript";
import type {
  ActorResource,
  CapabilitySetResource,
  ConversationData,
  ConversationListItem,
  ConversationMessage,
  ConversationSSEEvent,
  ConversationUploadedFile,
} from "@/types/api";
import {
  extractBashCommand,
  parseEditArgs,
  renderSimpleDiff,
  stripAnsi,
  type DiffLine,
  type EditArgs,
} from "@/lib/tool-renderers";
import type { ClipboardEvent, ReactElement } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MarkdownRenderer } from "@/components/conversation/markdown-renderer.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CostBadge } from "@/components/conversation/cost-badge";
import { StatusPill } from "@/components/baseline";
import { workspaceHref } from "@/lib/workspace";

interface PendingAttachment {
  id: string;
  file: File;
}

function pythonHighlightedSegments(line: string): Array<{ text: string; kind: "plain" | "keyword" | "string" | "comment" | "number" }> {
  const commentIndex = line.indexOf("#");
  const code = commentIndex >= 0 ? line.slice(0, commentIndex) : line;
  const comment = commentIndex >= 0 ? line.slice(commentIndex) : "";
  const pattern = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b|\b\d+(?:\.\d+)?\b)/g;
  const segments: Array<{ text: string; kind: "plain" | "keyword" | "string" | "comment" | "number" }> = [];
  let cursor = 0;
  for (const match of code.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      segments.push({ text: code.slice(cursor, index), kind: "plain" });
    }
    const text = match[0];
    const kind = text.startsWith("\"") || text.startsWith("'")
      ? "string"
      : /^\d/.test(text)
        ? "number"
        : "keyword";
    segments.push({ text, kind });
    cursor = index + text.length;
  }
  if (cursor < code.length) {
    segments.push({ text: code.slice(cursor), kind: "plain" });
  }
  if (comment) {
    segments.push({ text: comment, kind: "comment" });
  }
  return segments;
}

function PythonCodeBlock({ code }: { code: string }) {
  const classForKind = {
    plain: "",
    keyword: "text-violet-400",
    string: "text-emerald-300",
    comment: "text-slate-500",
    number: "text-amber-300",
  };
  return (
    <pre className="max-h-96 overflow-auto rounded-md border border-slate-700 bg-slate-950 p-3 font-mono text-[12px] leading-5 text-slate-100 shadow-inner">
      <code>
        {code.split("\n").map((line, index) => (
          <span key={index} className="block min-h-5">
            {pythonHighlightedSegments(line).map((segment, segmentIndex) => (
              <span key={segmentIndex} className={classForKind[segment.kind]}>
                {segment.text}
              </span>
            ))}
          </span>
        ))}
      </code>
    </pre>
  );
}

/**
 * Per-tool renderers for `tool_group` blocks.
 *
 * A renderer returns the JSX to render, or `null` to fall back to the
 * inline generic side-by-side branch in `MessageBlockView`. Keeping the
 * generic branch inline here preserves its prior rendering byte-for-byte.
 */
type ToolRenderer = (block: RenderBlock) => ReactElement | null;

function BashRenderer(block: RenderBlock): ReactElement {
  const display = toolDisplay(block);
  const isRunning = !block.toolResult;
  const command = extractBashCommand(block.toolArgs ?? display.argsText);
  const result = isRunning ? null : stripAnsi(block.toolResult ?? "");
  return (
    <div className="rounded-md border border-border/70 bg-background/70 p-2 text-xs shadow-sm">
      <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
        <Hammer className="size-3.5" />
        <span>bash</span>
        {isRunning && <Loader2 className="ml-auto size-3.5 animate-spin" />}
        {block.toolStatus && (
          <Badge variant="secondary" className={isRunning ? "h-5 px-1.5 text-[10px]" : "ml-auto h-5 px-1.5 text-[10px]"}>
            {block.toolStatus}
          </Badge>
        )}
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        <div className="min-w-0 rounded-md border border-blue-200 bg-blue-50/80 p-2 dark:border-blue-900/70 dark:bg-blue-950/30">
          <div className="mb-1 flex items-center gap-1.5 font-semibold text-blue-700 dark:text-blue-300">
            <SquareTerminal className="size-3.5" />
            <span>command</span>
          </div>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
            {command}
          </pre>
        </div>
        <div className="min-w-0 rounded-md border border-emerald-200 bg-emerald-50/80 p-2 dark:border-emerald-900/70 dark:bg-emerald-950/30">
          <div className="mb-1 flex items-center gap-1.5 font-semibold text-emerald-700 dark:text-emerald-300">
            <SquareTerminal className="size-3.5" />
            <span>result</span>
          </div>
          {isRunning
            ? <PendingToolBanner toolName={display.name} />
            : (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
                {result}
              </pre>
            )
          }
        </div>
      </div>
    </div>
  );
}

function diffLineClass(kind: DiffLine["kind"]): string {
  if (kind === "add") return "text-emerald-300";
  if (kind === "del") return "text-rose-300";
  return "text-slate-300";
}

function diffLinePrefix(kind: DiffLine["kind"]): string {
  if (kind === "add") return "+";
  if (kind === "del") return "-";
  return " ";
}

function EditRenderer(block: RenderBlock): ReactElement | null {
  const args: EditArgs | null = parseEditArgs(block.toolArgs ?? "");
  if (args === null) {
    return null;
  }
  const diff = renderSimpleDiff(args.old_string, args.new_string);
  const isRunning = !block.toolResult;
  return (
    <div className="rounded-md border border-border/70 bg-background/70 p-2 text-xs shadow-sm">
      <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
        <Hammer className="size-3.5" />
        <span>edit</span>
        {isRunning && <Loader2 className="ml-auto size-3.5 animate-spin" />}
        {block.toolStatus && (
          <Badge variant="secondary" className={isRunning ? "h-5 px-1.5 text-[10px]" : "ml-auto h-5 px-1.5 text-[10px]"}>
            {block.toolStatus}
          </Badge>
        )}
      </div>
      <div
        className="mb-2 break-all font-mono text-[11px] text-muted-foreground"
        title={args.path}
      >
        {args.path}
      </div>
      {isRunning && <PendingToolBanner toolName="edit" />}
      <pre className="max-h-96 overflow-auto rounded-md border border-slate-700 bg-slate-950 p-3 font-mono text-[12px] leading-5 shadow-inner">
        <code>
          {diff.map((line, index) => (
            <span key={index} className={`block min-h-5 ${diffLineClass(line.kind)}`}>
              {diffLinePrefix(line.kind)}
              {line.text}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

const toolRendererRegistry: Record<string, ToolRenderer> = {
  bash: BashRenderer,
  edit: EditRenderer,
};

export const Route = createFileRoute("/admin/conversations/$conversationId")({
  component: AdminConversationPage,
});

/**
 * Append blocks to the active in-flight assistant item in a single display list.
 * The active item is the last item whose `turnKey` matches; if none exists yet,
 * a new actor item is appended. Items are never reordered, so the natural append
 * order is the render order: user1, agent1, user2, agent2, ...
 */
function appendLiveBlocks(
  items: DisplayItem[],
  itemKey: string,
  turnKey: string,
  timestamp: number,
  blocks: RenderBlock[],
): DisplayItem[] {
  const index = items.findIndex((item) => item.key === itemKey);
  if (index === -1) {
    return [
      ...items,
      {
        key: itemKey,
        role: "actor",
        blocks: appendRenderBlocks([], blocks),
        timestamp,
        turnKey,
      },
    ];
  }
  return items.map((item, itemIndex) => (
    itemIndex === index
      ? { ...item, blocks: appendRenderBlocks(item.blocks, blocks) }
      : item
  ));
}

function hasLiveBlocksForTurn(items: DisplayItem[], turnKey: string): boolean {
  return items.some((item) => item.turnKey === turnKey && item.blocks.length > 0);
}

function markLiveTurnCompleted(items: DisplayItem[], turnKey: string): DisplayItem[] {
  return items.map((item) => (
    item.turnKey === turnKey
      ? { ...item, blocks: markToolBlocksCompleted(item.blocks) }
      : item
  ));
}

function ThinkingBlock({ content, isStreaming }: { content: string; isStreaming: boolean }) {
  // Default-expanded with a fixed ceiling: shorter content renders in full;
  // when it exceeds max-h, the container scrolls so newer deltas land at the
  // bottom and older text scrolls out the top. Auto-stick to bottom while the
  // turn is still streaming; once done, let the user scroll freely.
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!isStreaming) return;
    const node = scrollRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [content, isStreaming]);
  return (
    <details open className="group rounded-md border border-border/60 bg-background/60 text-xs text-muted-foreground">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 font-medium text-muted-foreground [&::-webkit-details-marker]:hidden">
        <Brain className="size-3.5" />
        <span>thinking</span>
        {isStreaming && <Loader2 className="size-3 animate-spin text-muted-foreground/70" />}
        <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground/70 group-open:hidden">expand</span>
        <span className="ml-auto hidden text-[10px] uppercase tracking-wide text-muted-foreground/70 group-open:inline">collapse</span>
      </summary>
      <div
        ref={scrollRef}
        className="max-h-64 overflow-y-auto border-t border-border/50 px-3 py-2 whitespace-pre-wrap break-words"
      >
        {content}
      </div>
    </details>
  );
}

/**
 * Pending-status label shown in a tool block's result panel while the tool is
 * still running (no `toolResult` arrived yet). Maps tool kind to a verb so the
 * user sees "Reading..." / "Editing..." / "Running python..." rather than a
 * bare "running".
 *
 * `toolName` may be a registered yuubot tool (`bash`, `execute_python`, `read`,
 * `edit`, `write`) or an integration facade (`yext.<integration>.<capability>`).
 */
function pendingToolLabel(toolName: string | undefined): string {
  const name = toolName ?? "";
  if (name === "bash") return "Running bash...";
  if (name === "execute_python" || name.endsWith(".execute_python")) return "Running python...";
  if (name === "read") return "Reading...";
  if (name === "edit") return "Editing...";
  if (name === "write") return "Writing...";
  if (name.startsWith("yext.")) return `Running ${name}...`;
  return "Running...";
}

function pendingToolIcon(toolName: string | undefined): typeof BookOpen {
  const name = toolName ?? "";
  if (name === "read") return BookOpen;
  if (name === "edit") return FileEdit;
  if (name === "write") return FilePen;
  if (name === "bash" || name === "execute_python" || name.endsWith(".execute_python")) return Play;
  return Hammer;
}

function PendingToolBanner({ toolName }: { toolName: string | undefined }) {
  const Icon = pendingToolIcon(toolName);
  const label = pendingToolLabel(toolName);
  return (
    <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
      <Icon className="size-3.5" />
      <span>{label}</span>
      <Loader2 className="ml-auto size-3.5 animate-spin" />
    </div>
  );
}

function MessageBlockView({ block, isStreaming }: { block: RenderBlock; isStreaming: boolean }) {
  if (block.type === "thinking") {
    return <ThinkingBlock content={block.content} isStreaming={isStreaming} />;
  }
  if (block.type === "tool_group") {
    const display = toolDisplay(block);
    const isExecutePython = display.name === "execute_python" || display.name.endsWith(".execute_python");
    const isRunning = !block.toolResult;
    if (isExecutePython) {
      return (
        <div className="rounded-md border border-border/70 bg-background/70 p-3 text-xs shadow-sm">
          <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
            <Hammer className="size-3.5" />
            <span>execute_python</span>
            {isRunning && <Loader2 className="ml-auto size-3.5 animate-spin" />}
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            <div className="min-w-0">
              <PythonCodeBlock code={display.code ?? display.argsText} />
            </div>
            {isRunning ? (
              <div className="flex min-h-24 flex-col justify-center rounded-md border border-emerald-900/50 bg-zinc-950 text-emerald-200 shadow-inner">
                <PendingToolBanner toolName={display.name} />
              </div>
            ) : (
              <pre className="max-h-96 min-h-24 overflow-auto whitespace-pre-wrap break-words rounded-md border border-emerald-900/50 bg-zinc-950 p-3 font-mono text-[12px] leading-5 text-emerald-200 shadow-inner">
                {block.toolResult ?? "running"}
              </pre>
            )}
          </div>
        </div>
      );
    }
    const renderer = toolRendererRegistry[display.name];
    if (renderer) {
      const rendered = renderer(block);
      if (rendered !== null) {
        return rendered;
      }
    }
    return (
      <div className="rounded-md border border-border/70 bg-background/70 p-2 text-xs shadow-sm">
        <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
          <Hammer className="size-3.5" />
          <span>{display.name}</span>
          {isRunning && <Loader2 className="ml-auto size-3.5 animate-spin" />}
          {block.toolStatus && (
            <Badge variant="secondary" className={isRunning ? "h-5 px-1.5 text-[10px]" : "ml-auto h-5 px-1.5 text-[10px]"}>
              {block.toolStatus}
            </Badge>
          )}
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          <div className="min-w-0 rounded-md border border-blue-200 bg-blue-50/80 p-2 dark:border-blue-900/70 dark:bg-blue-950/30">
            <div className="mb-1 flex items-center gap-1.5 font-semibold text-blue-700 dark:text-blue-300">
              <SquareTerminal className="size-3.5" />
              <span>tool call</span>
            </div>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
              {display.argsText}
            </pre>
          </div>
          <div className="min-w-0 rounded-md border border-emerald-200 bg-emerald-50/80 p-2 dark:border-emerald-900/70 dark:bg-emerald-950/30">
            <div className="mb-1 flex items-center gap-1.5 font-semibold text-emerald-700 dark:text-emerald-300">
              <SquareTerminal className="size-3.5" />
              <span>tool result</span>
            </div>
            {isRunning
              ? <PendingToolBanner toolName={display.name} />
              : (
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
                  {block.toolResult ?? "pending"}
                </pre>
              )
            }
          </div>
        </div>
      </div>
    );
  }
  if (block.type === "tool_call") {
    return (
      <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs dark:border-blue-800 dark:bg-blue-950/30">
        <div className="mb-1 font-semibold text-blue-600 dark:text-blue-400">
          {block.content}
        </div>
        {block.toolArgs && (
          <pre className="whitespace-pre-wrap break-all text-muted-foreground">
            {block.toolArgs}
          </pre>
        )}
      </div>
    );
  }
  if (block.type === "tool_result") {
    return (
      <pre className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs whitespace-pre-wrap break-words dark:border-green-800 dark:bg-green-950/30">
        {block.content}
      </pre>
    );
  }
  if (block.type === "error") {
    return (
      <div className="rounded-md bg-destructive/10 px-3 py-2 text-destructive whitespace-pre-wrap break-words">
        {block.content}
      </div>
    );
  }
  if (block.type === "raw") {
    return (
      <pre className="rounded-md border border-border/60 bg-background/50 px-3 py-2 text-xs whitespace-pre-wrap break-words">
        {block.content}
      </pre>
    );
  }
  return <MarkdownRenderer content={block.content} />;
}

function AdminConversationPage() {
  const { conversationId } = Route.useParams();
  const navigate = useNavigate();
  // ISSUE-0010: an `actor-`-prefixed conversationId is a draft bound to the
  // Actor named by the suffix — the sole creation path now that the
  // top-level list/New Conversation creator is gone. The bound Actor is
  // preselected + LOCKED (rendered as a read-only Badge, never a Select).
  const actorDraftPrefix = "actor-";
  const isActorDraft = conversationId.startsWith(actorDraftPrefix);
  const isDraft = conversationId === "new" || isActorDraft;
  const draftActorId = isActorDraft ? conversationId.slice(actorDraftPrefix.length) : null;
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: capabilitySets = [] } = useResourceList<CapabilitySetResource>("capability-sets");
  const runningActors = actors.filter((a) => a.enabled);
  const [actorId, setActorId] = useState<string>(draftActorId ?? runningActors[0]?.id ?? "");
  const [displayItems, setDisplayItems] = useState<DisplayItem[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [error, setError] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [conversationMetadata, setConversationMetadata] = useState<ConversationData | null>(null);
  const [conversationItems, setConversationItems] = useState<ConversationListItem[]>([]);
  const [conversationListLoading, setConversationListLoading] = useState(true);
  const [actorLocked, setActorLocked] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [totalCost, setTotalCost] = useState(0);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const sendingRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const connectingSsePromiseRef = useRef<Promise<void> | null>(null);
  const activeTurnKeyRef = useRef("");
  const currentAssistantItemKeyRef = useRef("");
  const liveItemIndexRef = useRef(0);
  const liveBlockIndexRef = useRef(0);
  const seenSseEventKeysRef = useRef<Set<string>>(new Set());

  const actor = actors.find((a) => a.id === actorId);
  const actorById = useMemo(
    () => new Map(actors.map((item) => [item.id, item])),
    [actors],
  );

  const connectSse = (): Promise<void> => {
    if (connectingSsePromiseRef.current) {
      return connectingSsePromiseRef.current;
    }
    if (sseRef.current) {
      return Promise.resolve();
    }
    const pending = new Promise<void>((resolve, reject) => {
      const appendAssistantEvent = (data: ConversationSSEEvent) => {
        if (!rememberConversationSseEvent(seenSseEventKeysRef.current, data)) {
          return;
        }
        const turnId = "turn_id" in data ? data.turn_id : "";
        const turnKey = activeTurnKeyRef.current || `event-${turnId || data.sequence}`;
        activeTurnKeyRef.current = turnKey;
        const itemKey = currentAssistantItemKeyRef.current || `live:${turnKey}:assistant:${liveItemIndexRef.current++}`;
        currentAssistantItemKeyRef.current = itemKey;
        const blocks = renderBlocksFromEvent(
          data,
          itemKey,
          () => liveBlockIndexRef.current++,
        );
        if (blocks.length === 0) {
          return;
        }
        setDisplayItems((prev) => appendLiveBlocks(
          prev,
          itemKey,
          turnKey,
          data.timestamp,
          blocks,
        ));
      };

      const handleAssistantStreamEvent = (e: MessageEvent) => {
        const data = JSON.parse(e.data) as ConversationSSEEvent;
        appendAssistantEvent(data);
      };

      const markGenerationComplete = () => {
        const completedTurnKey = activeTurnKeyRef.current;
        if (completedTurnKey) {
          setDisplayItems((prev) => markLiveTurnCompleted(prev, completedTurnKey));
        }
        activeTurnKeyRef.current = "";
        currentAssistantItemKeyRef.current = "";
        sendingRef.current = false;
        setIsSending(false);
      };

      const handleTurnCompletedEvent = (e: MessageEvent) => {
        // Named turn-completion signal: the daemon emits this instead of
        // closing the stream. Leaves the EventSource open so the next send
        // already has a subscriber — fixes the "every second message hangs"
        // regression caused by the prior close-on-turn_completed design.
        try {
          const data = JSON.parse(e.data) as ConversationSSEEvent;
          if (!rememberConversationSseEvent(seenSseEventKeysRef.current, data)) {
            return;
          }
        } catch {
          /* malformed frame; still mark complete to avoid hanging */
        }
        markGenerationComplete();
      };

      const handleErrorEvent = (e: MessageEvent) => {
        markGenerationComplete();
        try {
          const raw = JSON.parse(e.data);
          if (raw && typeof raw === "object" && "error" in raw) {
            setError(String(raw.error));
          }
        } catch { /* transport error, see onerror below */ }
      };

      // Phase 5-2 "cost_update" SSE event: emitted once per `llm.finished`
      // RuntimeEvent with the running cumulative USD spend for this
      // conversation. No quota field — daily budget is global; only
      // the running `$<total> spent` figure is surfaced in the header.
      const handleCostUpdateEvent = (e: MessageEvent) => {
        try {
          const raw = JSON.parse(e.data) as {
            total_cost?: number;
            turn_cost?: number;
          };
          if (typeof raw.total_cost === "number" && Number.isFinite(raw.total_cost)) {
            setTotalCost(raw.total_cost);
          }
        } catch { /* malformed cost frame — ignore without breaking the stream */ }
      };

      const es = new EventSource(`/api/admin/conversations/${conversationId}/events`);
      sseRef.current = es;
      es.onopen = () => {
        connectingSsePromiseRef.current = null;
        resolve();
      };
      es.onerror = () => {
        if (connectingSsePromiseRef.current) {
          // Initial connect failed before onopen fired.
          connectingSsePromiseRef.current = null;
          reject(new Error("Conversation stream setup failed"));
          return;
        }
        if (sendingRef.current) {
          // Mid-turn transport drop: daemon may still be running the turn,
          // but we have no durable replay on the SSE side. Surface it.
          markGenerationComplete();
          setError("Conversation stream disconnected mid-turn");
        }
        // EventSource reconnects by default; do not close, do not reject.
        // The long-lived stream survives idle disconnects between turns.
      };

      es.addEventListener("transcript_delta", handleAssistantStreamEvent);
      es.addEventListener("turn_completed", handleTurnCompletedEvent);
      es.addEventListener("error", handleErrorEvent);
      es.addEventListener("cost_update", handleCostUpdateEvent);
    });
    connectingSsePromiseRef.current = pending;
    return pending;
  };

  const closeSse = (): void => {
    connectingSsePromiseRef.current = null;
    if (sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
    }
  };

  // Load persisted metadata and history. A 404 metadata response is a draft route.
  useEffect(() => {
    let cancelled = false;
    activeTurnKeyRef.current = "";
    currentAssistantItemKeyRef.current = "";
    liveItemIndexRef.current = 0;
    liveBlockIndexRef.current = 0;
    seenSseEventKeysRef.current = new Set();
    sendingRef.current = false;
    closeSse();
    setDisplayItems([]);
    setConversationMetadata(null);
    setActorLocked(isActorDraft);
    setTotalCost(0);
    setLoadingHistory(true);

    if (isDraft) {
      // Draft route: no backend row exists yet. Render empty UI and wait for
      // the first send, which mints the real conversation id and navigates.
      setLoadingHistory(false);
      return;
    }

    void (async () => {
      try {
        const metadata = await getConversation(conversationId);
        if (cancelled) {
          return;
        }
        if (metadata === null) {
          setLoadingHistory(false);
          return;
        }

        setConversationMetadata(metadata);
        setActorId(metadata.actor_id);
        const persistedMessages = await getConversationMessages(conversationId);
        if (cancelled) {
          return;
        }
        setDisplayItems(historyItemsFromMessages(persistedMessages));
        setActorLocked(persistedMessages.length > 0);
        setLoadingHistory(false);
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load conversation");
          setLoadingHistory(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      closeSse();
    };
  }, [conversationId, isDraft]);

  // Existing persisted conversations may listen for runtime events, but opening
  // a draft route must not create the backend row or agent.
  useEffect(() => {
    if (conversationMetadata !== null) {
      void connectSse().catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Conversation stream setup failed");
      });
    }
  }, [conversationMetadata]);

  useEffect(() => {
    if (!actorId && runningActors[0]) setActorId(runningActors[0].id);
  }, [actorId, runningActors]);

  useEffect(() => {
    let cancelled = false;
    setConversationListLoading(true);
    void (async () => {
      try {
        const rows = await listConversations();
        if (cancelled) return;
        setConversationItems(
          rows
            .filter((item) => item.actor_id === actorId)
            .sort((left, right) => conversationTime(right) - conversationTime(left)),
        );
      } catch {
        if (!cancelled) setConversationItems([]);
      } finally {
        if (!cancelled) setConversationListLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId, conversationMetadata, actorId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [displayItems]);

  const handleStop = async () => {
    // Stop = POST /cancel. The backend's `cancel_turn` sets the cancel event
    // (single-point safety trip) + cancels the task + AWAITS the task. The
    // HTTP response is the "stop receipt" — it returns only after the loop's
    // CancelledError handler has completed (flush + cancel tools + synthesize
    // `[cancelled]` results) and `agent.turn_completed` has emitted via the
    // loop's normal exit path (the existing `turn_completed` SSE handler flips
    // `isSending` off). We only need to clear `isStopping` so the button
    // reverts to whatever `isSending` says.
    if (!isSending || isStopping) return;
    setIsStopping(true);
    try {
      await cancelConversationTurn(conversationId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to stop the turn");
    } finally {
      setIsStopping(false);
    }
  };

  const handleDeleteConversation = async (targetConversationId: string) => {
    await deleteConversation(targetConversationId);
    setConversationItems((current) => (
      current.filter((item) => item.conversation_id !== targetConversationId)
    ));
    if (targetConversationId === conversationId) {
      closeSse();
      setHistoryOpen(false);
      navigate({
        to: "/admin/conversations/$conversationId",
        params: { conversationId: actorId ? `actor-${actorId}` : "new" },
        replace: true,
      });
    }
  };

  const addFiles = (files: Iterable<File>) => {
    const next = Array.from(files).filter((file) => file.size >= 0);
    if (next.length === 0) return;
    setAttachments((current) => [
      ...current,
      ...next.map((file) => ({ id: `attachment-${crypto.randomUUID()}`, file })),
    ]);
  };

  const removeAttachment = (attachmentId: string) => {
    setAttachments((current) => current.filter((item) => item.id !== attachmentId));
  };

  const handlePaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const files: File[] = [];
    for (const item of Array.from(event.clipboardData.items)) {
      if (!item.type.startsWith("image/")) continue;
      const file = item.getAsFile();
      if (file === null) continue;
      const extension = item.type.split("/")[1] || "png";
      files.push(new File([file], `pasted-image-${Date.now()}.${extension}`, { type: item.type }));
    }
    if (files.length > 0) {
      addFiles(files);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    // Input is never disabled during generation: the Send button is always
    // actionable when visible (during generation it is replaced by the Stop
    // button); the input field is never disabled. Only the empty-input /
    // no-actor guards remain.
    if ((!text && attachments.length === 0) || !actorId) return;

    const isDraftSend = isDraft;
    const targetConversationId = isDraft
      ? `conversation-${crypto.randomUUID()}`
      : conversationId;
    const filesToUpload = attachments.map((item) => item.file);
    const optimisticText = [
      text,
      ...filesToUpload.map((file) => pendingAttachmentLine(file.name)),
    ].filter(Boolean).join("\n\n");

    const userMsgId = `user-${crypto.randomUUID()}`;
    const turnKey = `turn-${userMsgId}`;
    const userMsg: ConversationMessage = {
      id: 0,
      conversation_id: targetConversationId,
      message_id: userMsgId,
      role: "user",
      raw_content: JSON.stringify([{ type: "text", text: optimisticText }]),
      metadata: {},
      timestamp: Math.floor(Date.now() / 1000),
    };
    const userItemKey = `message:${userMsgId}`;
    setDisplayItems((prev) => [
      ...prev,
      ...historyItemsFromMessages([userMsg]),
    ]);
    setInput("");
    setAttachments([]);
    setDragActive(false);
    setError("");
    setIsSending(true);
    sendingRef.current = true;
    activeTurnKeyRef.current = turnKey;
    currentAssistantItemKeyRef.current = "";
    const hadMetadata = conversationMetadata !== null;
    void (async () => {
      try {
        const uploaded: ConversationUploadedFile[] = filesToUpload.length
          ? await uploadConversationFiles({
              conversationId: targetConversationId,
              files: filesToUpload,
              actorId: isDraftSend ? actorId : undefined,
            })
          : [];
        if (uploaded.length > 0) {
          const uploadedText = [
            text,
            ...uploaded.map((file) => uploadedAttachmentLine(file)),
          ].filter(Boolean).join("\n\n");
          const uploadedUserMsg: ConversationMessage = {
            ...userMsg,
            raw_content: JSON.stringify([{ type: "text", text: uploadedText }]),
          };
          setDisplayItems((prev) => [
            ...prev.filter((item) => item.key !== userItemKey),
            ...historyItemsFromMessages([uploadedUserMsg]),
          ]);
        }
        await sendConversationMessage({
          conversationId: targetConversationId,
          text,
          messageId: userMsgId,
          actorId: isDraftSend ? actorId : undefined,
          uploads: uploaded,
        });
        if (isDraftSend) {
          // Quietly switch to the real conversation URL. The remount triggers
          // getConversation + getConversationMessages + connectSse on the
          // freshly-created row; the daemon's first send already appended the
          // user message, started the turn, and is streaming assistant output.
          navigate({
            to: "/admin/conversations/$conversationId",
            params: { conversationId: targetConversationId },
            replace: true,
          });
          return;
        }
        setActorLocked(true);
      } catch (err: unknown) {
        if (activeTurnKeyRef.current === turnKey) {
          activeTurnKeyRef.current = "";
          currentAssistantItemKeyRef.current = "";
          sendingRef.current = false;
          setIsSending(false);
        }
        setDisplayItems((prev) => prev.filter((item) => item.key !== userItemKey));
        setAttachments(filesToUpload.map((file) => ({ id: `attachment-${crypto.randomUUID()}`, file })));
        setError(err instanceof Error ? err.message : "Send failed");
        if (!hadMetadata && !isDraftSend) {
          try {
            const metadata = await getConversation(conversationId);
            if (metadata !== null) {
              setConversationMetadata(metadata);
              setActorId(metadata.actor_id);
              const persistedMessages = await getConversationMessages(conversationId);
              setDisplayItems(historyItemsFromMessages(persistedMessages));
              setActorLocked(persistedMessages.length > 0);
            }
          } catch { /* keep the original send error visible */ }
        }
      }
    })();
  };

  const currentTurnHasLiveBlocks = activeTurnKeyRef.current
    ? hasLiveBlocksForTurn(displayItems, activeTurnKeyRef.current)
    : false;

  // Workspace source: actor.capability_set.workspace_path when embedded,
  // falling back to the capability-sets list when the actor response is flat.
  const actorCapabilitySet = actor?.capability_set ??
    capabilitySets.find((item) => item.id === actor?.capability_set_id);
  const workspacePath = actorCapabilitySet?.workspace_path;
  const workspaceUrl = workspaceHref(workspacePath);

  return (
    <div className="view view--conversations">
      <div className="chat chat--focus">
        <div className="chat__main">
          <header className="chat__topbar chat__topbar--focus">
            <div className="chat__topbar-left">
              <Link to="/actors" aria-label="Back to Actors" className="chat__back">
                <ArrowLeft />
              </Link>
              <span className="chat__topbar-crumb">Actors</span>
              <span className="chev">›</span>
              <span className="chat__topbar-here">
                {actor ? actor.name : isDraft ? "新对话" : conversationId}
              </span>
              {actor && (
                <StatusPill variant={actor.enabled ? "running" : "paused"}>
                  {actor.enabled ? "运行中" : "已停止"}
                </StatusPill>
              )}
            </div>
            <div className="chat__topbar-right">
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setHistoryOpen(true)}>
                <PanelLeft size={14} />
                <span>历史</span>
              </button>
              {workspaceUrl ? (
                <a
                  href={workspaceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn--ghost btn--sm"
                >
                  <SquareTerminal size={14} />
                  <span>Workspace</span>
                </a>
              ) : (
                <button type="button" className="btn btn--ghost btn--sm" disabled>
                  <SquareTerminal size={14} />
                  <span>Workspace</span>
                </button>
              )}
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setProfileOpen(true)}>
                <UserRound size={14} />
                <span>Profile</span>
              </button>
              {!isDraft && <CostBadge totalCost={totalCost} />}
            </div>
          </header>

          <div className="chat__scroll">
            {loadingHistory && <p className="chat__empty-mini">正在读取对话...</p>}
            {!loadingHistory && displayItems.length === 0 && (
              <div className="chat__empty">
                <div className="chat__empty-inner">
                  <div className="chat__empty-icon"><Send /></div>
                  <div className="chat__empty-title">{actor ? `向 ${actor.name} 发送第一条消息` : "请选择 Actor"}</div>
                  <div className="chat__empty-sub">专注对话模式已打开。历史、Workspace 与 Profile 都在右上角。</div>
                </div>
              </div>
            )}
            {displayItems.map((item) => {
              const itemIsStreaming = isSending
                && !!item.turnKey
                && item.turnKey === activeTurnKeyRef.current;
              return (
              <div key={item.key} className={`msg ${item.role === "user" ? "msg--user" : "msg--assistant"}`}>
                <div className="msg__avatar">{item.role === "user" ? "U" : (actor?.name.trim()[0] ?? "A").toUpperCase()}</div>
                <div className="msg__body">
                  <div className="msg__meta">{item.role === "user" ? "You" : actor?.name ?? "Actor"}</div>
                  <div className="msg__bubble">
                  {item.blocks.map((block) => (
                    <MessageBlockView key={block.key} block={block} isStreaming={itemIsStreaming} />
                  ))}
                  </div>
                </div>
              </div>
              );
            })}
            {isSending && !currentTurnHasLiveBlocks && (
              <div className="msg msg--assistant">
                <div className="msg__avatar">{(actor?.name.trim()[0] ?? "A").toUpperCase()}</div>
                <div className="msg__body">
                  <div className="msg__bubble">
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                      <Loader2 className="size-3 animate-spin" /> 等待回复...
                    </span>
                  </div>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {error && (
            <div className="chat__error">{error}</div>
          )}

          <form
            onSubmit={(e) => { e.preventDefault(); void handleSend(); }}
            className={`chat__composer ${dragActive ? "is-dragging" : ""}`}
            onDragEnter={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragOver={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={(event) => {
              if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
              setDragActive(false);
            }}
            onDrop={(event) => {
              event.preventDefault();
              setDragActive(false);
              addFiles(event.dataTransfer.files);
            }}
          >
            {attachments.length > 0 && (
              <div className="composer__attachments" aria-label="Pending uploads">
                {attachments.map((attachment) => (
                  <div key={attachment.id} className="composer__attachment">
                    <Paperclip size={14} />
                    <span className="composer__attachment-name">{attachment.file.name || "upload"}</span>
                    <span className="composer__attachment-size">{formatFileSize(attachment.file.size)}</span>
                    <button
                      type="button"
                      className="composer__attachment-remove"
                      onClick={() => removeAttachment(attachment.id)}
                      aria-label={`Remove ${attachment.file.name || "upload"}`}
                      disabled={isSending}
                    >
                      <X size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="composer">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="composer__file-input"
                onChange={(event) => {
                  addFiles(event.currentTarget.files ?? []);
                  event.currentTarget.value = "";
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => fileInputRef.current?.click()}
                aria-label="Attach files"
                title="Attach files"
                disabled={isSending}
              >
                <Paperclip className="size-4" />
              </Button>
              <Input value={input} onChange={(e) => setInput(e.target.value)}
                onPaste={handlePaste}
                placeholder={actor ? `给 ${actor.name} 发消息...` : "请选择 Actor..."}
                className="composer__input"
              />
              {isStopping ? (
                <Button
                  type="button"
                  variant="destructive"
                  size="icon"
                  disabled
                  aria-label="Stopping the turn"
                >
                  <Loader2 className="size-4 animate-spin" />
                </Button>
              ) : isSending ? (
                <Button
                  type="button"
                  variant="destructive"
                  size="icon"
                  onClick={() => { void handleStop(); }}
                  aria-label="Stop the running turn"
                  title="Stop"
                >
                  <Square className="size-4" />
                </Button>
              ) : (
                <Button
                  type="submit"
                  className="composer__send"
                  disabled={(!input.trim() && attachments.length === 0) || !actorId}
                  aria-label="Send message"
                >
                  <Send className="size-4" />
                </Button>
              )}
            </div>
          </form>
        </div>
      </div>

      {historyOpen && (
        <div className="chat-drawer" role="dialog" aria-label="历史对话">
          <button type="button" className="chat-drawer__backdrop" onClick={() => setHistoryOpen(false)} aria-label="关闭历史对话" />
          <div className="chat-drawer__panel chat-drawer__panel--left">
            <ConversationRail
              conversations={conversationItems}
              actorsById={actorById}
              activeConversationId={conversationId}
              actorId={actor?.id ?? actorId}
              loading={conversationListLoading}
              deletingDisabled={isSending || isStopping}
              onDeleteConversation={handleDeleteConversation}
              onClose={() => setHistoryOpen(false)}
            />
          </div>
        </div>
      )}

      {profileOpen && (
        <div className="chat-drawer" role="dialog" aria-label="Actor Profile">
          <button type="button" className="chat-drawer__backdrop" onClick={() => setProfileOpen(false)} aria-label="关闭 Profile" />
          <div className="chat-drawer__panel chat-drawer__panel--right">
            <BindingPanel
              actors={runningActors}
              actor={actor}
              workspaceUrl={workspaceUrl}
              actorLocked={actorLocked}
              isSending={isSending}
              onActorChange={setActorId}
              onClose={() => setProfileOpen(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function ConversationRail({
  conversations,
  actorsById,
  activeConversationId,
  actorId,
  loading,
  deletingDisabled,
  onDeleteConversation,
  onClose,
}: {
  conversations: ConversationListItem[];
  actorsById: Map<string, ActorResource>;
  activeConversationId: string;
  actorId: string;
  loading: boolean;
  deletingDisabled: boolean;
  onDeleteConversation: (conversationId: string) => Promise<void>;
  onClose: () => void;
}) {
  const newConversationId = actorId ? `actor-${actorId}` : "new";
  return (
    <aside className="chat__rail">
      <div className="chat__rail-head">
        <span>历史对话</span>
        <div className="chat__rail-actions">
          <Link
            to="/admin/conversations/$conversationId"
            params={{ conversationId: newConversationId }}
            className="chat__back"
            aria-label="新对话"
            onClick={onClose}
          >
            <Plus />
          </Link>
          <button type="button" className="chat__back" onClick={onClose} aria-label="关闭历史对话">
            <X />
          </button>
        </div>
      </div>
      <div className="chat__list">
        {loading ? (
          <div className="chat__empty-mini">加载中…</div>
        ) : conversations.length === 0 ? (
          <div className="chat__empty-mini">暂无历史对话</div>
        ) : (
          conversations.map((item) => {
            const actor = actorsById.get(item.actor_id);
            const shortId = shortConversationId(item.conversation_id);
            const displayTitle = conversationDisplayTitle(item);
            return (
              <div
                key={item.conversation_id}
                className={`conv-item ${item.conversation_id === activeConversationId ? "is-active" : ""}`}
                title={item.conversation_id}
              >
                <Link
                  to="/admin/conversations/$conversationId"
                  params={{ conversationId: item.conversation_id }}
                  className="conv-item__link"
                  onClick={onClose}
                >
                  <span className="conv-item__top">
                    <span className="conv-item__name">{displayTitle}</span>
                    <span className="conv-item__time">{shortConversationTime(item.updated_at ?? item.created_at)}</span>
                  </span>
                  <span className="conv-item__preview">{actor?.name ?? item.actor_id} · ID {shortId}</span>
                </Link>
                <button
                  type="button"
                  className="conv-item__delete"
                  disabled={deletingDisabled}
                  aria-label={`删除 ${displayTitle}`}
                  title="删除"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    void onDeleteConversation(item.conversation_id);
                  }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}

function BindingPanel({
  actors,
  actor,
  workspaceUrl,
  actorLocked,
  isSending,
  onActorChange,
  onClose,
}: {
  actors: ActorResource[];
  actor: ActorResource | undefined;
  workspaceUrl: string | null;
  actorLocked: boolean;
  isSending: boolean;
  onActorChange: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <aside className="chat-bind">
      <div className="chat-bind__head">
        <span>Profile</span>
        <button type="button" className="chat__back" onClick={onClose} aria-label="关闭 Profile">
          <X />
        </button>
      </div>
      <div className="chat-bind__body">
        <section className="rail-card">
          <h3 className="rail-card__title">Actor</h3>
          {actorLocked ? (
            // ISSUE-0010: actor-bound draft (and existing conversations with
            // messages) render the Actor as read-only; the editable actor
            // select affordance is gone entirely.
            <div className="chat-bind__line">
              {actor ? (
                <>
                  <span>{actor.name}</span>
                  <StatusPill variant={actor.enabled ? "running" : "paused"}>
                    {actor.enabled ? "运行中" : "已停止"}
                  </StatusPill>
                </>
              ) : (
                <StatusPill variant="paused">已锁定</StatusPill>
              )}
            </div>
          ) : (
            <Select value={actor?.id ?? ""} onValueChange={onActorChange} disabled={isSending}>
              <SelectTrigger className="h-8 w-full text-xs"><SelectValue placeholder="Select actor" /></SelectTrigger>
              <SelectContent>
                {actors.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {actor && !actorLocked && (
            <StatusPill variant={actor.enabled ? "running" : "paused"}>
              {actor.enabled ? "运行中" : "已停止"}
            </StatusPill>
          )}
          {/* Uses the user-configured CapabilitySet.workspace_path —
              a relative path under <data_dir>/workspace. If empty,
              no workspace was configured for this actor's CapabilitySet. */}
          {workspaceUrl ? (
            <a
              href={workspaceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
            >
              Open Workspace
            </a>
          ) : (
            <span
              className="pointer-events-none text-xs text-muted-foreground opacity-50"
              aria-disabled="true"
            >
              Open Workspace
            </span>
          )}
        </section>
        {/* TODO(TODO-B): Character / CapabilitySet / LLM Backend */}
        {/* TODO(TODO-C): Runtime params (model/temp/reasoning) */}
        {/* TODO(TODO-D): Model Context inspector */}
        {/* TODO(avatar): Avatar / Actor profile */}
      </div>
    </aside>
  );
}

function conversationTime(conversation: ConversationListItem): number {
  const value = conversation.updated_at ?? conversation.created_at;
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function shortConversationTime(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function conversationDisplayTitle(item: ConversationListItem): string {
  return item.title.trim() || `Conversation ${shortConversationId(item.conversation_id)}`;
}

function pendingAttachmentLine(name: string): string {
  return `[User uploaded a file ${name}, upload pending]`;
}

function uploadedAttachmentLine(file: ConversationUploadedFile): string {
  return `[User uploaded a file ${file.name}, stored at ${file.url}]`;
}

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function shortConversationId(value: string): string {
  return value.replace(/^conversation-/, "").slice(0, 8);
}

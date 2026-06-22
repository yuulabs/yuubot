import { useEffect, useRef, useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Send, Loader2, Brain, Hammer, SquareTerminal } from "lucide-react";
import { useResourceList } from "@/hooks/use-resources";
import { sendConversationMessage, getConversation, getConversationMessages } from "@/lib/api";
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
import type { ActorResource, ConversationData, ConversationMessage, ConversationSSEEvent } from "@/types/api";
import {
  extractBashCommand,
  parseEditArgs,
  renderSimpleDiff,
  stripAnsi,
  type DiffLine,
  type EditArgs,
} from "@/lib/tool-renderers";
import type { ReactElement } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
  const result = stripAnsi(block.toolResult ?? "running");
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
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
            {result}
          </pre>
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

function MessageBlockView({ block }: { block: RenderBlock }) {
  if (block.type === "thinking") {
    return (
      <details className="group rounded-md border border-border/60 bg-background/60 text-xs text-muted-foreground">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 font-medium text-muted-foreground [&::-webkit-details-marker]:hidden">
          <Brain className="size-3.5" />
          <span>thinking</span>
          <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground/70 group-open:hidden">expand</span>
          <span className="ml-auto hidden text-[10px] uppercase tracking-wide text-muted-foreground/70 group-open:inline">collapse</span>
        </summary>
        <div className="border-t border-border/50 px-3 py-2 whitespace-pre-wrap break-words">
          {block.content}
        </div>
      </details>
    );
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
            <pre className="max-h-96 min-h-24 overflow-auto whitespace-pre-wrap break-words rounded-md border border-emerald-900/50 bg-zinc-950 p-3 font-mono text-[12px] leading-5 text-emerald-200 shadow-inner">
              {block.toolResult ?? "running"}
            </pre>
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
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
              {block.toolResult ?? "pending"}
            </pre>
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
  return <div className="whitespace-pre-wrap break-words">{block.content}</div>;
}

function AdminConversationPage() {
  const { conversationId } = Route.useParams();
  const navigate = useNavigate();
  const isDraft = conversationId === "new";
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const runningActors = actors.filter((a) => a.enabled);
  const [actorId, setActorId] = useState<string>(runningActors[0]?.id ?? "");
  const [displayItems, setDisplayItems] = useState<DisplayItem[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [conversationMetadata, setConversationMetadata] = useState<ConversationData | null>(null);
  const [actorLocked, setActorLocked] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const sendingRef = useRef(false);
  const connectingSsePromiseRef = useRef<Promise<void> | null>(null);
  const activeTurnKeyRef = useRef("");
  const currentAssistantItemKeyRef = useRef("");
  const liveItemIndexRef = useRef(0);
  const liveBlockIndexRef = useRef(0);
  const intentionalCloseRef = useRef(false);
  const seenSseEventKeysRef = useRef<Set<string>>(new Set());

  const actor = actors.find((a) => a.id === actorId);

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
        intentionalCloseRef.current = true;
        activeTurnKeyRef.current = "";
        currentAssistantItemKeyRef.current = "";
        sendingRef.current = false;
        setIsSending(false);
      };

      const handleErrorEvent = (e: MessageEvent) => {
        markGenerationComplete();
        try {
          const raw = JSON.parse(e.data);
          if (raw && typeof raw === "object" && "error" in raw) {
            setError(String(raw.error));
          }
        } catch { /* connection error, auto-reconnect */ }
      };

      const es = new EventSource(`/api/admin/conversations/${conversationId}/events`);
      sseRef.current = es;
      es.onopen = () => {
        connectingSsePromiseRef.current = null;
        intentionalCloseRef.current = false;
        resolve();
      };
      es.onerror = () => {
        connectingSsePromiseRef.current = null;
        if (sendingRef.current) {
          markGenerationComplete();
          es.close();
          if (sseRef.current === es) {
            sseRef.current = null;
          }
          resolve();
          return;
        }
        if (sseRef.current === es) {
          sseRef.current = null;
        }
        es.close();
        if (!intentionalCloseRef.current) {
          reject(new Error("Conversation stream setup failed"));
        }
      };

      es.addEventListener("transcript_delta", handleAssistantStreamEvent);
      es.addEventListener("error", handleErrorEvent);
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
    intentionalCloseRef.current = false;
    closeSse();
    setDisplayItems([]);
    setConversationMetadata(null);
    setActorLocked(false);
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
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [displayItems]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || !actorId || sendingRef.current) return;

    const isDraftSend = isDraft;
    const targetConversationId = isDraft
      ? `conversation-${crypto.randomUUID()}`
      : conversationId;

    const userMsgId = `user-${crypto.randomUUID()}`;
    const turnKey = `turn-${userMsgId}`;
    const userMsg: ConversationMessage = {
      id: 0,
      conversation_id: targetConversationId,
      message_id: userMsgId,
      role: "user",
      raw_content: JSON.stringify([{ type: "text", text }]),
      metadata: {},
      timestamp: Math.floor(Date.now() / 1000),
    };
    const userItemKey = `message:${userMsgId}`;
    setDisplayItems((prev) => [
      ...prev,
      ...historyItemsFromMessages([userMsg]),
    ]);
    setInput("");
    setError("");
    setIsSending(true);
    sendingRef.current = true;
    activeTurnKeyRef.current = turnKey;
    currentAssistantItemKeyRef.current = "";
    const hadMetadata = conversationMetadata !== null;
    void (async () => {
      try {
        await sendConversationMessage({
          conversationId: targetConversationId,
          text,
          messageId: userMsgId,
          actorId: isDraftSend ? actorId : undefined,
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

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-3">
        <a href="/admin/conversations" onClick={(e) => { e.preventDefault(); window.history.back(); }}>
          <Button variant="ghost" size="icon"><ArrowLeft className="size-4" /></Button>
        </a>
        <div className="flex-1"><h2 className="text-sm font-semibold">{isDraft ? "New conversation" : conversationId}</h2></div>
        <div className="flex items-center gap-2">
          <Select value={actorId} onValueChange={setActorId} disabled={actorLocked || isSending}>
            <SelectTrigger className="h-8 w-44 text-xs"><SelectValue placeholder="Select actor" /></SelectTrigger>
            <SelectContent>
              {runningActors.map((a) => (
                <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {actor && (
            <Badge variant={actor.enabled ? "default" : "secondary"} className="text-xs">
              {actor.enabled ? "running" : "stopped"}
            </Badge>
          )}
        </div>
      </header>

      <div className="flex-1 space-y-4 overflow-auto p-4">
        {loadingHistory && <p className="text-xs text-muted-foreground text-center">Loading history...</p>}
        {!loadingHistory && displayItems.length === 0 && (
          <p className="text-xs text-muted-foreground text-center">No messages yet. Select an actor and start the conversation!</p>
        )}
        {displayItems.map((item) => (
          <div key={item.key} className={`flex ${item.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[80%] space-y-2 rounded-lg px-4 py-2 text-sm ${item.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
              {item.blocks.map((block) => (
                <MessageBlockView key={block.key} block={block} />
              ))}
            </div>
          </div>
        ))}
        {/* Pending indicator */}
        {isSending && !currentTurnHasLiveBlocks && (
          <div className="flex justify-start">
            <div className="flex items-center gap-2 rounded-lg bg-muted px-4 py-2 text-sm text-muted-foreground">
              <Loader2 className="size-3 animate-spin" /> Waiting for response…
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && (
        <div className="mx-4 mb-3 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>
      )}

      <form onSubmit={(e) => { e.preventDefault(); void handleSend(); }} className="flex items-center gap-2 border-t p-4">
        <Input value={input} onChange={(e) => setInput(e.target.value)}
          placeholder={actor ? `Message ${actor.name}...` : "Select an actor..."}
          className="flex-1" />
        <Button
          type="submit"
          size="icon"
          disabled={!input.trim() || !actorId || isSending}
        >
          {isSending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </Button>
      </form>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { ArrowLeft, Send, Loader2 } from "lucide-react";
import { useResourceList } from "@/hooks/use-resources";
import { sendConversationMessage, createConversation, ensureConversationAgent, getConversationMessages } from "@/lib/api";
import type { ActorResource, ConversationMessage, ConversationSSEEvent } from "@/types/api";
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

const ensuredConversations = new Map<string, Promise<unknown>>();

function ensureConversationOnce(actorId: string, conversationId: string): Promise<unknown> {
  const key = `${actorId}:${conversationId}`;
  const existing = ensuredConversations.get(key);
  if (existing) {
    return existing;
  }

  const pending = createConversation({ actorId, conversationId })
    .then(() => ensureConversationAgent({ conversationId }))
    .catch((error) => {
      ensuredConversations.delete(key);
      throw error;
    });
  ensuredConversations.set(key, pending);
  return pending;
}

// ---------------------------------------------------------------------------
// Block text extraction
// ---------------------------------------------------------------------------
// SSE blocks from the backend have one of these shapes:
//   { content: "plain text" }
//   { content: { type: "text",      text: "hello" } }
//   { content: { type: "thinking",  thinking: "..." } }
//   { content: { type: "tool_call", name: "...", arguments: {...} } }
//   "raw string" (rare)
//
// History raw_content items are flat:
//   { type: "text",     text: "hello" }
//   { type: "thinking", thinking: "..." }
//   ...

type ConversationBlockType = "thinking" | "text" | "tool_call" | "tool_result" | "error" | "raw";

interface RenderBlock {
  key: string;
  type: ConversationBlockType;
  content: string;
  toolArgs?: string;
}

interface DisplayItem {
  key: string;
  role: "user" | "actor";
  blocks: RenderBlock[];
  timestamp: number;
}

/** Extract human-readable text from a raw block dict without dropping unknown content. */
function extractBlockText(block: unknown): string {
  if (block === null || block === undefined) {
    return "";
  }
  if (typeof block !== "object") {
    return String(block);
  }
  const b = block as Record<string, unknown>;

  // Try the wrapped `.content` field first (SSE streaming format)
  const content = b.content;
  if (typeof content === "string") return content;
  if (content && typeof content === "object") {
    const c = content as Record<string, unknown>;
    if (c.type === "text" && typeof c.text === "string") return c.text;
    if (c.type === "thinking" && typeof c.thinking === "string") return c.thinking;
    return JSON.stringify(c);
  }

  // Flat block format (history raw_content)
  if (b.type === "text" && typeof b.text === "string") return b.text;
  if (b.type === "thinking" && typeof b.thinking === "string") return b.thinking;

  return JSON.stringify(b);
}

/** Stream-safe extraction: map blocks to strings and join. */
function blocksToText(blocks: Array<unknown> | undefined): string {
  return (blocks ?? []).map(extractBlockText).join("");
}

function renderBlockFromRaw(
  block: unknown,
  key: string,
  fallbackType: ConversationBlockType,
): RenderBlock {
  const source = rawBlockSource(block);
  const rawType = typeof source.type === "string" ? source.type : fallbackType;
  const type = blockType(rawType, fallbackType);
  return {
    key,
    type,
    content: extractBlockText(source),
    toolArgs: type === "tool_call" ? JSON.stringify(source, null, 2) : undefined,
  };
}

function rawBlockSource(block: unknown): Record<string, unknown> {
  if (!block || typeof block !== "object") {
    return { type: "text", text: typeof block === "string" ? block : String(block) };
  }
  const raw = block as Record<string, unknown>;
  if (typeof raw.content === "string") {
    return { type: "text", text: raw.content };
  }
  if (raw.content && typeof raw.content === "object") {
    return raw.content as Record<string, unknown>;
  }
  return raw;
}

function blockType(
  rawType: string,
  fallbackType: ConversationBlockType,
): ConversationBlockType {
  if (rawType.includes("thinking")) return "thinking";
  if (rawType === "text") return "text";
  if (rawType === "tool_call") return "tool_call";
  if (rawType === "tool_result") return "tool_result";
  if (rawType === "error") return "error";
  return fallbackType;
}


export const Route = createFileRoute("/admin/conversations/$conversationId")({
  component: AdminConversationPage,
});

/** Streaming message block rendered from SSE events. */
interface LiveBlock extends RenderBlock {
  key: string;
  type: Exclude<ConversationBlockType, "raw">;
  turnKey: string;
  agentId: string;
  sequence: number;
  timestamp: number;
  toolArgs?: string;
}

function liveBlockKey(
  data: ConversationSSEEvent,
  type: LiveBlock["type"],
  turnKey: string,
): string {
  const entityId = data.content.entity_id;
  const toolCallId = data.content.tool_call_id;
  return [
    turnKey || `event-${data.agent_id}-${Math.floor(data.timestamp * 1000)}`,
    type,
    typeof toolCallId === "string" ? toolCallId : "",
    typeof entityId === "string" ? entityId : data.agent_id,
  ].join(":");
}

function liveBlockSequence(
  data: ConversationSSEEvent,
  block: unknown,
  index: number,
): number {
  const raw = block && typeof block === "object"
    ? block as Record<string, unknown>
    : {};
  const chunkIndex = typeof data.content.chunk_index === "number"
    ? data.content.chunk_index
    : 0;
  const blockId = typeof raw.block_id === "number" ? raw.block_id : index;
  return chunkIndex * 100_000 + blockId;
}

function eventFallbackType(data: ConversationSSEEvent): Exclude<ConversationBlockType, "raw"> {
  if (data.event_type === "thinking") return "thinking";
  if (data.event_type === "tool_call") return "tool_call";
  if (data.event_type === "tool_result") return "tool_result";
  if (data.event_type === "error") return "error";
  return "text";
}

function liveBlocksFromEvent(
  data: ConversationSSEEvent,
  turnKey: string,
): LiveBlock[] {
  const fallbackType = eventFallbackType(data);
  const blocks = data.content.blocks as Array<unknown> | undefined;
  return (blocks ?? []).flatMap<LiveBlock>((block, index) => {
    const source = rawBlockSource(block);
    const rawType = typeof source.type === "string" ? source.type : fallbackType;
    const type = blockType(rawType, fallbackType);
    if (type === "raw") {
      return [];
    }
    if (type === "tool_call") {
      return [{
        key: liveBlockKey(data, type, turnKey),
        type,
        turnKey,
        agentId: data.agent_id,
        sequence: liveBlockSequence(data, block, index),
        content: `Tool: ${source.name ?? "tool"}`,
        timestamp: data.timestamp,
        toolArgs: JSON.stringify(source, null, 2),
      }];
    }
    const content = extractBlockText(source);
    if (!content) {
      return [];
    }
    return [{
      key: liveBlockKey(data, type, turnKey),
      type,
      turnKey,
      agentId: data.agent_id,
      sequence: liveBlockSequence(data, block, index),
      content,
      timestamp: data.timestamp,
    }];
  });
}

function mergeLiveBlock(block: LiveBlock) {
  return (prev: LiveBlock[]): LiveBlock[] => {
    const index = prev.findIndex((item) => item.key === block.key);
    if (index === -1) {
      return [...prev, block];
    }

    const next = [...prev];
    next[index] = {
      ...next[index],
      ...block,
      sequence: Math.min(next[index].sequence, block.sequence),
      timestamp: Math.min(next[index].timestamp, block.timestamp),
      content: `${next[index].content}${block.content}`,
    };
    return next;
  };
}

function liveBlocksForDisplay(blocks: LiveBlock[]): DisplayItem[] {
  const groups = new Map<string, LiveBlock[]>();
  for (const block of blocks) {
    groups.set(block.turnKey, [...(groups.get(block.turnKey) ?? []), block]);
  }
  return Array.from(groups.entries()).map(([turnKey, group]) => {
    const sorted = [...group].sort((left, right) => left.sequence - right.sequence);
    return {
      key: `live:${turnKey}`,
      role: "actor",
      blocks: sorted,
      timestamp: Math.min(...sorted.map((block) => block.timestamp)),
    };
  });
}

function finalEventText(data: ConversationSSEEvent): string | null {
  const role = data.content.role;
  const content = data.content.content;
  if (
    role !== "assistant" &&
    role !== "system" &&
    role !== "tool" &&
    role !== "user"
  ) {
    return null;
  }
  if (!Array.isArray(content)) {
    return null;
  }
  return blocksToText(content);
}

function finalMessageFromEvent(data: ConversationSSEEvent): ConversationMessage | null {
  const role = data.content.role;
  const content = data.content.content;
  if (
    role !== "assistant" &&
    role !== "system" &&
    role !== "tool" &&
    role !== "user"
  ) {
    return null;
  }
  if (!Array.isArray(content)) {
    return null;
  }
  return {
    id: 0,
    conversation_id: data.conversation_id,
    message_id: `event-${data.agent_id}-${Math.floor(data.timestamp * 1000)}`,
    role,
    raw_content: JSON.stringify(content),
    metadata: {},
    timestamp: Math.floor(data.timestamp),
  };
}

function liveTurnText(blocks: LiveBlock[], turnKey: string): string {
  return blocks
    .filter((block) => block.turnKey === turnKey)
    .sort((left, right) => left.sequence - right.sequence)
    .map((block) => block.content)
    .join("");
}

function finalEventMatchesLiveText(finalText: string, liveText: string): boolean {
  const expected = finalText.trim();
  const actual = liveText.trim();
  if (!expected || !actual) {
    return true;
  }
  return expected.includes(actual) || actual.includes(expected);
}

function MessageBlockView({ block }: { block: RenderBlock }) {
  if (block.type === "thinking") {
    return (
      <div className="rounded-md border border-border/60 bg-background/50 px-3 py-2 text-xs italic text-muted-foreground">
        <div className="mb-1 text-[11px] font-semibold not-italic">thinking</div>
        <div className="whitespace-pre-wrap break-words">{block.content}</div>
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
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const runningActors = actors.filter((a) => a.enabled);
  const [actorId, setActorId] = useState<string>(runningActors[0]?.id ?? "");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [streamReady, setStreamReady] = useState(false);
  const [sseBlocks, setSseBlocks] = useState<LiveBlock[]>([]);
  const sseBlocksRef = useRef<LiveBlock[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const sendingRef = useRef(false);
  const activeTurnKeyRef = useRef("");

  const actor = actors.find((a) => a.id === actorId);

  // Load history on mount
  useEffect(() => {
    activeTurnKeyRef.current = "";
    sseBlocksRef.current = [];
    setSseBlocks([]);
    setLoadingHistory(true);
    getConversationMessages(conversationId)
      .then(setMessages)
      .catch(() => {})
      .finally(() => setLoadingHistory(false));
  }, [conversationId]);

  // Connect SSE stream
  useEffect(() => {
    if (!actorId) return;

    let cancelled = false;
    setStreamReady(false);

    if (sseRef.current) sseRef.current.close();

    const mergeStreamEvent = (data: ConversationSSEEvent) => {
      const turnKey = activeTurnKeyRef.current || `event-${data.agent_id}-${Math.floor(data.timestamp * 1000)}`;
      const liveBlocks = liveBlocksFromEvent(data, turnKey);
      if (liveBlocks.length === 0) {
        return;
      }
      if (activeTurnKeyRef.current === turnKey) {
        setIsSending(false);
      }
      setSseBlocks((prev) => {
        const next = liveBlocks.reduce(
          (current, block) => mergeLiveBlock(block)(current),
          prev,
        );
        sseBlocksRef.current = next;
        return next;
      });
    };

    const handleStreamEvent = (e: MessageEvent) => {
      const data = JSON.parse(e.data) as ConversationSSEEvent;
      mergeStreamEvent(data);
    };

    const handleFinalEvent = (e: MessageEvent) => {
      const data = JSON.parse(e.data) as ConversationSSEEvent;
      const turnKey = activeTurnKeyRef.current;
      const finalText = finalEventText(data);
      if (turnKey && finalText !== null) {
        const liveText = liveTurnText(sseBlocksRef.current, turnKey);
        if (!finalEventMatchesLiveText(finalText, liveText)) {
          console.warn("conversation final message did not match streamed chunks", {
            conversationId,
            agentId: data.agent_id,
            liveText,
            finalText,
          });
        }
      }
      if (turnKey) {
        setSseBlocks((prev) => {
          const next = prev.filter((block) => block.turnKey !== turnKey);
          sseBlocksRef.current = next;
          return next;
        });
      }
      activeTurnKeyRef.current = "";
      sendingRef.current = false;
      setIsSending(false);
      const finalMessage = finalMessageFromEvent(data);
      if (finalMessage) {
        setMessages((prev) => (
          prev.some((message) => message.message_id === finalMessage.message_id)
            ? prev
            : [...prev, finalMessage]
        ));
      }
    };

    const handleErrorEvent = (e: MessageEvent) => {
      setStreamReady(false);
      try {
        const raw = JSON.parse(e.data);
        if (raw && typeof raw === "object" && "error" in raw) {
          setError(String(raw.error));
        }
      } catch { /* connection error, auto-reconnect */ }
    };

    void ensureConversationOnce(actorId, conversationId)
      .then(() => {
        if (cancelled) {
          return;
        }

        const es = new EventSource(`/api/admin/conversations/${conversationId}/events`);
        sseRef.current = es;
        es.onopen = () => {
          if (!cancelled) {
            setStreamReady(true);
          }
        };

        es.addEventListener("thinking", handleStreamEvent);
        es.addEventListener("text", handleStreamEvent);
        es.addEventListener("output", handleStreamEvent);
        es.addEventListener("tool_call", handleStreamEvent);
        es.addEventListener("tool_result", handleStreamEvent);
        es.addEventListener("message", handleFinalEvent);
        es.addEventListener("error", handleErrorEvent);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Conversation stream setup failed");
        }
      });

    return () => {
      cancelled = true;
      setStreamReady(false);
      if (sseRef.current) {
        sseRef.current.close();
        sseRef.current = null;
      }
    };
  }, [actorId, conversationId]);

  useEffect(() => {
    if (!actorId && runningActors[0]) setActorId(runningActors[0].id);
  }, [actorId, runningActors]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sseBlocks]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || !actorId || !streamReady || sendingRef.current) return;

    const userMsgId = `user-${crypto.randomUUID()}`;
    const turnKey = `turn-${userMsgId}`;
    const userMsg: ConversationMessage = {
      id: 0,
      conversation_id: conversationId,
      message_id: userMsgId,
      role: "user",
      raw_content: JSON.stringify([{ type: "text", text }]),
      metadata: {},
      timestamp: Math.floor(Date.now() / 1000),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setError("");
    setIsSending(true);
    sendingRef.current = true;
    activeTurnKeyRef.current = turnKey;
    void ensureConversationOnce(actorId, conversationId)
      .then(() => sendConversationMessage({ conversationId, text }))
      .then(() => {
        if (activeTurnKeyRef.current === turnKey) {
          activeTurnKeyRef.current = "";
          sendingRef.current = false;
          setIsSending(false);
        }
      })
      .catch((err: unknown) => {
        if (activeTurnKeyRef.current === turnKey) {
          activeTurnKeyRef.current = "";
          sendingRef.current = false;
          setIsSending(false);
        }
        setError(err instanceof Error ? err.message : "Send failed");
      });
  };

  const messageBlocks = (message: ConversationMessage): RenderBlock[] => {
    try {
      const parsed = JSON.parse(message.raw_content) as unknown;
      if (Array.isArray(parsed)) {
        return parsed.map((item, index) =>
          renderBlockFromRaw(item, `${message.message_id}:${index}`, "text")
        );
      }
      return [
        renderBlockFromRaw(
          parsed,
          `${message.message_id}:raw`,
          "raw",
        ),
      ];
    } catch {
      return [
        {
          key: `${message.message_id}:raw-content`,
          type: "raw",
          content: message.raw_content,
        },
      ];
    }
  };

  const historyItems: DisplayItem[] = messages.map((message) => ({
    key: `message:${message.message_id}`,
    role: message.role === "user" ? "user" : "actor",
    blocks: messageBlocks(message),
    timestamp: message.timestamp,
  }));
  const liveItems = liveBlocksForDisplay(sseBlocks);
  const displayItems = [...historyItems, ...liveItems].sort(
    (left, right) => left.timestamp - right.timestamp,
  );
  const currentTurnHasLiveBlocks = activeTurnKeyRef.current
    ? sseBlocks.some((block) => block.key.startsWith(`${activeTurnKeyRef.current}:`))
    : false;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-3">
        <a href="/admin/conversations" onClick={(e) => { e.preventDefault(); window.history.back(); }}>
          <Button variant="ghost" size="icon"><ArrowLeft className="size-4" /></Button>
        </a>
        <div className="flex-1"><h2 className="text-sm font-semibold">{conversationId}</h2></div>
        <div className="flex items-center gap-2">
          <Select value={actorId} onValueChange={setActorId}>
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
        <Button type="submit" size="icon" disabled={!input.trim() || !actorId || !streamReady || isSending}>
          {isSending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </Button>
      </form>
    </div>
  );
}

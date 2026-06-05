import { useEffect, useRef, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { ArrowLeft, Send, Loader2 } from "lucide-react";
import { useResourceList } from "@/hooks/use-resources";
import { sendConversationMessage, createConversation, getConversationMessages } from "@/lib/api";
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

/** Extract human-readable text from a raw block dict (any shape). */
function extractBlockText(block: unknown): string {
  if (!block || typeof block !== "object") {
    return typeof block === "string" ? block : "";
  }
  const b = block as Record<string, unknown>;

  // Try the wrapped `.content` field first (SSE streaming format)
  const content = b.content;
  if (typeof content === "string") return content;
  if (content && typeof content === "object") {
    const c = content as Record<string, unknown>;
    if (c.type === "text" && typeof c.text === "string") return c.text;
    if (c.type === "thinking" && typeof c.thinking === "string") return c.thinking;
    if (c.type === "tool_call") return "";
    return JSON.stringify(c);
  }

  // Flat block format (history raw_content)
  if (b.type === "text" && typeof b.text === "string") return b.text;
  if (b.type === "thinking" && typeof b.thinking === "string") return "";

  return "";
}

/** Stream-safe extraction: map blocks to strings and join. */
function blocksToText(blocks: Array<unknown> | undefined): string {
  return (blocks ?? []).map(extractBlockText).join("");
}


export const Route = createFileRoute("/chat/$dialogId")({
  component: ChatDialogPage,
});

/** Streaming message block rendered from SSE events. */
interface LiveBlock {
  type: "thinking" | "text" | "tool_call" | "tool_result" | "error";
  content: string;
  toolName?: string;
  toolArgs?: string;
}

function ChatDialogPage() {
  const { dialogId } = Route.useParams();
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const runningActors = actors.filter((a) => a.enabled);
  const [actorId, setActorId] = useState<string>(runningActors[0]?.id ?? "");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [sseBlocks, setSseBlocks] = useState<LiveBlock[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);

  const actor = actors.find((a) => a.id === actorId);

  // Load history on mount
  useEffect(() => {
    getConversationMessages(dialogId)
      .then(setMessages)
      .catch(() => {})
      .finally(() => setLoadingHistory(false));
  }, [dialogId]);

  // Connect SSE stream
  useEffect(() => {
    if (!actorId) return;

    // Ensure conversation exists
    createConversation({ actorId, conversationId: dialogId }).catch(() => {});

    if (sseRef.current) sseRef.current.close();

    const es = new EventSource(`/api/conversations/${dialogId}/events`);
    sseRef.current = es;

    const appendBlock = (block: LiveBlock) => {
      setSseBlocks((prev) => [...prev, block]);
    };

    es.addEventListener("thinking", (e) => {
      const data = JSON.parse(e.data) as ConversationSSEEvent;
      const text = blocksToText(data.content.blocks as Array<unknown>);
      if (text) appendBlock({ type: "thinking", content: text });
    });

    es.addEventListener("text", (e) => {
      const data = JSON.parse(e.data) as ConversationSSEEvent;
      const text = blocksToText(data.content.blocks as Array<unknown>);
      if (text) appendBlock({ type: "text", content: text });
    });

    es.addEventListener("tool_call", (e) => {
      const data = JSON.parse(e.data) as ConversationSSEEvent;
      const blocks = data.content.blocks as Array<{ content?: Record<string, unknown> }> | undefined;
      for (const b of blocks ?? []) {
        const c = b?.content as Record<string, unknown> | undefined;
        if (c?.type === "tool_call") {
          appendBlock({
            type: "tool_call",
            content: `🔧 ${c.name ?? "tool"}`,
            toolName: c.name as string,
            toolArgs: JSON.stringify(c.arguments ?? {}),
          });
        }
      }
    });

    es.addEventListener("tool_result", (e) => {
      const data = JSON.parse(e.data) as ConversationSSEEvent;
      const text = blocksToText(data.content.blocks as Array<unknown>);
      const preview = text.length > 500 ? text.slice(0, 500) + "…" : text;
      if (preview) {
        appendBlock({
          type: "tool_result",
          content: preview,
          toolName: data.content.entity_type ?? "",
        });
      }
    });

    es.addEventListener("message", () => {
      // Reload history when the LLM turn completes
      getConversationMessages(dialogId)
        .then((data) => { setMessages(data); setSseBlocks([]); })
        .catch(() => { setSseBlocks([]); });
    });

    es.addEventListener("error", (e) => {
      try {
        const raw = JSON.parse((e as MessageEvent).data);
        if (raw && typeof raw === "object" && "error" in raw) {
          setError(String(raw.error));
        }
      } catch { /* connection error, auto-reconnect */ }
    });

    return () => {
      es.close();
      sseRef.current = null;
    };
  }, [actorId, dialogId]);

  useEffect(() => {
    if (!actorId && runningActors[0]) setActorId(runningActors[0].id);
  }, [actorId, runningActors]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sseBlocks]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || !actorId) return;

    const userMsgId = `user-${crypto.randomUUID()}`;
    const userMsg: ConversationMessage = {
      id: 0,
      conversation_id: dialogId,
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
    try {
      await sendConversationMessage({ conversationId: dialogId, text });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Send failed");
    } finally {
      setIsSending(false);
    }
  };

  // Build display from history + SSE blocks
  const renderMessageContent = (m: ConversationMessage): string => {
    try {
      const items = JSON.parse(m.raw_content) as unknown[];
      if (Array.isArray(items)) {
        return items.map(extractBlockText).join(" ");
      }
      return "";
    } catch {
      return "";
    }
  };

  const historyItems = messages.map((m) => ({
    role: m.role === "user" ? "user" : "actor",
    content: renderMessageContent(m),
  }));

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-3">
        <a href="/chat" onClick={(e) => { e.preventDefault(); window.history.back(); }}>
          <Button variant="ghost" size="icon"><ArrowLeft className="size-4" /></Button>
        </a>
        <div className="flex-1"><h2 className="text-sm font-semibold">{dialogId}</h2></div>
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
        {!loadingHistory && historyItems.length === 0 && sseBlocks.length === 0 && (
          <p className="text-xs text-muted-foreground text-center">No messages yet. Select an actor and start the conversation!</p>
        )}
        {historyItems.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[80%] rounded-lg px-4 py-2 text-sm ${msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"}`}>
              <div className="whitespace-pre-wrap break-words">{msg.content}</div>
            </div>
          </div>
        ))}
        {/* Live SSE blocks */}
        {sseBlocks.map((block, i) => (
          <div key={`sse-${i}`} className="flex justify-start">
            <div className={`max-w-[80%] rounded-lg px-4 py-2 text-sm ${
              block.type === "thinking" ? "bg-muted/50 italic text-muted-foreground" :
              block.type === "tool_call" ? "bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800" :
              block.type === "tool_result" ? "bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 text-xs font-mono" :
              block.type === "error" ? "bg-destructive/10 text-destructive" :
              "bg-muted"
            }`}>
              {block.type === "thinking" && <span className="text-[11px] font-semibold text-muted-foreground mr-1">💭</span>}
              {block.type === "tool_call" && <div className="text-[11px] font-semibold text-blue-600 dark:text-blue-400 mb-0.5">{block.content}</div>}
              {block.type === "tool_call" && block.toolArgs && (
                <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-all">{block.toolArgs}</pre>
              )}
              {block.type !== "tool_call" && <div className="whitespace-pre-wrap break-words">{block.content}</div>}
            </div>
          </div>
        ))}
        {/* Pending indicator */}
        {isSending && sseBlocks.length === 0 && (
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
          placeholder={actor ? `Message ${actor.name}...` : "Select an actor to chat..."}
          className="flex-1" />
        <Button type="submit" size="icon" disabled={!input.trim() || !actorId || isSending}>
          {isSending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </Button>
      </form>
    </div>
  );
}

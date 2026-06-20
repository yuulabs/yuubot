import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { ArrowLeft, Send, Loader2, Brain, Hammer, SquareTerminal } from "lucide-react";
import { useResourceList } from "@/hooks/use-resources";
import { sendConversationMessage, createConversation, ensureConversationAgent, getConversation, getConversationMessages } from "@/lib/api";
import type { ActorResource, ConversationData, ConversationMessage, ConversationSSEEvent } from "@/types/api";
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

type ConversationBlockType = "thinking" | "text" | "tool_call" | "tool_result" | "tool_group" | "error" | "raw";

interface RenderBlock {
  key: string;
  type: ConversationBlockType;
  content: string;
  toolArgs?: string;
  toolCallId?: string;
  toolName?: string;
  toolResult?: string;
  toolStatus?: string;
}

interface ToolDisplay {
  name: string;
  argsText: string;
  code?: string;
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
  if (b.type === "tool_call") {
    const name = typeof b.name === "string" ? b.name : "tool";
    return `Tool: ${name}`;
  }
  if (b.type === "tool_result") {
    if (typeof b.content === "string") return b.content;
    return JSON.stringify(b);
  }

  return JSON.stringify(b);
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
    toolCallId: toolCallId(source, type),
    toolName: toolName(source),
    toolStatus: toolStatus(source),
  };
}

function rawBlockSource(block: unknown): Record<string, unknown> {
  if (!block || typeof block !== "object") {
    return { type: "text", text: typeof block === "string" ? block : String(block) };
  }
  const raw = block as Record<string, unknown>;
  // EntityLog wraps LLM items as {type: "content", content: {...}}.
  // The inner content item is the semantic block the transcript should render.
  if ((raw.type === "content" || typeof raw.type !== "string") && raw.content && typeof raw.content === "object") {
    return raw.content as Record<string, unknown>;
  }
  // Flat format: block already has a recognized type — return as-is
  // (history raw_content items, plus the new blocks emitted by Change 1)
  if (typeof raw.type === "string") {
    return raw;
  }
  // Simple wrapped: {content: "plain text"}
  if (typeof raw.content === "string") {
    return { type: "text", text: raw.content };
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

function toolCallId(source: Record<string, unknown>, type: ConversationBlockType): string | undefined {
  if (type === "tool_call" && typeof source.id === "string") {
    return source.id;
  }
  if (typeof source.tool_call_id === "string") {
    return source.tool_call_id;
  }
  return undefined;
}

function toolName(source: Record<string, unknown>): string | undefined {
  if (typeof source.name === "string") {
    return source.name;
  }
  if (typeof source.tool_name === "string") {
    return source.tool_name;
  }
  return undefined;
}

function toolStatus(source: Record<string, unknown>): string | undefined {
  return typeof source.status === "string" ? source.status : undefined;
}

function parseJsonMaybe(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function toolDisplay(block: RenderBlock): ToolDisplay {
  const name = block.toolName ?? (block.content.replace(/^Tool:\s*/, "") || "tool");
  const raw = block.toolArgs ? parseJsonMaybe(block.toolArgs) : undefined;
  const source = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const rawArgs = source.arguments ?? source.args ?? source.input;
  const args = typeof rawArgs === "string" ? parseJsonMaybe(rawArgs) : rawArgs;
  const argsText = args === undefined
    ? "{}"
    : typeof args === "string"
      ? args
      : JSON.stringify(args, null, 2);
  const code = args && typeof args === "object" && typeof (args as Record<string, unknown>).code === "string"
    ? String((args as Record<string, unknown>).code)
    : undefined;
  return { name, argsText, code };
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


export const Route = createFileRoute("/admin/conversations/$conversationId")({
  component: AdminConversationPage,
});

/** Streaming transcript item rendered from SSE events. */
interface LiveItem extends DisplayItem {
  turnKey: string;
}

function syncLiveItemsRef(
  ref: MutableRefObject<LiveItem[]>,
  update: (prev: LiveItem[]) => LiveItem[],
): (prev: LiveItem[]) => LiveItem[] {
  return (prev) => {
    const next = update(prev);
    ref.current = next;
    return next;
  };
}

function renderBlocksFromEvent(
  data: ConversationSSEEvent,
  keyPrefix: string,
  nextBlockIndex: () => number,
): RenderBlock[] {
  const fallbackType = eventFallbackType(data);
  const blocks = data.content.blocks as Array<unknown> | undefined;
  return (blocks ?? []).flatMap<RenderBlock>((block) => {
    const source = rawBlockSource(block);
    const rawType = typeof source.type === "string" ? source.type : fallbackType;
    const type = blockType(rawType, fallbackType);
    const key = `${keyPrefix}:block:${nextBlockIndex()}`;
    if (type === "tool_call") {
      const callId = toolCallId(source, type);
      const name = toolName(source) ?? "tool";
      return [{
        key,
        type,
        content: `Tool: ${name}`,
        toolArgs: JSON.stringify(source, null, 2),
        toolCallId: callId,
        toolName: name,
        toolStatus: toolStatus(source),
      }];
    }
    const content = extractBlockText(source);
    if (!content) {
      return [];
    }
    return [{
      key,
      type,
      content,
      toolCallId: toolCallId(source, type),
      toolName: toolName(source),
      toolStatus: toolStatus(source),
    }];
  });
}

function eventFallbackType(data: ConversationSSEEvent): Exclude<ConversationBlockType, "raw"> {
  if (data.event_type === "thinking") return "thinking";
  if (data.event_type === "tool_call") return "tool_call";
  if (data.event_type === "tool_result") return "tool_result";
  if (data.event_type === "error") return "error";
  return "text";
}

function liveItemKey(turnKey: string, kind: string, index: number): string {
  return `live:${turnKey}:${kind}:${index}`;
}

function eventHasToolCall(data: ConversationSSEEvent): boolean {
  const content = data.content.content;
  if (!Array.isArray(content)) {
    return false;
  }
  return content.some((item) => {
    const source = rawBlockSource(item);
    return blockType(typeof source.type === "string" ? source.type : "", "raw") === "tool_call";
  });
}

function shouldMergeAdjacentBlocks(left: RenderBlock, right: RenderBlock): boolean {
  return (
    left.type === right.type &&
    left.type !== "tool_call" &&
    left.type !== "tool_result" &&
    left.type !== "tool_group" &&
    left.type !== "raw" &&
    !left.toolArgs &&
    !right.toolArgs
  );
}

function makeToolGroup(call: RenderBlock, result?: RenderBlock): RenderBlock {
  const name = call.toolName ?? (call.content.replace(/^Tool:\s*/, "") || "tool");
  return {
    key: `${call.key}:group`,
    type: "tool_group",
    content: name,
    toolArgs: call.toolArgs ?? call.content,
    toolCallId: call.toolCallId,
    toolName: name,
    toolResult: result?.content,
    toolStatus: result?.toolStatus,
  };
}

function appendToolResultToGroup(group: RenderBlock, result: RenderBlock): RenderBlock {
  return {
    ...group,
    toolResult: group.toolResult ? `${group.toolResult}${result.content}` : result.content,
    toolStatus: result.toolStatus ?? group.toolStatus,
  };
}

function mergeToolArgs(existing?: string, incoming?: string): string | undefined {
  if (!existing) return incoming;
  if (!incoming) return existing;

  const left = parseJsonMaybe(existing);
  const right = parseJsonMaybe(incoming);
  if (
    left &&
    right &&
    typeof left === "object" &&
    typeof right === "object" &&
    !Array.isArray(left) &&
    !Array.isArray(right)
  ) {
    const leftRecord = left as Record<string, unknown>;
    const rightRecord = right as Record<string, unknown>;
    const merged: Record<string, unknown> = { ...leftRecord, ...rightRecord };
    for (const key of ["arguments", "args", "input"]) {
      const leftValue = leftRecord[key];
      const rightValue = rightRecord[key];
      if (typeof leftValue === "string" && typeof rightValue === "string") {
        merged[key] = rightValue.startsWith(leftValue) ? rightValue : `${leftValue}${rightValue}`;
      } else if (rightValue === undefined && leftValue !== undefined) {
        merged[key] = leftValue;
      }
    }
    return JSON.stringify(merged, null, 2);
  }

  return incoming.startsWith(existing) ? incoming : `${existing}${incoming}`;
}

function samePendingToolCall(left: RenderBlock, right: RenderBlock): boolean {
  if (left.type === "tool_group" && left.toolResult) {
    return false;
  }
  if (left.toolCallId && right.toolCallId) {
    return left.toolCallId === right.toolCallId;
  }
  return Boolean(left.toolName && right.toolName && left.toolName === right.toolName);
}

function mergeToolCallBlocks(left: RenderBlock, right: RenderBlock): RenderBlock {
  const name = right.toolName ?? left.toolName ?? right.content.replace(/^Tool:\s*/, "") ?? left.content.replace(/^Tool:\s*/, "") ?? "tool";
  return {
    ...left,
    content: left.type === "tool_group" ? name : `Tool: ${name}`,
    toolArgs: mergeToolArgs(left.toolArgs, right.toolArgs),
    toolCallId: left.toolCallId ?? right.toolCallId,
    toolName: name,
    toolStatus: right.toolStatus ?? left.toolStatus,
  };
}

function appendToolCallToGroup(group: RenderBlock, call: RenderBlock): RenderBlock {
  const merged = mergeToolCallBlocks(group, call);
  return {
    ...merged,
    type: "tool_group",
    content: merged.toolName ?? group.content,
    toolResult: group.toolResult,
  };
}

function findMatchingToolCallChunkIndex(blocks: RenderBlock[], call: RenderBlock): number {
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    if (block.type !== "tool_call" && block.type !== "tool_group") {
      continue;
    }
    if (samePendingToolCall(block, call)) {
      return index;
    }
  }
  return -1;
}

function findMatchingToolCallIndex(blocks: RenderBlock[], result: RenderBlock): number {
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    if (block.type !== "tool_call" && block.type !== "tool_group") {
      continue;
    }
    if (result.toolCallId && block.toolCallId && result.toolCallId !== block.toolCallId) {
      continue;
    }
    if (block.type === "tool_group" && block.toolResult) {
      continue;
    }
    return index;
  }
  return -1;
}

function groupToolBlocks(blocks: RenderBlock[]): RenderBlock[] {
  const next: RenderBlock[] = [];
  for (const block of blocks) {
    if (block.type === "tool_call") {
      const matchIndex = findMatchingToolCallChunkIndex(next, block);
      if (matchIndex >= 0) {
        const match = next[matchIndex];
        next[matchIndex] = match.type === "tool_group"
          ? appendToolCallToGroup(match, block)
          : mergeToolCallBlocks(match, block);
        continue;
      }
      next.push(block);
      continue;
    }
    if (block.type === "tool_result") {
      const matchIndex = findMatchingToolCallIndex(next, block);
      if (matchIndex >= 0) {
        const match = next[matchIndex];
        next[matchIndex] = match.type === "tool_group"
          ? appendToolResultToGroup(match, block)
          : makeToolGroup(match, block);
      } else {
        continue;
      }
      continue;
    }
    next.push(block);
  }
  return next.map((block) => block.type === "tool_call" ? makeToolGroup(block) : block);
}

function appendRenderBlocks(
  existing: RenderBlock[],
  incoming: RenderBlock[],
): RenderBlock[] {
  const next = [...existing];
  for (const block of incoming) {
    const previous = next.length > 0 ? next[next.length - 1] : undefined;
    if (previous && shouldMergeAdjacentBlocks(previous, block)) {
      next[next.length - 1] = {
        ...previous,
        content: `${previous.content}${block.content}`,
      };
    } else {
      next.push(block);
    }
  }
  return groupToolBlocks(next);
}

function appendLiveItemBlocks(
  items: LiveItem[],
  itemKey: string,
  role: DisplayItem["role"],
  turnKey: string,
  timestamp: number,
  blocks: RenderBlock[],
): LiveItem[] {
  const index = items.findIndex((item) => item.key === itemKey);
  if (index === -1) {
    return [
      ...items,
      {
        key: itemKey,
        role,
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

function hasLiveBlocksForTurn(items: LiveItem[], turnKey: string): boolean {
  return items.some((item) => item.turnKey === turnKey && item.blocks.length > 0);
}

function liveItemsHaveToolResult(items: LiveItem[]): boolean {
  return items.some((item) =>
    item.blocks.some((block) =>
      block.type === "tool_result" ||
      (block.type === "tool_group" && Boolean(block.toolResult))
    )
  );
}

function messageBlocks(message: ConversationMessage): RenderBlock[] {
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
}

function displayRoleForMessage(message: ConversationMessage): DisplayItem["role"] {
  return message.role === "user" ? "user" : "actor";
}

function historyItemsFromMessages(messages: ConversationMessage[]): DisplayItem[] {
  const items: DisplayItem[] = [];
  for (const message of messages) {
    const role = displayRoleForMessage(message);
    const blocks = messageBlocks(message);
    const previous = items.length > 0 ? items[items.length - 1] : undefined;
    const shouldAppendToPreviousActorMessage = (
      role === "actor" &&
      previous?.role === "actor"
    );

    if (shouldAppendToPreviousActorMessage) {
      items[items.length - 1] = {
        ...previous,
        blocks: appendRenderBlocks(previous.blocks, blocks),
        timestamp: Math.min(previous.timestamp, message.timestamp),
      };
      continue;
    }

    items.push({
      key: `message:${message.message_id}`,
      role,
      blocks: appendRenderBlocks([], blocks),
      timestamp: message.timestamp,
    });
  }
  return items;
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
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const runningActors = actors.filter((a) => a.enabled);
  const [actorId, setActorId] = useState<string>(runningActors[0]?.id ?? "");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [streamReady, setStreamReady] = useState(false);
  const [conversationMetadata, setConversationMetadata] = useState<ConversationData | null>(null);
  const [actorLocked, setActorLocked] = useState(false);
  const [liveItems, setLiveItems] = useState<LiveItem[]>([]);
  const liveItemsRef = useRef<LiveItem[]>([]);
  const [streamStatus, setStreamStatus] = useState<"connected" | "disconnected">("connected");
  const bottomRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const sendingRef = useRef(false);
  const connectingSseRef = useRef(false);
  const connectingSsePromiseRef = useRef<Promise<void> | null>(null);
  const activeTurnKeyRef = useRef("");
  const currentAssistantItemKeyRef = useRef("");
  const liveItemIndexRef = useRef(0);
  const liveBlockIndexRef = useRef(0);
  const intentionalCloseRef = useRef(false);

  const actor = actors.find((a) => a.id === actorId);

  const connectSse = (): Promise<void> => {
    if (sseRef.current && streamReady) {
      return Promise.resolve();
    }
    if (connectingSsePromiseRef.current) {
      return connectingSsePromiseRef.current;
    }
    connectingSseRef.current = true;
    setStreamReady(false);

    const pending = new Promise<void>((resolve, reject) => {
      const appendAssistantEvent = (data: ConversationSSEEvent) => {
        const turnKey = activeTurnKeyRef.current || `event-${data.agent_id}-${Math.floor(data.timestamp * 1000)}`;
        const itemKey = currentAssistantItemKeyRef.current || liveItemKey(
          turnKey,
          "assistant",
          liveItemIndexRef.current++,
        );
        currentAssistantItemKeyRef.current = itemKey;
        const blocks = renderBlocksFromEvent(
          data,
          itemKey,
          () => liveBlockIndexRef.current++,
        );
        if (blocks.length === 0) {
          return;
        }
        setLiveItems(syncLiveItemsRef(
          liveItemsRef,
          (prev) => appendLiveItemBlocks(
            prev,
            itemKey,
            "actor",
            turnKey,
            data.timestamp,
            blocks,
          ),
        ));
      };

      const handleAssistantStreamEvent = (e: MessageEvent) => {
        const data = JSON.parse(e.data) as ConversationSSEEvent;
        appendAssistantEvent(data);
      };

      const handleFinalEvent = (e: MessageEvent) => {
        const data = JSON.parse(e.data) as ConversationSSEEvent;
        if (data.content.role === "assistant" && !eventHasToolCall(data)) {
          currentAssistantItemKeyRef.current = "";
        }
      };

      const handleTurnCompleted = (_e: MessageEvent) => {
        intentionalCloseRef.current = true;
        activeTurnKeyRef.current = "";
        currentAssistantItemKeyRef.current = "";
        sendingRef.current = false;
        setIsSending(false);
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

      const es = new EventSource(`/api/admin/conversations/${conversationId}/events`);
      sseRef.current = es;
      es.onopen = () => {
        connectingSseRef.current = false;
        connectingSsePromiseRef.current = null;
        setStreamReady(true);
        setStreamStatus("connected");
        intentionalCloseRef.current = false;
        resolve();
      };
      es.onerror = () => {
        connectingSseRef.current = false;
        connectingSsePromiseRef.current = null;
        if (!intentionalCloseRef.current) {
          setStreamStatus("disconnected");
          reject(new Error("Conversation stream setup failed"));
        }
      };

      es.addEventListener("thinking", handleAssistantStreamEvent);
      es.addEventListener("text", handleAssistantStreamEvent);
      es.addEventListener("output", handleAssistantStreamEvent);
      es.addEventListener("tool_call", handleAssistantStreamEvent);
      es.addEventListener("tool_result", handleAssistantStreamEvent);
      es.addEventListener("message", handleFinalEvent);
      es.addEventListener("turn_completed", handleTurnCompleted);
      es.addEventListener("error", handleErrorEvent);
    });
    connectingSsePromiseRef.current = pending;
    return pending;
  };

  const closeSse = (): void => {
    setStreamReady(false);
    setStreamStatus("disconnected");
    connectingSseRef.current = false;
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
    liveItemsRef.current = [];
    sendingRef.current = false;
    intentionalCloseRef.current = false;
    closeSse();
    setLiveItems([]);
    setMessages([]);
    setConversationMetadata(null);
    setActorLocked(false);
    setLoadingHistory(true);

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
        setMessages(persistedMessages);
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
  }, [conversationId]);

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
  }, [messages, liveItems]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || !actorId || sendingRef.current) return;
    if (conversationMetadata !== null && !streamReady) return;

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
    currentAssistantItemKeyRef.current = "";
    void (async () => {
      try {
        let metadata = conversationMetadata;
        if (metadata === null || messages.length === 0) {
          metadata = await createConversation({ actorId, conversationId });
          setConversationMetadata(metadata);
          setActorId(metadata.actor_id);
        }
        await ensureConversationAgent({ conversationId });
        await connectSse();
        await sendConversationMessage({ conversationId, text, messageId: userMsgId });
        setActorLocked(true);
      } catch (err: unknown) {
        if (activeTurnKeyRef.current === turnKey) {
          activeTurnKeyRef.current = "";
          currentAssistantItemKeyRef.current = "";
          sendingRef.current = false;
          setIsSending(false);
        }
        setMessages((prev) => prev.filter((message) => message.message_id !== userMsgId));
        setError(err instanceof Error ? err.message : "Send failed");
        try {
          const metadata = await getConversation(conversationId);
          if (metadata !== null) {
            setConversationMetadata(metadata);
            setActorId(metadata.actor_id);
            const persistedMessages = await getConversationMessages(conversationId);
            setMessages(persistedMessages);
            setActorLocked(persistedMessages.length > 0);
          }
        } catch { /* keep the original send error visible */ }
      }
    })();
  };

  const historyItems = historyItemsFromMessages(messages);
  const displayItems = [...historyItems, ...liveItems];
  const currentTurnHasLiveBlocks = activeTurnKeyRef.current
    ? hasLiveBlocksForTurn(liveItems, activeTurnKeyRef.current)
    : false;

  const conversationInProgress = (() => {
    if (messages.length === 0) return false;
    const lastMsg = messages[messages.length - 1];
    // If last message is a tool result, check if there's a follow-up assistant message
    if (lastMsg.role === "tool") return false;
    // If last message is assistant with tool_call, turn is in progress
    try {
      const parsed = JSON.parse(lastMsg.raw_content);
      if (Array.isArray(parsed) && parsed.some((b: Record<string,unknown>) => b.type === "tool_call")) {
        // Check if there's a subsequent tool result
        const hasToolResult = messages.some((m) => {
          if (m.role !== "tool") return false;
          try {
            const content = JSON.parse(m.raw_content);
            return Array.isArray(content) && content.some((b: Record<string,unknown>) => b.type === "tool_result");
          } catch { return false; }
        });
        const hasLiveToolResult = liveItemsHaveToolResult(liveItems);
        return !(hasToolResult || hasLiveToolResult);
      }
    } catch { /* ignore */ }
    return false;
  })();

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-3">
        <a href="/admin/conversations" onClick={(e) => { e.preventDefault(); window.history.back(); }}>
          <Button variant="ghost" size="icon"><ArrowLeft className="size-4" /></Button>
        </a>
        <div className="flex-1"><h2 className="text-sm font-semibold">{conversationId}</h2></div>
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

      {((conversationMetadata !== null && streamStatus === "disconnected") || conversationInProgress) && !loadingHistory && (
        <div className="mx-4 mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-950/30">
          后台运行中，结果可能不同步，若需查看更新请刷新~
        </div>
      )}

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
          disabled={!input.trim() || !actorId || (conversationMetadata !== null && !streamReady) || isSending}
        >
          {isSending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </Button>
      </form>
    </div>
  );
}

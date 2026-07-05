import type { HistoryItem } from "../../../shared/types/api";

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      const payload = item as Record<string, unknown>;
      if (typeof payload.text === "string") return payload.text;
      if (typeof payload.path === "string") return `[${String(payload.kind ?? "file")}: ${payload.path}]`;
      if (typeof payload.url === "string") return `[${String(payload.kind ?? "url")}: ${payload.url}]`;
      return "";
    })
    .filter(Boolean)
    .join("\n\n");
}

export type ConversationBlockType =
  | "thinking"
  | "text"
  | "tool_call"
  | "tool_result"
  | "tool_group"
  | "error"
  | "raw";

export interface RenderBlock {
  key: string;
  type: ConversationBlockType;
  content: string;
  toolArgs?: string;
  toolCallId?: string;
  toolStreamId?: string;
  toolName?: string;
  toolResult?: string;
  toolStatus?: string;
}

export interface DisplayItem {
  key: string;
  role: "user" | "actor";
  blocks: RenderBlock[];
  timestamp: number;
  createdAt?: string | null;
  turnKey?: string;
  streaming?: boolean;
}

export interface ToolDisplay {
  name: string;
  argsText: string;
  code?: string;
}

export interface StreamEventFrame {
  group_id: string;
  kind: string;
  payload: Record<string, unknown>;
}

export type ConversationPhase = "idle" | "sending" | "streaming" | "error";

const PREFIX_KINDS = new Set(["tool_specs", "system_prompt", "cost"]);

export function toolDisplay(block: RenderBlock): ToolDisplay {
  const name = block.toolName ?? (block.content.replace(/^Tool:\s*/, "") || "tool");
  const args = toolDisplayArgs(block.toolArgs);
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

function toolDisplayArgs(toolArgs: string | undefined): unknown {
  if (!toolArgs) {
    return undefined;
  }
  const raw = parseJsonMaybe(toolArgs);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return raw;
  }

  const source = raw as Record<string, unknown>;
  const wrappedArgs = source.arguments ?? source.args ?? source.input;
  if (wrappedArgs === undefined) {
    return source;
  }
  return typeof wrappedArgs === "string" ? parseJsonMaybe(wrappedArgs) : wrappedArgs;
}

export function parseJsonMaybe(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

export function renderBlocksFromStreamEvent(
  event: StreamEventFrame,
  keyPrefix: string,
  nextBlockIndex: () => number,
): RenderBlock[] {
  if (event.kind === "stream_stop") {
    return [];
  }

  const key = `${keyPrefix}:block:${nextBlockIndex()}`;
  const payload = event.payload;

  if (event.kind === "reasoning_delta") {
    return [{ key, type: "thinking", content: String(payload.text ?? "") }];
  }
  if (event.kind === "text_delta") {
    return [{ key, type: "text", content: String(payload.text ?? "") }];
  }
  if (event.kind === "tool_name") {
    const name = String(payload.name ?? "tool");
    const id = String(payload.id ?? event.group_id);
    return [{
      key: `${keyPrefix}:tool:${id}`,
      type: "tool_call",
      content: `Tool: ${name}`,
      toolArgs: "",
      toolCallId: id,
      toolStreamId: event.group_id,
      toolName: name,
      toolStatus: "running",
    }];
  }
  if (event.kind === "tool_arguments_delta") {
    const id = event.group_id;
    return [{
      key: `${keyPrefix}:tool:${id}`,
      type: "tool_call",
      content: "Tool: tool",
      toolArgs: String(payload.text ?? ""),
      toolStreamId: id,
      toolStatus: "running",
    }];
  }
  if (event.kind === "tool_arguments_end") {
    return [];
  }
  return [];
}

export function renderBlocksFromToolResults(
  results: unknown[],
  keyPrefix: string,
  nextBlockIndex: () => number,
): RenderBlock[] {
  return results
    .filter((result): result is Record<string, unknown> => Boolean(result && typeof result === "object"))
    .map((result) => {
      const toolCallId = String(result.tool_call_id ?? "");
      return {
        key: `${keyPrefix}:tool-result:${toolCallId || nextBlockIndex()}`,
        type: "tool_result" as const,
        content: contentText(result.content),
        toolCallId,
        toolStatus: "completed",
      };
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
    toolStreamId: call.toolStreamId,
    toolName: name,
    toolResult: result?.content,
    toolStatus: result?.toolStatus,
  };
}

function appendToolResultToGroup(group: RenderBlock, result: RenderBlock): RenderBlock {
  return {
    ...group,
    toolResult: mergeToolResultContent(group.toolResult, result.content),
    toolStatus: result.toolStatus ?? group.toolStatus,
  };
}

export function markToolBlocksCompleted(blocks: RenderBlock[]): RenderBlock[] {
  return blocks.map((block) => {
    if (block.type === "tool_group" && block.toolResult) {
      return { ...block, toolStatus: "completed" };
    }
    if (block.type === "tool_result") {
      return { ...block, toolStatus: "completed" };
    }
    return block;
  });
}

function mergeToolResultContent(existing: string | undefined, incoming: string): string {
  if (!existing) return incoming;
  if (incoming.startsWith(existing)) return incoming;
  if (existing.startsWith(incoming)) return existing;
  return `${existing}${incoming}`;
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
    for (const argsKey of ["arguments", "args", "input"]) {
      const leftValue = leftRecord[argsKey];
      const rightValue = rightRecord[argsKey];
      if (typeof leftValue === "string" && typeof rightValue === "string") {
        merged[argsKey] = rightValue.startsWith(leftValue) ? rightValue : `${leftValue}${rightValue}`;
      } else if (rightValue === undefined && leftValue !== undefined) {
        merged[argsKey] = leftValue;
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
  if (left.toolStreamId && right.toolStreamId) {
    return left.toolStreamId === right.toolStreamId;
  }
  if (left.toolCallId && right.toolCallId) {
    return left.toolCallId === right.toolCallId;
  }
  return Boolean(left.toolName && right.toolName && left.toolName === right.toolName);
}

function sameToolCall(left: RenderBlock, right: RenderBlock): boolean {
  if (left.toolStreamId && right.toolStreamId) {
    return left.toolStreamId === right.toolStreamId;
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
    toolStreamId: left.toolStreamId ?? right.toolStreamId,
    toolName: name,
    toolStatus: right.toolStatus ?? left.toolStatus,
  };
}

function mergeToolGroupBlocks(left: RenderBlock, right: RenderBlock): RenderBlock {
  const merged = mergeToolCallBlocks(left, right);
  const leftResult = left.toolResult;
  const rightResult = right.toolResult;
  return {
    ...merged,
    type: "tool_group",
    content: merged.toolName ?? left.content,
    toolResult: leftResult && rightResult
      ? mergeToolResultContent(leftResult, rightResult)
      : rightResult ?? leftResult,
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

function findMatchingToolGroupIndex(blocks: RenderBlock[], group: RenderBlock): number {
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    if (block.type !== "tool_group") {
      continue;
    }
    if (sameToolCall(block, group)) {
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
      if (!result.toolCallId || block.toolCallId !== result.toolCallId) {
        continue;
      }
    }
    return index;
  }
  return -1;
}

function findMatchingToolResultIndex(blocks: RenderBlock[], result: RenderBlock): number {
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    if (block.type !== "tool_result") {
      continue;
    }
    if ((result.toolCallId || block.toolCallId) && result.toolCallId !== block.toolCallId) {
      continue;
    }
    if (!result.toolCallId && !block.toolCallId && result.toolName !== block.toolName) {
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
    if (block.type === "tool_group") {
      const matchIndex = findMatchingToolGroupIndex(next, block);
      if (matchIndex >= 0) {
        next[matchIndex] = mergeToolGroupBlocks(next[matchIndex], block);
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
        const resultIndex = findMatchingToolResultIndex(next, block);
        if (resultIndex >= 0) {
          const match = next[resultIndex];
          next[resultIndex] = {
            ...match,
            content: mergeToolResultContent(match.content, block.content),
            toolStatus: block.toolStatus ?? match.toolStatus,
          };
        } else {
          next.push(block);
        }
      }
      continue;
    }
    next.push(block);
  }
  return next.map((block) => block.type === "tool_call" ? makeToolGroup(block) : block);
}

export function appendRenderBlocks(
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

function historyItemToBlocks(item: HistoryItem): RenderBlock[] {
  const key = `history-${item.seq}`;

  if (item.kind === "input") {
    const role = String(item.payload.role ?? "user");
    if (role === "developer") {
      return [{
        key,
        type: "raw",
        content: contentText(item.payload.content),
      }];
    }
    return [{ key, type: "text", content: contentText(item.payload.content) }];
  }

  if (item.kind === "gen_reasoning") {
    return [{ key, type: "thinking", content: String(item.payload.text ?? "") }];
  }

  if (item.kind === "gen_text") {
    return [{ key, type: "text", content: String(item.payload.text ?? "") }];
  }

  if (item.kind === "gen_tool_call") {
    const name = String(item.payload.name ?? "tool");
    const id = String(item.payload.id ?? key);
    const args = String(item.payload.arguments ?? "");
    return [{
      key,
      type: "tool_call",
      content: `Tool: ${name}`,
      toolArgs: args,
      toolCallId: id,
      toolStreamId: id,
      toolName: name,
      toolStatus: "completed",
    }];
  }

  if (item.kind === "tool_result") {
    return [{
      key,
      type: "tool_result",
      content: contentText(item.payload.content),
      toolCallId: String(item.payload.tool_call_id ?? ""),
      toolStatus: "completed",
    }];
  }

  return [{
    key,
    type: "raw",
    content: JSON.stringify(item.payload, null, 2),
  }];
}

function parseTimestamp(createdAt: string | null): number {
  if (!createdAt) return 0;
  const value = Date.parse(createdAt);
  return Number.isNaN(value) ? 0 : value;
}

export function historyItemsFromHistory(history: HistoryItem[]): DisplayItem[] {
  const items: DisplayItem[] = [];

  for (const item of history) {
    if (PREFIX_KINDS.has(item.kind)) {
      continue;
    }

    if (item.kind === "input") {
      const role = String(item.payload.role ?? "user");
      if (role === "developer") {
        const previous = items[items.length - 1];
        const blocks = historyItemToBlocks(item);
        if (previous?.role === "actor") {
          items[items.length - 1] = {
            ...previous,
            blocks: appendRenderBlocks(previous.blocks, blocks),
          };
        } else {
          items.push({
            key: `history:${item.seq}`,
            role: "actor",
            blocks: appendRenderBlocks([], blocks),
            timestamp: parseTimestamp(item.created_at),
            createdAt: item.created_at,
          });
        }
        continue;
      }

      items.push({
        key: `history:${item.seq}`,
        role: "user",
        blocks: appendRenderBlocks([], historyItemToBlocks(item)),
        timestamp: parseTimestamp(item.created_at),
        createdAt: item.created_at,
      });
      continue;
    }

    const blocks = historyItemToBlocks(item);
    const previous = items[items.length - 1];
    if (previous?.role === "actor") {
      items[items.length - 1] = {
        ...previous,
        blocks: appendRenderBlocks(previous.blocks, blocks),
        timestamp: Math.min(previous.timestamp, parseTimestamp(item.created_at)),
      };
      continue;
    }

    items.push({
      key: `history:${item.seq}`,
      role: "actor",
      blocks: appendRenderBlocks([], blocks),
      timestamp: parseTimestamp(item.created_at),
      createdAt: item.created_at,
    });
  }

  return items;
}

function uniqueHistoryItems(history: HistoryItem[]): HistoryItem[] {
  const seenSeq = new Set<number>();
  const unique: HistoryItem[] = [];
  for (const item of history) {
    if (seenSeq.has(item.seq)) {
      continue;
    }
    seenSeq.add(item.seq);
    unique.push(item);
  }
  return unique;
}

function mergeLiveAssistantTurn(
  items: DisplayItem[],
  liveBlocks: RenderBlock[],
  turnKey: string | undefined,
  phase: ConversationPhase,
): DisplayItem[] {
  const active = phase === "sending" || phase === "streaming";
  if (!active && liveBlocks.length === 0) {
    return items;
  }

  const lastIndex = items.length - 1;
  const last = items[lastIndex];
  const streaming = phase === "streaming" || (phase === "sending" && liveBlocks.length === 0);

  if (last?.role === "actor" && ((last.turnKey === turnKey && turnKey) || active)) {
    return items.map((item, index) => (
      index === lastIndex
        ? {
            ...item,
            blocks: appendRenderBlocks(item.blocks, liveBlocks),
            streaming,
          }
        : item
    ));
  }

  if (liveBlocks.length === 0 && phase === "sending") {
    return items;
  }

  return [
    ...items,
    {
      key: "live-assistant",
      role: "actor" as const,
      blocks: appendRenderBlocks([], liveBlocks),
      timestamp: Date.now(),
      turnKey,
      streaming,
    },
  ];
}

export function buildDisplayItems({
  history,
  liveBlocks = [],
  optimisticUserText,
  phase,
  turnKey,
}: {
  history: HistoryItem[];
  liveBlocks?: RenderBlock[];
  optimisticUserText: string | null;
  phase: ConversationPhase;
  turnKey?: string;
}): DisplayItem[] {
  let items = historyItemsFromHistory(uniqueHistoryItems(history));

  if (
    optimisticUserText
    && !items.some((item) => (
      item.role === "user"
      && item.blocks.some((block) => block.type === "text" && block.content === optimisticUserText)
    ))
  ) {
    items = [
      ...items,
      {
        key: "optimistic-user",
        role: "user",
        blocks: [{ key: "optimistic-user:text", type: "text", content: optimisticUserText }],
        timestamp: Date.now(),
      },
    ];
  }

  return mergeLiveAssistantTurn(items, liveBlocks, turnKey, phase);
}

export function isTerminalStreamStop(payload: Record<string, unknown>): boolean {
  const reason = payload.reason;
  return reason !== "tool_calls" && reason !== "function_call";
}

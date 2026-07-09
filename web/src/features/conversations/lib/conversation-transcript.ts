import type { HistoryItem } from "../../../shared/types/api";
import { extractToolStringArg } from "../../../shared/lib/tool-renderers.ts";
import { formatWorkspaceRef } from "../../../shared/lib/workspace-ref.ts";

const REAL_TIME_CONTEXT_MARKER = "[yuubot-real-time-context]";
const REAL_TIME_CONTEXT_SEPARATOR = "\n---\n";

export function stripRealTimeContext(text: string): string {
  if (!text.startsWith(REAL_TIME_CONTEXT_MARKER)) return text;
  const separatorIndex = text.indexOf(REAL_TIME_CONTEXT_SEPARATOR);
  if (separatorIndex < 0) return text;
  return text.slice(separatorIndex + REAL_TIME_CONTEXT_SEPARATOR.length);
}

function contentText(content: unknown): string {
  if (typeof content === "string") return stripRealTimeContext(content);
  if (!Array.isArray(content)) return "";
  return content
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      const payload = item as Record<string, unknown>;
      if (typeof payload.text === "string") return stripRealTimeContext(payload.text);
      if (typeof payload.path === "string") return formatWorkspaceRef(payload.path);
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
  const streamedCode = block.toolArgs !== undefined
    ? extractToolStringArg(block.toolArgs, "code")
    : null;
  const code = args && typeof args === "object" && typeof (args as Record<string, unknown>).code === "string"
    ? String((args as Record<string, unknown>).code)
    : streamedCode ?? undefined;
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
  if (event.kind === "tool_result_delta") {
    const id = String(payload.tool_call_id ?? event.group_id);
    return [{
      key: `${keyPrefix}:tool-result-stream:${id}`,
      type: "tool_result",
      content: String(payload.text ?? ""),
      toolCallId: id,
      toolName: typeof payload.tool_name === "string" ? payload.tool_name : undefined,
      toolStatus: "running",
    }];
  }
  if (event.kind === "tool_result_end") {
    const id = String(payload.tool_call_id ?? event.group_id);
    const fallback = [{ kind: "text", text: String(payload.text ?? "") }];
    return [{
      key: `${keyPrefix}:tool-result:${id}`,
      type: "tool_result",
      content: contentText(payload.content ?? fallback),
      toolCallId: id,
      toolName: typeof payload.tool_name === "string" ? payload.tool_name : undefined,
      toolStatus: "completed",
    }];
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
    toolStatus: result?.toolStatus ?? call.toolStatus,
  };
}

function appendToolResultToGroup(group: RenderBlock, result: RenderBlock): RenderBlock {
  const replaceRunningResult = group.toolStatus !== "completed" && result.toolStatus === "completed";
  const replaceStreamingSnapshot = result.toolStatus === "running";
  return {
    ...group,
    toolResult: (replaceRunningResult || replaceStreamingSnapshot)
      ? result.content
      : mergeToolResultContent(group.toolResult, result.content),
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
  if (left.toolCallId && right.toolCallId) {
    return left.toolCallId === right.toolCallId;
  }
  if (left.toolStreamId && right.toolStreamId) {
    return left.toolStreamId === right.toolStreamId;
  }
  return Boolean(left.toolName && right.toolName && left.toolName === right.toolName);
}

function sameToolCall(left: RenderBlock, right: RenderBlock): boolean {
  if (left.toolCallId && right.toolCallId) {
    return left.toolCallId === right.toolCallId;
  }
  if (left.toolStreamId && right.toolStreamId) {
    return left.toolStreamId === right.toolStreamId;
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
          const replaceRunningResult = match.toolStatus !== "completed" && block.toolStatus === "completed";
          next[resultIndex] = {
            ...match,
            content: replaceRunningResult ? block.content : mergeToolResultContent(match.content, block.content),
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
      toolName: name,
      toolStatus: "running",
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
  let currentUserSeq: number | null = null;

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

      currentUserSeq = item.seq;
      items.push({
        key: `turn:${item.seq}:user`,
        role: "user",
        blocks: appendRenderBlocks([], historyItemToBlocks(item)),
        timestamp: parseTimestamp(item.created_at),
        createdAt: item.created_at,
        turnKey: `turn:${item.seq}`,
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

    const turnKey = currentUserSeq === null ? `history:${item.seq}` : `turn:${currentUserSeq}`;
    items.push({
      key: `${turnKey}:actor`,
      role: "actor",
      blocks: appendRenderBlocks([], blocks),
      timestamp: parseTimestamp(item.created_at),
      createdAt: item.created_at,
      turnKey,
    });
  }

  return items;
}

export function uniqueHistoryItems(history: HistoryItem[]): HistoryItem[] {
  const seenSeq = new Set<number>();
  const unique: HistoryItem[] = [];
  for (const item of [...history].sort((left, right) => left.seq - right.seq)) {
    if (seenSeq.has(item.seq)) {
      continue;
    }
    seenSeq.add(item.seq);
    unique.push(item);
  }
  return unique;
}

function lastUserTurnKey(items: DisplayItem[]): string | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item?.role === "user") {
      return item.turnKey ?? item.key.replace(/:user$/, "");
    }
  }
  return undefined;
}

function hasDurableActorForTurn(items: DisplayItem[], turnKey: string): boolean {
  return items.some((item) => item.role === "actor" && item.turnKey === turnKey);
}

function liveBlocksAfterDurablePrefix(liveBlocks: RenderBlock[], durableBlocks: RenderBlock[]): RenderBlock[] {
  let liveIndex = 0;
  let durableIndex = 0;

  while (liveIndex < liveBlocks.length && durableIndex < durableBlocks.length) {
    const liveBlock = liveBlocks[liveIndex];
    const durableBlock = durableBlocks[durableIndex];
    if (!durableCoversLiveBlock(durableBlock, liveBlock)) {
      break;
    }
    liveIndex += 1;
    durableIndex += 1;
  }

  return liveBlocks.slice(liveIndex);
}

function durableCoversLiveBlock(durableBlock: RenderBlock, liveBlock: RenderBlock): boolean {
  if (durableBlock.type === liveBlock.type && (liveBlock.type === "text" || liveBlock.type === "thinking")) {
    return true;
  }
  if (isToolLikeBlock(durableBlock) && isToolLikeBlock(liveBlock)) {
    if (!sameToolIdentity(durableBlock, liveBlock)) {
      return false;
    }
    if (!textCovers(durableBlock.toolArgs, liveBlock.toolArgs)) {
      return false;
    }
    return textCovers(durableBlock.toolResult, liveBlock.toolResult);
  }
  return durableBlock.type === liveBlock.type && durableBlock.content === liveBlock.content;
}

function isToolLikeBlock(block: RenderBlock): boolean {
  return block.type === "tool_group" || block.type === "tool_call" || block.type === "tool_result";
}

function sameToolIdentity(left: RenderBlock, right: RenderBlock): boolean {
  if (left.toolCallId || right.toolCallId) {
    return left.toolCallId === right.toolCallId;
  }
  if (left.toolStreamId || right.toolStreamId) {
    return left.toolStreamId === right.toolStreamId;
  }
  if (left.toolName || right.toolName) {
    return left.toolName === right.toolName;
  }
  return left.content === right.content;
}

function textCovers(durable: string | undefined, live: string | undefined): boolean {
  if (!live) {
    return true;
  }
  if (!durable) {
    return false;
  }
  return durable.startsWith(live) || live.startsWith(durable);
}

function mergeLiveAssistantTurn({
  items,
  liveBlocks,
  turnKey,
  phase,
  previewStartedAt,
}: {
  items: DisplayItem[];
  liveBlocks: RenderBlock[];
  turnKey: string | undefined;
  phase: ConversationPhase;
  previewStartedAt: number;
}): DisplayItem[] {
  const active = phase === "sending" || phase === "streaming";
  if (!active || liveBlocks.length === 0) {
    return items;
  }

  const streaming = phase === "streaming";
  const stableTurnKey = lastUserTurnKey(items) ?? turnKey ?? "live";
  const liveKey = `${stableTurnKey}:actor`;
  if (hasDurableActorForTurn(items, stableTurnKey)) {
    return items.map((item) => {
      if (item.key !== liveKey) {
        return item;
      }
      const overlay = liveBlocksAfterDurablePrefix(liveBlocks, item.blocks);
      return {
        ...item,
        blocks: overlay.length > 0 ? appendRenderBlocks(item.blocks, overlay) : item.blocks,
        streaming,
        turnKey: stableTurnKey,
      };
    });
  }

  const existingIndex = items.findIndex((item) => item.key === liveKey);
  if (existingIndex >= 0) {
    return items.map((item, index) => (
      index === existingIndex
        ? { ...item, blocks: liveBlocks, streaming, turnKey: stableTurnKey }
        : item
    ));
  }

  return [
    ...items,
    {
      key: liveKey,
      role: "actor" as const,
      blocks: liveBlocks,
      timestamp: previewStartedAt,
      turnKey: stableTurnKey,
      streaming,
    },
  ];
}

export interface PendingUserMessage {
  clientKey: string;
  text: string;
  timestamp: number;
}

export interface TranscriptState {
  history: HistoryItem[];
  liveBlocks: RenderBlock[];
  phase: ConversationPhase;
  turnKey?: string;
  previewStartedAt: number;
  pendingUser: PendingUserMessage | null;
}

export type TranscriptAction =
  | { type: "reset"; history: HistoryItem[] }
  | { type: "begin_turn"; turnKey: string; now: number }
  | { type: "history_append"; item: HistoryItem }
  | { type: "pending_user"; clientKey: string; text: string; now: number }
  | { type: "append_blocks"; blocks: RenderBlock[] }
  | { type: "mark_tools_completed" }
  | { type: "finish_turn" }
  | { type: "set_phase"; phase: ConversationPhase }
  | { type: "clear_live" };

export function createTranscriptState(history: HistoryItem[] = []): TranscriptState {
  return {
    history: uniqueHistoryItems(history),
    liveBlocks: [],
    phase: "idle",
    previewStartedAt: Date.now(),
    pendingUser: null,
  };
}

function isUserInputItem(item: HistoryItem): boolean {
  return item.kind === "input" && String(item.payload.role ?? "user") === "user";
}

export function transcriptReducer(state: TranscriptState, action: TranscriptAction): TranscriptState {
  if (action.type === "reset") {
    return createTranscriptState(action.history);
  }
  if (action.type === "begin_turn") {
    return {
      ...state,
      liveBlocks: [],
      phase: "sending",
      turnKey: action.turnKey,
      previewStartedAt: action.now,
    };
  }
  if (action.type === "pending_user") {
    return {
      ...state,
      pendingUser: {
        clientKey: action.clientKey,
        text: action.text,
        timestamp: action.now,
      },
    };
  }
  if (action.type === "history_append") {
    if (PREFIX_KINDS.has(action.item.kind) || state.history.some((item) => item.seq === action.item.seq)) {
      return state;
    }
    return {
      ...state,
      history: uniqueHistoryItems([...state.history, action.item]),
      pendingUser: isUserInputItem(action.item) ? null : state.pendingUser,
    };
  }
  if (action.type === "append_blocks") {
    if (!action.blocks.length) {
      return state;
    }
    return {
      ...state,
      liveBlocks: appendRenderBlocks(state.liveBlocks, action.blocks),
      phase: state.phase === "sending" ? "streaming" : state.phase,
    };
  }
  if (action.type === "mark_tools_completed") {
    return {
      ...state,
      liveBlocks: markToolBlocksCompleted(state.liveBlocks),
      phase: "streaming",
    };
  }
  if (action.type === "finish_turn") {
    return {
      ...state,
      phase: "idle",
      liveBlocks: [],
      turnKey: undefined,
    };
  }
  if (action.type === "set_phase") {
    return {
      ...state,
      phase: action.phase,
    };
  }
  if (action.type === "clear_live") {
    return {
      ...state,
      liveBlocks: [],
      turnKey: undefined,
    };
  }
  return state;
}

export function transcriptDisplayItems(state: TranscriptState): DisplayItem[] {
  return buildDisplayItems({
    history: state.history,
    liveBlocks: state.liveBlocks,
    phase: state.phase,
    turnKey: state.turnKey,
    previewStartedAt: state.previewStartedAt,
    pendingUser: state.pendingUser,
  });
}

function pendingUserDisplayItem(pendingUser: PendingUserMessage): DisplayItem {
  return {
    key: pendingUser.clientKey,
    role: "user",
    blocks: [{
      key: `${pendingUser.clientKey}:text`,
      type: "text",
      content: pendingUser.text,
    }],
    timestamp: pendingUser.timestamp,
    turnKey: pendingUser.clientKey,
  };
}

export function buildDisplayItems({
  history,
  liveBlocks = [],
  phase,
  turnKey,
  previewStartedAt = Date.now(),
  pendingUser = null,
}: {
  history: HistoryItem[];
  liveBlocks?: RenderBlock[];
  phase: ConversationPhase;
  turnKey?: string;
  previewStartedAt?: number;
  pendingUser?: PendingUserMessage | null;
}): DisplayItem[] {
  const items = historyItemsFromHistory(uniqueHistoryItems(history));
  if (pendingUser) {
    items.push(pendingUserDisplayItem(pendingUser));
  }

  return mergeLiveAssistantTurn({
    items,
    liveBlocks,
    phase,
    turnKey,
    previewStartedAt,
  });
}

export function contentItemsToText(content: Array<{ kind: string; text?: string; path?: string }>): string {
  return content
    .map((item) => {
      if (item.kind === "text" && item.text) {
        return item.text;
      }
      if (item.path) {
        return formatWorkspaceRef(item.path);
      }
      return "";
    })
    .filter(Boolean)
    .join("\n\n");
}

export function isTerminalStreamStop(payload: Record<string, unknown>): boolean {
  const reason = payload.reason;
  return reason !== "tool_calls" && reason !== "function_call";
}

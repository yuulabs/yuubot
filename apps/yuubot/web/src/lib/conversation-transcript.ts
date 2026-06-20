import type { ConversationMessage, ConversationSSEEvent } from "@/types/api";

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
  toolName?: string;
  toolResult?: string;
  toolStatus?: string;
}

export interface DisplayItem {
  key: string;
  role: "user" | "actor";
  blocks: RenderBlock[];
  timestamp: number;
}

/** Extract human-readable text from a raw block dict without dropping unknown content. */
export function extractBlockText(block: unknown): string {
  if (block === null || block === undefined) {
    return "";
  }
  if (typeof block !== "object") {
    return String(block);
  }
  const b = block as Record<string, unknown>;

  const content = b.content;
  if (typeof content === "string") return content;
  if (content && typeof content === "object") {
    const c = content as Record<string, unknown>;
    if (c.type === "text" && typeof c.text === "string") return c.text;
    if (c.type === "thinking" && typeof c.thinking === "string") return c.thinking;
    return JSON.stringify(c);
  }

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

export function renderBlockFromRaw(
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

export function rawBlockSource(block: unknown): Record<string, unknown> {
  if (!block || typeof block !== "object") {
    return { type: "text", text: typeof block === "string" ? block : String(block) };
  }
  const raw = block as Record<string, unknown>;
  if ((raw.type === "content" || typeof raw.type !== "string") && raw.content && typeof raw.content === "object") {
    return raw.content as Record<string, unknown>;
  }
  if (typeof raw.type === "string") {
    return raw;
  }
  if (typeof raw.content === "string") {
    return { type: "text", text: raw.content };
  }
  return raw;
}

export function blockType(
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

export function toolCallId(source: Record<string, unknown>, type: ConversationBlockType): string | undefined {
  if (type === "tool_call" && typeof source.id === "string") {
    return source.id;
  }
  if (typeof source.tool_call_id === "string") {
    return source.tool_call_id;
  }
  return undefined;
}

export function toolName(source: Record<string, unknown>): string | undefined {
  if (typeof source.name === "string") {
    return source.name;
  }
  if (typeof source.tool_name === "string") {
    return source.tool_name;
  }
  return undefined;
}

export function toolStatus(source: Record<string, unknown>): string | undefined {
  return typeof source.status === "string" ? source.status : undefined;
}

export function parseJsonMaybe(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

export function renderBlocksFromEvent(
  data: ConversationSSEEvent,
  keyPrefix: string,
  nextBlockIndex: () => number,
): RenderBlock[] {
  const fallbackType = eventFallbackType(data);
  const blocks = data.content.blocks as Array<unknown> | undefined;
  return (blocks ?? []).flatMap<RenderBlock>((block) => {
    const source = rawBlockSource(block);
    const rawType = typeof source.type === "string" ? source.type : fallbackType;
    const type = eventBlockType(rawType, fallbackType);
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
      toolCallId: toolCallId(source, type) ?? eventToolCallId(data),
      toolName: toolName(source) ?? eventToolName(data),
      toolStatus: toolStatus(source) ?? eventToolStatus(data),
    }];
  });
}

function eventBlockType(
  rawType: string,
  fallbackType: Exclude<ConversationBlockType, "raw">,
): ConversationBlockType {
  if (fallbackType === "tool_result" && rawType !== "tool_call") {
    return "tool_result";
  }
  return blockType(rawType, fallbackType);
}

function eventToolCallId(data: ConversationSSEEvent): string | undefined {
  return typeof data.content.tool_call_id === "string" ? data.content.tool_call_id : undefined;
}

function eventToolName(data: ConversationSSEEvent): string | undefined {
  return typeof data.content.tool_name === "string" ? data.content.tool_name : undefined;
}

function eventToolStatus(data: ConversationSSEEvent): string | undefined {
  return typeof data.content.status === "string" ? data.content.status : undefined;
}

export function eventFallbackType(data: ConversationSSEEvent): Exclude<ConversationBlockType, "raw"> {
  if (data.event_type === "thinking") return "thinking";
  if (data.event_type === "tool_call") return "tool_call";
  if (data.event_type === "tool_result") return "tool_result";
  if (data.event_type === "error") return "error";
  return "text";
}

export function liveItemKey(turnKey: string, kind: string, index: number): string {
  return `live:${turnKey}:${kind}:${index}`;
}

export function eventHasToolCall(data: ConversationSSEEvent): boolean {
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
    toolResult: mergeToolResultContent(group.toolResult, result.content),
    toolStatus: result.toolStatus ?? group.toolStatus,
  };
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

export function messageBlocks(message: ConversationMessage): RenderBlock[] {
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

export function displayRoleForMessage(message: ConversationMessage): DisplayItem["role"] {
  return message.role === "user" ? "user" : "actor";
}

export function historyItemsFromMessages(messages: ConversationMessage[]): DisplayItem[] {
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

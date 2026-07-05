import assert from "node:assert/strict";
import { test } from "node:test";

import {
  appendRenderBlocks,
  buildDisplayItems,
  historyItemsFromHistory,
  renderBlocksFromStreamEvent,
  toolDisplay,
  type RenderBlock,
} from "./conversation-transcript.ts";
import type { HistoryItem } from "../../../shared/types/api.ts";

test("toolDisplay renders live bare tool arguments", () => {
  const block: RenderBlock = {
    key: "tool",
    type: "tool_group",
    content: "bash",
    toolName: "bash",
    toolArgs: JSON.stringify({ command: "ls -la" }),
  };

  assert.deepEqual(toolDisplay(block), {
    name: "bash",
    argsText: JSON.stringify({ command: "ls -la" }, null, 2),
    code: undefined,
  });
});

test("toolDisplay renders persisted wrapped tool arguments", () => {
  const block: RenderBlock = {
    key: "tool",
    type: "tool_group",
    content: "bash",
    toolName: "bash",
    toolArgs: JSON.stringify({ arguments: { command: "ls -la" } }),
  };

  assert.deepEqual(toolDisplay(block), {
    name: "bash",
    argsText: JSON.stringify({ command: "ls -la" }, null, 2),
    code: undefined,
  });
});

test("stream tool events render grouped bash command", () => {
  let blockIndex = 0;
  const next = () => blockIndex++;
  const nameBlock = renderBlocksFromStreamEvent(
    {
      group_id: "tool-1",
      kind: "tool_name",
      payload: { id: "tool-1", name: "bash" },
    },
    "item",
    next,
  );
  const argsBlock = renderBlocksFromStreamEvent(
    {
      group_id: "tool-1",
      kind: "tool_arguments_delta",
      payload: { text: JSON.stringify({ command: "ls -la" }) },
    },
    "item",
    next,
  );

  const [group] = appendRenderBlocks([], [...nameBlock, ...argsBlock]);
  assert.equal(group?.type, "tool_group");
  assert.equal(toolDisplay(group).argsText, JSON.stringify({ command: "ls -la" }, null, 2));
});

test("stream tool arguments merge with real tool call id results", () => {
  let blockIndex = 0;
  const next = () => blockIndex++;
  const nameBlock = renderBlocksFromStreamEvent(
    {
      group_id: "tool-0",
      kind: "tool_name",
      payload: { id: "call-real", name: "execute_python" },
    },
    "item",
    next,
  );
  const argsBlock = renderBlocksFromStreamEvent(
    {
      group_id: "tool-0",
      kind: "tool_arguments_delta",
      payload: { text: JSON.stringify({ code: "print(1)" }) },
    },
    "item",
    next,
  );
  const resultBlock: RenderBlock = {
    key: "item:tool-result:call-real",
    type: "tool_result",
    content: "1\n",
    toolCallId: "call-real",
    toolStatus: "completed",
  };

  const blocks = appendRenderBlocks([], [...nameBlock, ...argsBlock, resultBlock]);

  assert.equal(blocks.length, 1);
  assert.equal(blocks[0]?.type, "tool_group");
  assert.equal(blocks[0]?.toolName, "execute_python");
  assert.equal(blocks[0]?.toolCallId, "call-real");
  assert.equal(toolDisplay(blocks[0]!).code, "print(1)");
  assert.equal(blocks[0]?.toolResult, "1\n");
  assert.equal(blocks[0]?.toolStatus, "completed");
});

test("appendRenderBlocks merges duplicate live and persisted tool groups", () => {
  const persisted: RenderBlock = {
    key: "history-tool",
    type: "tool_group",
    content: "execute_python",
    toolArgs: JSON.stringify({ code: "print(1)" }),
    toolCallId: "call-1",
    toolName: "execute_python",
    toolStatus: "completed",
  };
  const live: RenderBlock = {
    key: "live-tool",
    type: "tool_group",
    content: "execute_python",
    toolArgs: "",
    toolCallId: "call-1",
    toolName: "execute_python",
    toolStatus: "running",
  };

  const blocks = appendRenderBlocks([persisted], [live]);

  assert.equal(blocks.length, 1);
  assert.equal(blocks[0]?.type, "tool_group");
  assert.equal(blocks[0]?.toolName, "execute_python");
  assert.equal(toolDisplay(blocks[0]!).code, "print(1)");
});

test("history groups tool call and result into one actor bubble", () => {
  const history: HistoryItem[] = [
    {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hello" }] },
      created_at: "2026-01-01T00:00:00Z",
    },
    {
      seq: 1,
      kind: "gen_reasoning",
      payload: { text: "thinking..." },
      created_at: "2026-01-01T00:00:01Z",
    },
    {
      seq: 2,
      kind: "gen_tool_call",
      payload: { id: "tool-1", name: "bash", arguments: JSON.stringify({ command: "ls -la" }) },
      created_at: "2026-01-01T00:00:02Z",
    },
    {
      seq: 3,
      kind: "tool_result",
      payload: { tool_call_id: "tool-1", content: [{ kind: "text", text: "ok\n" }] },
      created_at: "2026-01-01T00:00:03Z",
    },
    {
      seq: 4,
      kind: "gen_text",
      payload: { text: "done" },
      created_at: "2026-01-01T00:00:04Z",
    },
  ];

  const items = historyItemsFromHistory(history);
  assert.equal(items.length, 2);
  assert.equal(items[0]?.role, "user");
  assert.equal(items[1]?.role, "actor");
  assert.equal(items[1]?.blocks.length, 3);
  assert.equal(items[1]?.blocks[0]?.type, "thinking");
  assert.equal(items[1]?.blocks[1]?.type, "tool_group");
  assert.equal(items[1]?.blocks[1]?.toolResult, "ok\n");
  assert.equal(items[1]?.blocks[2]?.type, "text");
});

test("buildDisplayItems merges live blocks into active assistant turn", () => {
  const items = buildDisplayItems({
    history: [{
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hi" }] },
      created_at: null,
    }],
    liveBlocks: [{ key: "live:text", type: "text", content: "partial" }],
    optimisticUserText: null,
    phase: "streaming",
    turnKey: "turn-1",
  });

  assert.equal(items.length, 2);
  assert.equal(items[1]?.role, "actor");
  assert.equal(items[1]?.blocks[0]?.content, "partial");
  assert.equal(items[1]?.turnKey, "turn-1");
});

test("buildDisplayItems deduplicates repeated persisted history", () => {
  const input: HistoryItem = {
    seq: 0,
    kind: "input",
    payload: { role: "user", name: "user", content: [{ kind: "text", text: "hi" }] },
    created_at: null,
  };

  const items = buildDisplayItems({
    history: [input, input],
    optimisticUserText: "hi",
    phase: "sending",
    turnKey: "turn-1",
  });

  assert.equal(items.length, 1);
  assert.equal(items[0]?.role, "user");
  assert.equal(items[0]?.blocks[0]?.content, "hi");
});

test("buildDisplayItems merges live tool block into active history turn", () => {
  const userInput: HistoryItem = {
    seq: 0,
    kind: "input",
    payload: { role: "user", name: "user", content: [{ kind: "text", text: "run python" }] },
    created_at: null,
  };
  const persistedToolCall: HistoryItem = {
    seq: 1,
    kind: "gen_tool_call",
    payload: { id: "call-1", name: "execute_python", arguments: JSON.stringify({ code: "print(1)" }) },
    created_at: null,
  };
  const liveToolBlock: RenderBlock = {
    key: "turn-1:tool:call-1:group",
    type: "tool_group",
    content: "execute_python",
    toolArgs: "",
    toolCallId: "call-1",
    toolName: "execute_python",
    toolStatus: "running",
  };

  const items = buildDisplayItems({
    history: [userInput, persistedToolCall],
    liveBlocks: [liveToolBlock],
    optimisticUserText: null,
    phase: "streaming",
    turnKey: "turn-1",
  });

  assert.equal(items.length, 2);
  assert.equal(items[1]?.role, "actor");
  assert.equal(items[1]?.blocks.length, 1);
  assert.equal(items[1]?.blocks[0]?.toolName, "execute_python");
  assert.equal(toolDisplay(items[1]!.blocks[0]!).code, "print(1)");
});

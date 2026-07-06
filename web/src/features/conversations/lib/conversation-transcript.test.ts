import assert from "node:assert/strict";
import { test } from "node:test";

import {
  appendRenderBlocks,
  buildDisplayItems,
  createTranscriptState,
  historyItemsFromHistory,
  renderBlocksFromStreamEvent,
  stripRealTimeContext,
  toolDisplay,
  transcriptDisplayItems,
  transcriptReducer,
  type RenderBlock,
} from "./conversation-transcript.ts";
import type { HistoryItem } from "../../../shared/types/api.ts";

test("stripRealTimeContext removes per-turn mode prefix from user text", () => {
  const raw = "[yuubot-real-time-context]\nmode: actor\nnow: 2026-07-06T12:00:00+08:00\n\n---\nhello";
  assert.equal(stripRealTimeContext(raw), "hello");
});

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

test("toolDisplay exposes streamed execute_python code before JSON completes", () => {
  const block: RenderBlock = {
    key: "tool",
    type: "tool_group",
    content: "execute_python",
    toolName: "execute_python",
    toolArgs: "{\"code\":\"print(1",
  };

  assert.equal(toolDisplay(block).code, "print(1");
});

test("duplicate text_delta doubles rendered content", () => {
  let blockIndex = 0;
  const next = () => blockIndex++;
  const delta = renderBlocksFromStreamEvent(
    {
      group_id: "text-0",
      kind: "text_delta",
      payload: { text: "The" },
    },
    "item",
    next,
  );

  const once = appendRenderBlocks([], delta);
  const twice = appendRenderBlocks(once, delta);

  assert.equal(once[0]?.content, "The");
  assert.equal(twice[0]?.content, "TheThe");
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

test("stream tool arguments keep merging after tool result arrives", () => {
  let blockIndex = 0;
  const next = () => blockIndex++;
  const nameBlock = renderBlocksFromStreamEvent(
    {
      group_id: "tool-0",
      kind: "tool_name",
      payload: { id: "call-1", name: "execute_python" },
    },
    "item",
    next,
  );
  const partialArgs = renderBlocksFromStreamEvent(
    {
      group_id: "tool-0",
      kind: "tool_arguments_delta",
      payload: { text: "{\"code\":\"import foo" },
    },
    "item",
    next,
  );
  const resultBlock: RenderBlock = {
    key: "item:tool-result:call-1",
    type: "tool_result",
    content: "ok\n",
    toolCallId: "call-1",
    toolStatus: "completed",
  };
  const finalArgs = renderBlocksFromStreamEvent(
    {
      group_id: "tool-0",
      kind: "tool_arguments_delta",
      payload: { text: "\nprint(1)\"}" },
    },
    "item",
    next,
  );

  const blocks = appendRenderBlocks([], [...nameBlock, ...partialArgs, resultBlock, ...finalArgs]);

  assert.equal(blocks.length, 1);
  assert.equal(toolDisplay(blocks[0]!).code, "import foo\nprint(1)");
  assert.equal(blocks[0]?.toolResult, "ok\n");
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
    phase: "streaming",
    turnKey: "turn-1",
  });

  assert.equal(items.length, 2);
  assert.equal(items[1]?.role, "actor");
  assert.equal(items[1]?.key, "turn:0:actor");
  assert.equal(items[1]?.blocks[0]?.content, "partial");
  assert.equal(items[1]?.turnKey, "turn:0");
});

test("buildDisplayItems renders durable assistant history once", () => {
  const history: HistoryItem[] = [
    {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hi" }] },
      created_at: "2026-07-05T15:46:43.809410+00:00",
    },
    {
      seq: 1,
      kind: "gen_reasoning",
      payload: { text: "The user is just saying hi." },
      created_at: "2026-07-05T15:46:45.953612+00:00",
    },
    {
      seq: 2,
      kind: "gen_text",
      payload: { text: "Hey there!" },
      created_at: "2026-07-05T15:46:45.953612+00:00",
    },
  ];

  const items = buildDisplayItems({
    history,
    phase: "idle",
  });

  assert.equal(items.length, 2);
  assert.equal(items[0]?.role, "user");
  assert.equal(items[1]?.role, "actor");
  assert.equal(items[1]?.blocks.some((block) => block.type === "thinking"), true);
  assert.equal(items[1]?.blocks.some((block) => block.content === "Hey there!"), true);
});

test("history append sequence interleaves user and actor turns", () => {
  const history: HistoryItem[] = [
    {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "first" }] },
      created_at: null,
    },
    {
      seq: 1,
      kind: "gen_text",
      payload: { text: "reply one" },
      created_at: null,
    },
    {
      seq: 2,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "second" }] },
      created_at: null,
    },
    {
      seq: 3,
      kind: "gen_text",
      payload: { text: "reply two" },
      created_at: null,
    },
  ];

  const items = buildDisplayItems({
    history,
    phase: "idle",
  });

  assert.equal(items.length, 4);
  assert.deepEqual(items.map((item) => item.role), ["user", "actor", "user", "actor"]);
  assert.equal(items[1]?.blocks[0]?.content, "reply one");
  assert.equal(items[3]?.blocks[0]?.content, "reply two");
});

test("transcript reducer renders durable append-only history from an empty conversation", () => {
  let state = createTranscriptState();
  const history: HistoryItem[] = [
    {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hi" }] },
      created_at: null,
    }, {
      seq: 1,
      kind: "gen_text",
      payload: { text: "hello back" },
      created_at: null,
    },
  ];
  for (const item of history) {
    state = transcriptReducer(state, { type: "history_append", item });
  }

  const items = transcriptDisplayItems(state);

  assert.equal(items.length, 2);
  assert.equal(items[1]?.role, "actor");
  assert.equal(items[1]?.blocks[0]?.content, "hello back");
});

test("buildDisplayItems does not create a local user message when history already persisted it", () => {
  // Regression guard: optimistic user bubbles caused repeated first messages
  // when persisted history and live stream rendering overlapped. User messages
  // must come from history only.
  const input: HistoryItem = {
    seq: 0,
    kind: "input",
    payload: { role: "user", name: "user", content: [{ kind: "text", text: "hi" }] },
    created_at: null,
  };

  const items = buildDisplayItems({
    history: [input],
    phase: "streaming",
    turnKey: "turn-1",
  });

  assert.equal(items.length, 1);
  assert.equal(items[0]?.role, "user");
  assert.equal(items[0]?.blocks[0]?.content, "hi");
});

test("buildDisplayItems does not create a local user message while sending", () => {
  const prevUser: HistoryItem = {
    seq: 0,
    kind: "input",
    payload: { role: "user", name: "user", content: [{ kind: "text", text: "first" }] },
    created_at: null,
  };
  const prevActor: HistoryItem = {
    seq: 1,
    kind: "gen_text",
    payload: { text: "response" },
    created_at: null,
  };

  const items = buildDisplayItems({
    history: [prevUser, prevActor],
    phase: "sending",
    turnKey: "turn-2",
  });

  assert.equal(items.length, 2);
  assert.equal(items[0]?.role, "user");
  assert.equal(items[1]?.role, "actor");
});

test("buildDisplayItems preserves first actor turn while streaming second reply", () => {
  const history: HistoryItem[] = [
    {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hello" }] },
      created_at: "2026-07-05T14:06:09.574014+00:00",
    },
    {
      seq: 1,
      kind: "gen_text",
      payload: { text: "Hi there!" },
      created_at: "2026-07-05T14:06:10.574014+00:00",
    },
    {
      seq: 2,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "roll dice" }] },
      created_at: "2026-07-05T15:46:43.809410+00:00",
    },
  ];

  const items = buildDisplayItems({
    history,
    liveBlocks: [{ key: "live:thinking", type: "thinking", content: "The user is asking" }],
    phase: "streaming",
    turnKey: "turn-2",
  });

  assert.equal(items.length, 4);
  assert.equal(items[0]?.role, "user");
  assert.equal(items[1]?.role, "actor");
  assert.equal(items[1]?.blocks[0]?.content, "Hi there!");
  assert.equal(items[2]?.role, "user");
  assert.equal(items[3]?.key, "turn:2:actor");
  assert.equal(items[3]?.blocks[0]?.content, "The user is asking");
});

test("buildDisplayItems prefers persisted partial history over overlapping live stream", () => {
  const history: HistoryItem[] = [
    {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hello" }] },
      created_at: null,
    },
    {
      seq: 1,
      kind: "gen_text",
      payload: { text: "Hi there!" },
      created_at: null,
    },
    {
      seq: 2,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "roll dice" }] },
      created_at: null,
    },
    {
      seq: 3,
      kind: "gen_reasoning",
      payload: { text: "The user is" },
      created_at: null,
    },
  ];

  const items = buildDisplayItems({
    history,
    liveBlocks: [{ key: "live:thinking", type: "thinking", content: "The user is asking" }],
    phase: "streaming",
    turnKey: "turn-2",
  });

  assert.equal(items.length, 4);
  assert.equal(items[3]?.blocks[0]?.content, "The user is");
  assert.equal(items[3]?.blocks[0]?.content.includes("TheThe"), false);
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
    phase: "idle",
    turnKey: "turn-1",
  });

  assert.equal(items.length, 1);
  assert.equal(items[0]?.role, "user");
  assert.equal(items[0]?.blocks[0]?.content, "hi");
});

test("buildDisplayItems attaches durable assistant turn after user message in existing conversation", () => {
  const history: HistoryItem[] = [
    {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hello" }] },
      created_at: "2026-07-05T14:06:09.574014+00:00",
    },
    {
      seq: 1,
      kind: "gen_text",
      payload: { text: "Hi there!" },
      created_at: "2026-07-05T14:06:10.574014+00:00",
    },
    {
      seq: 2,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "roll dice" }] },
      created_at: "2026-07-05T15:46:43.809410+00:00",
    },
    {
      seq: 3,
      kind: "gen_text",
      payload: { text: "You rolled a 4." },
      created_at: "2026-07-05T15:46:44.809410+00:00",
    },
  ];

  const items = buildDisplayItems({
    history,
    phase: "idle",
  });

  assert.equal(items.length, 4);
  assert.equal(items[0]?.role, "user");
  assert.equal(items[1]?.role, "actor");
  assert.equal(items[1]?.blocks[0]?.content, "Hi there!");
  assert.equal(items[2]?.role, "user");
  assert.equal(items[3]?.role, "actor");
  assert.equal(items[3]?.blocks[0]?.content, "You rolled a 4.");
});

test("buildDisplayItems keeps durable partial history instead of replacing it with live buffer", () => {
  const history: HistoryItem[] = [
    {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hello" }] },
      created_at: null,
    },
    {
      seq: 1,
      kind: "gen_text",
      payload: { text: "Hi there!" },
      created_at: null,
    },
    {
      seq: 2,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "run python" }] },
      created_at: null,
    },
    {
      seq: 3,
      kind: "gen_tool_call",
      payload: { id: "call-1", name: "execute_python", arguments: JSON.stringify({ code: "print(1)" }) },
      created_at: null,
    },
  ];
  const liveToolBlock: RenderBlock = {
    key: "turn-2:tool:call-1:group",
    type: "tool_group",
    content: "execute_python",
    toolArgs: JSON.stringify({ code: "print(1)" }),
    toolCallId: "call-1",
    toolName: "execute_python",
    toolStatus: "running",
  };

  const items = buildDisplayItems({
    history,
    liveBlocks: [liveToolBlock],
    phase: "streaming",
    turnKey: "turn-2",
  });

  assert.equal(items.length, 4);
  assert.equal(items[1]?.blocks[0]?.content, "Hi there!");
  assert.equal(items[3]?.role, "actor");
  assert.equal(items[3]?.blocks.length, 1);
  assert.equal(items[3]?.blocks[0]?.toolName, "execute_python");
  assert.equal(toolDisplay(items[3]!.blocks[0]!).code, "print(1)");
  assert.equal(items[3]?.blocks[0]?.toolStatus, "completed");
});

test("transcript reducer replaces live preview with durable history without changing turn key", () => {
  let state = createTranscriptState();
  state = transcriptReducer(state, { type: "begin_turn", turnKey: "turn-live", now: 1 });
  state = transcriptReducer(state, {
    type: "history_append",
    item: {
      seq: 0,
      kind: "input",
      payload: { role: "user", name: "user", content: [{ kind: "text", text: "hi" }] },
      created_at: null,
    },
  });
  state = transcriptReducer(state, {
    type: "append_blocks",
    blocks: [{ key: "live:text", type: "text", content: "partial" }],
  });
  const liveItems = transcriptDisplayItems(state);
  assert.equal(liveItems[1]?.key, "turn:0:actor");
  assert.equal(liveItems[1]?.blocks[0]?.content, "partial");

  state = transcriptReducer(state, {
    type: "history_append",
    item: {
      seq: 1,
      kind: "gen_text",
      payload: { text: "persisted" },
      created_at: null,
    },
  });

  const durableItems = transcriptDisplayItems(state);
  assert.equal(durableItems[1]?.key, "turn:0:actor");
  assert.equal(durableItems[1]?.blocks[0]?.content, "persisted");
});

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  appendRenderBlocks,
  historyItemsFromMessages,
  markToolBlocksCompleted,
  rememberConversationSseEvent,
  renderBlocksFromEvent,
  toolDisplay,
  type RenderBlock,
} from "./conversation-transcript.ts";
import { extractToolPath as extractToolPathFromArgs } from "./tool-renderers.ts";
import type { ConversationSSEEvent } from "../types/api.ts";

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

test("live tool call deltas render the bash command after grouping", () => {
  let blockIndex = 0;
  const blocks = renderBlocksFromEvent(
    {
      conversation_id: "conversation",
      event_id: "event-1",
      event_type: "transcript_delta",
      sequence: 1,
      timestamp: 1,
      turn_id: "turn-1",
      deltas: [{
        type: "tool_call",
        tool_call_id: "tool-1",
        tool_name: "bash",
        arguments_text_delta: JSON.stringify({ command: "ls -la" }),
      }],
    },
    "item",
    () => blockIndex++,
  );

  const [group] = appendRenderBlocks([], blocks);
  assert.equal(group?.type, "tool_group");
  assert.equal(toolDisplay(group).argsText, JSON.stringify({ command: "ls -la" }, null, 2));
});

test("persisted tool messages render the same grouped bash shape as live deltas", () => {
  const items = historyItemsFromMessages([
    {
      id: 1,
      conversation_id: "conversation",
      message_id: "assistant-1",
      role: "assistant",
      raw_content: JSON.stringify([{
        type: "tool_call",
        id: "tool-1",
        name: "bash",
        arguments: JSON.stringify({ command: "ls -la" }),
      }]),
      metadata: {},
      timestamp: 1,
    },
    {
      id: 2,
      conversation_id: "conversation",
      message_id: "tool-1",
      role: "tool",
      raw_content: JSON.stringify([{
        type: "tool_result",
        tool_call_id: "tool-1",
        content: "ok\n",
      }]),
      metadata: {},
      timestamp: 2,
    },
  ]);

  assert.equal(items.length, 1);
  const [group] = items[0].blocks;
  assert.equal(group?.type, "tool_group");
  assert.equal(group?.toolName, "bash");
  assert.equal(group?.toolResult, "ok\n");
  assert.equal(toolDisplay(group).argsText, JSON.stringify({ command: "ls -la" }, null, 2));
});

test("duplicate live tool call events are ignored before rendering", () => {
  const seen = new Set<string>();
  const event: ConversationSSEEvent = {
    conversation_id: "conversation",
    event_id: "event-1",
    event_type: "transcript_delta",
    sequence: 1,
    timestamp: 1,
    turn_id: "turn-1",
    deltas: [{
      type: "tool_call",
      tool_call_id: "tool-1",
      tool_name: "bash",
      arguments_text_delta: JSON.stringify({ command: "ls -la" }),
    }],
  };
  let blockIndex = 0;
  const rendered = [
    event,
    { ...event, event_id: "event-duplicate" },
  ].flatMap((item) => (
    rememberConversationSseEvent(seen, item)
      ? renderBlocksFromEvent(item, "item", () => blockIndex++)
      : []
  ));

  assert.equal(rendered.length, 1);
});

test("rememberConversationSseEvent rejects duplicate event ids or sequences", () => {
  const seen = new Set<string>();
  const event = {
    conversation_id: "conversation",
    event_id: "event-1",
    event_type: "transcript_delta",
    sequence: 10,
    timestamp: 1,
  };

  assert.equal(rememberConversationSseEvent(seen, event), true);
  assert.equal(rememberConversationSseEvent(seen, { ...event, sequence: 11 }), false);
  assert.equal(rememberConversationSseEvent(seen, { ...event, event_id: "event-2" }), false);
});

test("markToolBlocksCompleted completes live tool groups with results", () => {
  const blocks: RenderBlock[] = [
    {
      key: "tool",
      type: "tool_group",
      content: "bash",
      toolResult: "done",
      toolStatus: "running",
    },
    {
      key: "pending-tool",
      type: "tool_group",
      content: "bash",
      toolStatus: "running",
    },
  ];

  assert.deepEqual(markToolBlocksCompleted(blocks).map((block) => block.toolStatus), [
    "completed",
    "running",
  ]);
});

test("extractToolPath reads direct and wrapped path tool args", () => {
  assert.equal(
    extractToolPathFromArgs(JSON.stringify({ path: "src/main.py" })),
    "src/main.py",
  );
  assert.equal(
    extractToolPathFromArgs(JSON.stringify({ arguments: { path: "README.md" } })),
    "README.md",
  );
});

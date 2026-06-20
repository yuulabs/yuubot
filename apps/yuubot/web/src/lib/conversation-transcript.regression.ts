import {
  appendRenderBlocks,
  historyItemsFromMessages,
  renderBlocksFromEvent,
  type RenderBlock,
} from "@/lib/conversation-transcript";
import type { ConversationMessage, ConversationSSEEvent } from "@/types/api";

function assert(condition: boolean, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function streamedToolResultEvent(): ConversationSSEEvent {
  return {
    conversation_id: "conversation-test",
    agent_id: "agent-test",
    agent_name: "Test Agent",
    event_type: "tool_result",
    content: {
      tool_call_id: "call-1",
      tool_name: "execute_python",
      status: "completed",
      blocks: [{
        type: "tool_result",
        tool_call_id: "call-1",
        tool_name: "execute_python",
        content: "200\nKeyError: slice(None, 3, None)",
        status: "completed",
      }],
    },
    timestamp: 1,
  };
}

function streamedTextShapedToolResultEvent(): ConversationSSEEvent {
  return {
    conversation_id: "conversation-test",
    agent_id: "agent-test",
    agent_name: "Test Agent",
    event_type: "tool_result",
    content: {
      tool_call_id: "call-2",
      tool_name: "execute_python",
      status: "completed",
      blocks: [{
        type: "text",
        text: "stdout line\nTraceback details",
      }],
    },
    timestamp: 2,
  };
}

function persistedToolResultMessage(): ConversationMessage {
  return {
    id: 1,
    message_id: "tool-1",
    conversation_id: "conversation-test",
    role: "tool",
    raw_content: JSON.stringify([{
      type: "tool_result",
      tool_call_id: "call-1",
      tool_name: "execute_python",
      content: "200\nKeyError: slice(None, 3, None)",
      status: "completed",
    }]),
    metadata: {},
    timestamp: 1,
  };
}

export function verifyConversationTranscriptRegressions(): void {
  let index = 0;
  const streamedBlocks = renderBlocksFromEvent(
    streamedToolResultEvent(),
    "live:test",
    () => index++,
  );
  assert(streamedBlocks.length === 1, "streamed tool result should produce one render block");
  assert(streamedBlocks[0].type === "tool_result", "streamed tool result should keep tool_result type");

  const appended = appendRenderBlocks([], streamedBlocks);
  assert(appended.length === 1, "orphan streamed tool result should not be dropped");
  assert(appended[0].type === "tool_result", "orphan streamed tool result should render as a tool result");
  assert(appended[0].content.includes("KeyError"), "tool result should preserve exception/stdout content");

  const textShapedBlocks = renderBlocksFromEvent(
    streamedTextShapedToolResultEvent(),
    "live:text-shaped",
    () => index++,
  );
  assert(textShapedBlocks.length === 1, "text-shaped tool result should produce one render block");
  assert(textShapedBlocks[0].type === "tool_result", "tool_result event should override inner text block type");
  assert(textShapedBlocks[0].toolName === "execute_python", "tool_result event metadata should preserve tool name");

  const deduped = appendRenderBlocks(textShapedBlocks, textShapedBlocks);
  assert(deduped.length === 1, "duplicate streamed tool result content should render once");
  assert(deduped[0].content === textShapedBlocks[0].content, "duplicate streamed tool result content should not be appended twice");

  const history = historyItemsFromMessages([persistedToolResultMessage()]);
  assert(history.length === 1, "persisted tool result should produce one history item");
  const historyBlock = history[0].blocks[0] as RenderBlock | undefined;
  assert(historyBlock !== undefined, "persisted tool result should produce one render block");
  assert(historyBlock.type === "tool_result", "persisted tool result should keep tool_result type");
  assert(historyBlock.content.includes("KeyError"), "persisted tool result should preserve content");
}

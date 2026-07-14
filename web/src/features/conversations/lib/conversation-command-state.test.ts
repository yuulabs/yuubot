import assert from "node:assert/strict";
import test from "node:test";

import {
  conversationCommandReducer,
  createConversationCommandState,
} from "./conversation-command-state";

const payload = {
  actorId: "amy",
  conversationId: "c1",
  content: [{ kind: "text", text: "retry me" }],
};

test("only the matching send command can accept or reject pending input", () => {
  let state = conversationCommandReducer(createConversationCommandState(), {
    type: "send_local",
    commandId: "send-1",
    baselineSeq: 4,
    payload,
  });

  state = conversationCommandReducer(state, { type: "send_accepted", commandId: "open-1" });
  assert.equal(state.pending?.stage, "local_pending");

  state = conversationCommandReducer(state, { type: "send_accepted", commandId: "send-1" });
  assert.equal(state.pending?.stage, "accepted");

  state = conversationCommandReducer(state, { type: "send_rejected", commandId: "answer-1" });
  assert.equal(state.pending?.stage, "accepted");

  state = conversationCommandReducer(state, { type: "send_rejected", commandId: "send-1" });
  assert.equal(state.pending, null);
  assert.deepEqual(state.retry, payload);
});

test("durable input commit clears pending and retry state", () => {
  let state = conversationCommandReducer(createConversationCommandState(), {
    type: "send_local",
    commandId: "send-1",
    baselineSeq: 4,
    payload,
  });
  state = conversationCommandReducer(state, { type: "user_input_committed" });
  assert.equal(state.pending, null);
  assert.equal(state.retry, null);
});

test("interrupt result stays interrupting until terminal commit", () => {
  let state = conversationCommandReducer(createConversationCommandState(), {
    type: "interrupt_requested",
    commandId: "interrupt-1",
  });
  state = conversationCommandReducer(state, {
    type: "interrupt_result",
    commandId: "interrupt-1",
    accepted: true,
  });
  assert.equal(state.interrupting, true);

  state = conversationCommandReducer(state, {
    type: "interrupt_result",
    commandId: "interrupt-other",
    accepted: false,
  });
  assert.equal(state.interrupting, true);

  state = conversationCommandReducer(state, { type: "terminal_commit" });
  assert.equal(state.interrupting, false);
});

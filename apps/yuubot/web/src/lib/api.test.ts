import assert from "node:assert/strict";
import { test } from "node:test";

import * as api from "./api.ts";

/** Captured fetch invocation: URL string + RequestInit body (string | undefined). */
interface CapturedCall {
  url: string;
  body: string | undefined;
}

/**
 * Install a fetch stub that records calls and responds with 202 + the
 * provided JSON payload. Returns the captured calls array and a restore fn.
 *
 * The path-style `request<T>` helper in api.ts reads `response.ok` and
 * `response.json()`, so the stub only needs to implement those two members.
 */
function installFetchStub(responsePayload: unknown): {
  calls: CapturedCall[];
  restore: () => void;
} {
  const calls: CapturedCall[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = ((input: unknown, init?: RequestInit) => {
    const url = typeof input === "string" ? input : String(input);
    calls.push({ url, body: init?.body?.toString() });
    return Promise.resolve(
      new Response(JSON.stringify(responsePayload), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }) as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = original; } };
}

test("sendConversationMessage sends actor_id on first send (draft mode)", async () => {
  const { calls, restore } = installFetchStub({
    status: "accepted",
    data: { conversation_id: "c1", message_id: "m1" },
  });
  try {
    await api.sendConversationMessage({
      conversationId: "c1",
      text: "hi",
      actorId: "actor-1",
      messageId: "u1",
    });
    assert.equal(calls.length, 1);
    const parsed = JSON.parse(calls[0].body ?? "null") as Record<string, unknown>;
    assert.equal(parsed.actor_id, "actor-1");
    assert.equal(parsed.text, "hi");
    assert.equal(parsed.message_id, "u1");
  } finally {
    restore();
  }
});

test("sendConversationMessage omits actor_id on subsequent sends", async () => {
  const { calls, restore } = installFetchStub({
    status: "accepted",
    data: { conversation_id: "c1", message_id: "m2" },
  });
  try {
    await api.sendConversationMessage({
      conversationId: "c1",
      text: "yo",
      messageId: "u2",
    });
    assert.equal(calls.length, 1);
    const parsed = JSON.parse(calls[0].body ?? "null") as Record<string, unknown>;
    assert.equal(parsed.text, "yo");
    assert.equal(parsed.message_id, "u2");
    assert.equal(
      Object.prototype.hasOwnProperty.call(parsed, "actor_id"),
      false,
      "subsequent send must not include actor_id",
    );
  } finally {
    restore();
  }
});

test("createConversation and ensureConversationAgent are not exported", () => {
  assert.equal(
    (api as Record<string, unknown>).createConversation,
    undefined,
    "createConversation must be removed from api.ts exports",
  );
  assert.equal(
    (api as Record<string, unknown>).ensureConversationAgent,
    undefined,
    "ensureConversationAgent must be removed from api.ts exports",
  );
});

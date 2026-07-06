import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldProcessCommandFrame } from "./ws-frame.ts";

test("shouldProcessCommandFrame accepts matching command id", () => {
  assert.equal(shouldProcessCommandFrame("send-1", "send-1"), true);
});

test("shouldProcessCommandFrame rejects stale command id", () => {
  assert.equal(shouldProcessCommandFrame("send-1", "send-2"), false);
});

test("shouldProcessCommandFrame accepts frames without id", () => {
  assert.equal(shouldProcessCommandFrame(undefined, "send-1"), true);
});

test("shouldProcessCommandFrame accepts frames when no active command", () => {
  assert.equal(shouldProcessCommandFrame("send-1", null), true);
});

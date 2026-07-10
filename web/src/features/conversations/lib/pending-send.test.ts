import assert from "node:assert/strict";
import { test } from "node:test";

import { parsePendingSend } from "./pending-send.ts";

test("parsePendingSend parses valid router state", () => {
  const parsed = parsePendingSend({
    pendingSend: {
      actorId: "amy",
      content: [{ kind: "text", text: "hi" }],
    },
  });
  assert.deepEqual(parsed, {
    actorId: "amy",
    content: [{ kind: "text", text: "hi" }],
  });
});

test("parsePendingSend rejects invalid state", () => {
  assert.equal(parsePendingSend(null), null);
  assert.equal(parsePendingSend({ pendingSend: { actorId: "", content: [] } }), null);
});

// sse-long-lived-markers.test.ts
//
// Regression guard for the "every second message hangs" bug caused by the
// prior close-on-turn_completed SSE design. The stream must stay open
// across turns; turn completion is a named event the frontend listens for.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const routeSrc = readFileSync(
  path.join(here, "admin.conversations.$conversationId.tsx"),
  "utf8",
);
const typesSrc = readFileSync(
  path.join(here, "..", "types", "api.ts"),
  "utf8",
);

test("frontend SSE contract declares turn_completed as a named event type", () => {
  assert.ok(
    typesSrc.includes("event_type: \"turn_completed\""),
    "ConversationSSEEvent union must include TurnCompletedEvent",
  );
});

test("conversation route registers a turn_completed EventSource listener", () => {
  assert.ok(
    routeSrc.includes('addEventListener("turn_completed"'),
    "route must subscribe to turn_completed so the named event — and not a transport close — ends the turn",
  );
});

test("conversation route does not close the EventSource on turn completion", () => {
  // The prior regression closed the EventSource in onerror to signal
  // turn completion (because the daemon closed the stream). The new
  // design keeps the stream long-lived; onerror must NOT call es.close()
  // on the happy path.
  const onerrorStart = routeSrc.indexOf("es.onerror = () => {");
  assert.ok(onerrorStart >= 0, "es.onerror handler not found");
  const onerrorEnd = routeSrc.indexOf("};", onerrorStart);
  assert.ok(onerrorEnd > onerrorStart, "es.onerror handler end not found");
  const onerrorSlice = routeSrc.slice(onerrorStart, onerrorEnd);
  assert.ok(
    !onerrorSlice.includes("es.close()"),
    "es.onerror must not es.close() — EventSource needs to auto-reconnect on idle disconnects",
  );
});

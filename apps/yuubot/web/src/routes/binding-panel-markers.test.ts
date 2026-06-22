// binding-panel-markers.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const routeSrc = readFileSync(
  path.join(here, "admin.conversations.$conversationId.tsx"), "utf8");

test("conversation route hosts the binding panel markers", () => {
  // TODO(B-phase) is intentionally not asserted here — the B-phase landed test
  // below asserts its absence and the real Open Workspace link's presence.
  assert.ok(routeSrc.includes("TODO(TODO-B)"), "missing TODO-B reserved section");
  assert.ok(routeSrc.includes("TODO(TODO-C)"), "missing TODO-C reserved section");
  assert.ok(routeSrc.includes("TODO(TODO-D)"), "missing TODO-D reserved section");
});

test("conversation route exposes a real Open Workspace link (B-phase landed)", () => {
  // The link source must reference the actor's capability_set.workspace_path
  // field — the daemon already eagerly loads capability_set on every actor
  // returned from /api/resources/actors, so this works on /new (draft, before
  // first send) as well as on existing conversations.
  assert.ok(
    routeSrc.includes("capability_set?.workspace_path") ||
    routeSrc.includes("capability_set.workspace_path"),
    "Open Workspace link must read actor.capability_set.workspace_path",
  );
  // The conversation metadata must no longer be the source of the workspace link.
  assert.ok(
    !routeSrc.includes("conversationMetadata.workspace_path"),
    "should not read workspace_path from conversationMetadata anymore",
  );
  assert.ok(
    !routeSrc.includes("/workspace/${actorId}"),
    "wrong: route still references actorId for workspace link",
  );
  assert.ok(!routeSrc.includes("TODO(B-phase)"), "B-phase placeholder still present");
});

test("conversation route no longer renders the Actor Select inside <header>", () => {
  // The header now carries only the back button + title; Actor Select is in BindingPanel.
  // Heuristic: there is exactly one <Select ...> in the file and it is not inside <header>.
  const selectCount = (routeSrc.match(/<Select[\s>]/g) || []).length;
  assert.equal(selectCount, 1, "expected exactly one Select in the route");
  // Split the source by <header ... </header> and assert the Select is NOT in the header slice.
  const headerStart = routeSrc.indexOf("<header");
  const headerEnd = routeSrc.indexOf("</header>");
  assert.ok(headerStart >= 0 && headerEnd > headerStart, "header not found");
  const headerSlice = routeSrc.slice(headerStart, headerEnd);
  assert.ok(!/<Select[\s>]/.test(headerSlice), "Actor Select still in header");
});

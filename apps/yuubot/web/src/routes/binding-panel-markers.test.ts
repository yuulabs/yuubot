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
  assert.ok(routeSrc.includes("TODO(B-phase)"), "missing B-phase Open Workspace placeholder");
  assert.ok(routeSrc.includes("TODO(TODO-B)"), "missing TODO-B reserved section");
  assert.ok(routeSrc.includes("TODO(TODO-C)"), "missing TODO-C reserved section");
  assert.ok(routeSrc.includes("TODO(TODO-D)"), "missing TODO-D reserved section");
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

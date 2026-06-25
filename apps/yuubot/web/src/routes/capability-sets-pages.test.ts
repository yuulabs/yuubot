// capability-sets-pages.test.ts
// Source-text contract test for ISSUE-0007 S5 — Capability Sets pages
// (browse rewrite + /new + /$id/edit editor with CapTree).
//
// Per the instruction's Test Boundary: no render tests, no CapTree internal
// state assertions. We only read the three route files and assert the demo-
// aligned structural markers are present (and the legacy create-form card
// title is gone).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));

function src(name: string): string {
  return readFileSync(path.join(here, name), "utf8");
}

const browseSrc = src("capability-sets.tsx");
const newSrc = src("capability-sets.new.tsx");
const editSrc = src("capability-sets.$id.edit.tsx");

test("browse view renders CrudHeader + Empty + DataTable (no right-side create form)", () => {
  assert.ok(browseSrc.includes("<CrudHeader"), "browse must use <CrudHeader>");
  assert.ok(browseSrc.includes("<Empty"), "browse must use <Empty>");
  assert.ok(browseSrc.includes("<DataTable"), "browse must use <DataTable>");
});

test("browse view drops the legacy right-side Create Capability Set card title", () => {
  assert.ok(
    !browseSrc.includes("Create Capability Set"),
    "browse must no longer host its own create form card title",
  );
});

test("/capability-sets/new editor exists and is a form with CapTree + create mutation", () => {
  assert.ok(newSrc.includes("<form"), "new editor must be a <form>");
  assert.ok(newSrc.includes("<CapTree"), "new editor must render <CapTree>");
  assert.ok(
    newSrc.includes('useCreateResource("capability-sets")'),
    "new editor must call useCreateResource(\"capability-sets\")",
  );
});

test("/capability-sets/$id/edit editor exists and wires CapTree + update mutation", () => {
  assert.ok(editSrc.includes("<CapTree"), "edit editor must render <CapTree>");
  assert.ok(
    editSrc.includes("useUpdateResource"),
    "edit editor must call useUpdateResource",
  );
});

test("CapTree groups are assembled from live capabilities", () => {
  const combined = `${newSrc}\n${editSrc}`;
  assert.ok(
    combined.includes("integration_capability_ids"),
    "editor must drive integration_capability_ids from selection",
  );
  assert.ok(
    combined.includes("useLiveCapabilities") || combined.includes("liveCapabilities"),
    "editor must source capabilities from useLiveCapabilities()",
  );
});

test("capabilities are grouped by integration source", () => {
  const combined = `${newSrc}\n${editSrc}`;
  assert.ok(
    combined.includes("groupBy") ||
    combined.includes(".integration_name") ||
    combined.includes("sourceName"),
    "editor must group capabilities by integration source",
  );
});

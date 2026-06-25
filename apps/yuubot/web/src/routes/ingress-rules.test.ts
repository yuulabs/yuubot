// ingress-rules.test.ts
//
// Source-marker test for ISSUE-0007 S4: rebuilding /routes (Ingress Rules page)
// to the demo `view--ingress` layout. Mirrors the __root.test.ts /
// conversation-entry-via-actor.test.ts pattern (node:test + readFileSync):
// asserts source-text contracts on routes.tsx — no component rendering.
//
// Contract (per s4-routes-instructions.md):
//   - IngressFlow flow diagram at top.
//   - CrudHeader + table (DataTable or `<table class="data-table`) for rules.
//   - Inline draft row (inserts an editable row in-table, not a side form).
//   - Three-part Empty empty state.
//   - The old right-side fixed create-form (`<CardTitle>New Rule</CardTitle>`)
//     and the IngressRuleTutorial card are removed.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, "routes.tsx"), "utf8");

test("routes.tsx renders the IngressFlow diagram", () => {
  assert.ok(src.includes("<IngressFlow"), "routes.tsx must render <IngressFlow>");
});

test("routes.tsx renders CrudHeader + Empty + data table", () => {
  assert.ok(src.includes("<CrudHeader"), "routes.tsx must render <CrudHeader>");
  assert.ok(src.includes("<Empty"), "routes.tsx must render <Empty>");
  // DataTable component or a `<table class="data-table">` element (JSX uses className).
  const hasDataTableComponent = src.includes("<DataTable");
  const hasDataTableElement = /<table[^>]*data-table/.test(src);
  assert.ok(
    hasDataTableComponent || hasDataTableElement,
    "routes.tsx must render a DataTable or a <table class=\"data-table\">",
  );
});

test("routes.tsx implements the inline draft row", () => {
  assert.ok(
    /(draft|ingressDraftRow|openDraft|saveDraft)/.test(src),
    "routes.tsx must name draft / openDraft / saveDraft / ingressDraftRow for the inline draft row",
  );
});

test("routes.tsx drops the standalone right-side create form card", () => {
  assert.ok(
    !src.includes("<CardTitle>New Rule</CardTitle>"),
    "routes.tsx must not retain the standalone `<CardTitle>New Rule</CardTitle>` form card",
  );
});

test("routes.tsx reuses the ingress-rules data layer (create + delete)", () => {
  assert.ok(
    src.includes('useCreateResource("ingress-rules")'),
    'routes.tsx must keep useCreateResource("ingress-rules")',
  );
  assert.ok(
    src.includes("useDeleteResource"),
    "routes.tsx must keep useDeleteResource for ingress rules",
  );
});

test("routes.tsx ships a three-part Empty empty state", () => {
  const hasRouteIllustration = /illustration\s*=\s*"Route"/.test(src);
  const hasRouteCopy = src.includes("还没有 Ingress 规则");
  assert.ok(
    hasRouteIllustration || hasRouteCopy,
    'Empty must carry illustration="Route" or "还没有 Ingress 规则" copy',
  );
});

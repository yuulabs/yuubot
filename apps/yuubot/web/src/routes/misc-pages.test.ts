// misc-pages.test.ts
//
// Source-text contract test for ISSUE-0007 S6 — baseline-styled remaining
// pages (providers / integrations / monitor / settings / index dashboard) +
// admin.conversations redirect preservation. Mirrors the __root.test.ts /
// binding-panel-markers.test.ts pattern: node:test + readFileSync, asserts
// source-text contracts — no component rendering (no jsdom).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (name: string) => readFileSync(path.join(here, name), "utf8");

const providersSrc = read("providers.tsx");
const integrationsSrc = read("integrations.tsx");
const monitorSrc = read("monitor.tsx");
const settingsSrc = read("settings.tsx");
const indexSrc = read("index.tsx");
const adminConversationsSrc = read("admin.conversations.tsx");

// ---------------------------------------------------------------------------
// /providers — demo providers view (presets + inline API key form + connected)
// ---------------------------------------------------------------------------

test("providers.tsx wraps in baseline <PageShell>", () => {
  assert.ok(
    providersSrc.includes("<PageShell"),
    "providers.tsx must wrap its view in the baseline <PageShell>",
  );
});

test("providers.tsx references the provider presets model", () => {
  assert.ok(
    providersSrc.includes("providerPresets") || providersSrc.includes("Presets"),
    "providers.tsx must reference providerPresets (the preset list source)",
  );
});

test("providers.tsx renders the connected-backends list header", () => {
  assert.ok(
    providersSrc.includes("<CrudHeader") || providersSrc.includes("connected"),
    "providers.tsx must render a CrudHeader / connected section for backends",
  );
});

test("providers.tsx wires llm-backends create + delete mutations", () => {
  assert.ok(
    providersSrc.includes('useCreateResource') &&
      providersSrc.includes('"llm-backends"'),
    "providers.tsx must useCreateResource(\"llm-backends\")",
  );
  assert.ok(
    providersSrc.includes("useDeleteResource"),
    "providers.tsx must useDeleteResource for backend deletion",
  );
});

test("providers.tsx carries an inline API key form (Field/LegendCard + api key)", () => {
  const hasFormField =
    providersSrc.includes("Field") || providersSrc.includes("<LegendCard");
  assert.ok(hasFormField, "providers.tsx must use Field or LegendCard for the form");
  assert.ok(
    providersSrc.includes("apiKey") || providersSrc.includes("api_key"),
    "providers.tsx must reference the api key field",
  );
});

// ---------------------------------------------------------------------------
// /integrations — card grid wrapped in LegendCard style
// ---------------------------------------------------------------------------

test("integrations.tsx adopts baseline styling (PageShell or LegendCard)", () => {
  assert.ok(
    integrationsSrc.includes("<PageShell") || integrationsSrc.includes("<LegendCard"),
    "integrations.tsx must wrap content with baseline <PageShell> or <LegendCard>",
  );
});

// ---------------------------------------------------------------------------
// /monitor / /settings / / — dashboard pages wrapped in PageShell
// ---------------------------------------------------------------------------

test("monitor.tsx wraps in <PageShell>", () => {
  assert.ok(
    monitorSrc.includes("<PageShell"),
    "monitor.tsx must wrap its view in <PageShell>",
  );
});

test("settings.tsx wraps in <PageShell>", () => {
  assert.ok(
    settingsSrc.includes("<PageShell"),
    "settings.tsx must wrap its view in <PageShell>",
  );
});

test("index.tsx (dashboard) wraps in <PageShell>", () => {
  assert.ok(
    indexSrc.includes("<PageShell"),
    "index.tsx must wrap its view in <PageShell>",
  );
});

// ---------------------------------------------------------------------------
// /admin/conversations — ISSUE-0010 redirect contract preserved
// ---------------------------------------------------------------------------

test("admin.conversations.tsx preserves the ISSUE-0010 redirect contract", () => {
  assert.ok(
    adminConversationsSrc.includes("redirect"),
    "admin.conversations.tsx must still call redirect for bare /admin/conversations",
  );
});

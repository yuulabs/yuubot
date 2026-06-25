// actors-pages.test.ts
//
// Source-text contract test for ISSUE-0007 S3 — Actors 页（browse + editor +
// detail）照 demo 重建. Reads the four route files and asserts the demo
// interaction surface is present in source, the editor is split into its own
// routes, and the documented schema deviations (D1–D3 + D-extra) are visible.
//
// Shape mirrors conversation-entry-via-actor.test.ts: node:test + readFileSync,
// no component rendering.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (name: string): string =>
  readFileSync(path.join(here, name), "utf8");

test("actors.tsx browse view exposes SearchBox + SegFilter + LayoutToggle + Empty + data-layout", () => {
  const src = read("actors.tsx");
  assert.ok(src.includes("<SearchBox"), "actors browse must render <SearchBox>");
  assert.ok(src.includes("<SegFilter"), "actors browse must render <SegFilter>");
  assert.ok(src.includes("<LayoutToggle"), "actors browse must render <LayoutToggle>");
  assert.ok(src.includes("<Empty"), "actors browse must render <Empty>");
  assert.ok(src.includes("data-layout"), "actors browse must carry data-layout (grid/list)");
});

test("actors.tsx no longer inlines a <form> create (create moved to /actors/new)", () => {
  const src = read("actors.tsx");
  assert.ok(!/<form\b/.test(src), "actors browse must not inline a create <form>");
  assert.ok(!/useCreateResource/.test(src), "actors browse must not call useCreateResource");
});

test("actors.$id.tsx is pure detail: three LegendCard titles + danger RailCard + listConversations + ingress-rules client filter", () => {
  const src = read("actors.$id.tsx");
  assert.ok(src.includes("配置概览"), "detail must render 配置概览 LegendCard");
  assert.ok(src.includes("事件路由"), "detail must render 事件路由 LegendCard");
  assert.ok(src.includes("能力"), "detail must render 能力 LegendCard");
  assert.ok(src.includes("danger"), "detail must render a danger RailCard");
  assert.ok(src.includes("listConversations"), "detail must list this Actor's conversations via listConversations");
  assert.ok(src.includes(".filter("), "detail must client-filter ingress-rules by actor_id");
});

test("actors.$id.tsx no longer inlines the edit form (no Textarea system_prompt edit)", () => {
  const src = read("actors.$id.tsx");
  assert.ok(!/<Textarea\b/.test(src), "detail must not inline a System-prompt Textarea editor");
  assert.ok(!/useUpdateResource/.test(src), "detail must not call useUpdateResource (edit moved out)");
});

test("actors.new.tsx exists and drives the dual create call (Character then Actor)", () => {
  const src = read("actors.new.tsx");
  assert.ok(src.includes("<form"), "editor must render a <form>");
  assert.ok(
    src.includes("createCharacter") ||
      /useCreateResource<CharacterResource>/.test(src) ||
      /useCreateResource\([^)]*characters/.test(src),
    "editor must reference createCharacter / characters create hook",
  );
  assert.ok(
    src.includes("createActor") ||
      /useCreateResource<ActorResource>/.test(src) ||
      /useCreateResource\([^)]*actors/.test(src),
    "editor must reference createActor / actors create hook",
  );
});

test("actors.$id.edit.tsx exists and drives update", () => {
  const src = read("actors.$id.edit.tsx");
  assert.ok(
    src.includes("useUpdateResource") || src.includes("updateActor"),
    "edit route must reference useUpdateResource / updateActor",
  );
});

test("schema-deviation placeholders are visible in source (max_concurrent / cooldown disabled, strict global hint)", () => {
  const here2 = path.dirname(fileURLToPath(import.meta.url));
  const newSrc = readFileSync(path.join(here2, "actors.new.tsx"), "utf8");
  const editSrc = readFileSync(path.join(here2, "actors.$id.edit.tsx"), "utf8");
  // The disabled placeholders live in the shared presentational editor body.
  const editorSrc = readFileSync(
    path.join(here2, "..", "components", "baseline", "ActorEditor.tsx"),
    "utf8",
  );
  const combined = `${newSrc}\n${editSrc}\n${editorSrc}`;
  assert.ok(combined.includes("待后端"), "disabled placeholders must surface 待后端 hint");
});

// actors-pages.test.ts
//
// Source-text contract test for ISSUE-0007 S3 — Actors 页（browse + editor +
// detail）照 demo 重建. Reads the route files and asserts the demo interaction
// surface is present in source and the documented schema deviations
// (D1–D3 + D-extra) are visible.
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
});

test("actors.$id.tsx merges detail and edit: ActorEditor edit mode + update + save + detail blocks", () => {
  const src = read("actors.$id.tsx");
  assert.ok(src.includes("<ActorEditor"), "detail must reuse <ActorEditor>");
  assert.ok(src.includes('mode="edit"'), "detail must render ActorEditor in edit mode");
  assert.ok(src.includes("useUpdateResource"), "detail must update Actor resources");
  assert.ok(src.includes('form="actor-editor-form"'), "detail must expose a topbar save button");
  assert.ok(src.includes("运行上下文"), "detail must render runtime context detail block");
  assert.ok(src.includes("事件路由"), "detail must render 事件路由 LegendCard");
  assert.ok(src.includes("能力"), "detail must render 能力 LegendCard");
  assert.ok(src.includes("onDelete"), "detail must pass delete action into ActorEditor danger rail");
  assert.ok(src.includes("listConversations"), "detail must list this Actor's conversations via listConversations");
  assert.ok(src.includes(".filter("), "detail must client-filter ingress-rules by actor_id");
  assert.ok(src.includes("<form"), "merged detail/editor must render a <form>");
});

test("actors.new.tsx exists and creates an Actor with inline persona", () => {
  const src = read("actors.new.tsx");
  assert.ok(src.includes("<form"), "editor must render a <form>");
  assert.ok(
    src.includes("persona_prompt") || src.includes("systemPrompt"),
    "editor must carry inline persona_prompt state",
  );
  assert.ok(
    src.includes("createActor") ||
      /useCreateResource<ActorResource>/.test(src) ||
      /useCreateResource\([^)]*actors/.test(src),
    "editor must reference createActor / actors create hook",
  );
});

test("actors.$id.edit.tsx redirects to the merged detail/editor page", () => {
  const src = read("actors.$id.edit.tsx");
  assert.ok(src.includes("redirect"), "legacy edit route must redirect");
  assert.ok(src.includes('to: "/actors/$id"'), "legacy edit route must target merged detail/editor");
});

test("actor editor labels match the create/detail contract", () => {
  const here2 = path.dirname(fileURLToPath(import.meta.url));
  const newSrc = readFileSync(path.join(here2, "actors.new.tsx"), "utf8");
  const editSrc = readFileSync(path.join(here2, "actors.$id.edit.tsx"), "utf8");
  const detailSrc = readFileSync(path.join(here2, "actors.$id.tsx"), "utf8");
  const editorSrc = readFileSync(
    path.join(here2, "..", "components", "baseline", "ActorEditor.tsx"),
    "utf8",
  );
  const combined = `${newSrc}\n${editSrc}\n${detailSrc}\n${editorSrc}`;
  assert.ok(combined.includes("Actor Type"), "Actor pages must expose Actor Type");
  assert.ok(combined.includes("actorType"), "Actor editor state must carry actorType");
  assert.ok(combined.includes("LLM 供应商"), "Actor pages must label provider selection as LLM 供应商");
  assert.ok(
    combined.includes("Persona"),
    "Actor pages must expose Persona",
  );
  const oldAgentLabel = ["Agent", "规格"].join(" ");
  const oldProviderLabel = ["LLM", "角色"].join(" ");
  const oldPlaceholder = "待" + "后端";
  assert.ok(!combined.includes(oldAgentLabel), "Actor pages must not expose the old agent label");
  assert.ok(!combined.includes(oldProviderLabel), "Actor pages must not expose the old provider label");
  assert.ok(!combined.includes(oldPlaceholder), "Actor pages must not render placeholder controls");
});

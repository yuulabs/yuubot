// actors-update-presets.test.ts
//
// Source-text contract test for ISSUE-0005 — Actors page "update preset Actors"
// action. Existing users (who already have a backend, so the onboarding dialog
// never fired) reach the seeded preset Actors through this button. The action
// reuses the shared @/lib/presets payload and the actors create hook, skips
// Actors whose name already exists, and binds new Actors to a chosen backend.
//
// Same node:test + readFileSync style as providers-onboarding.test.ts (no jsdom).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const actorsSrc = readFileSync(path.join(here, "actors.tsx"), "utf8");
const presetsSrc = readFileSync(
  path.join(here, "..", "lib", "presets.ts"),
  "utf8",
);

// Stable seeded preset ids the preset Actor create calls MUST reference.
const STABLE_PRESET_IDS = [
  "builtin-character-general",
  "builtin-capability-general",
  "builtin-character-shiori",
  "builtin-capability-shiori",
] as const;

test("actors.tsx ships the 更新预设 Actor button", () => {
  assert.ok(
    actorsSrc.includes("更新预设 Actor"),
    'actors.tsx must include the 更新预设 Actor button label',
  );
});

test("actors.tsx shares the preset list from @/lib/presets", () => {
  assert.ok(
    actorsSrc.includes("@/lib/presets"),
    "actors.tsx must import the preset definitions from @/lib/presets",
  );
});

test("actors.tsx creates Actors via useCreateResource(\"actors\")", () => {
  assert.ok(
    actorsSrc.includes("useCreateResource") &&
      actorsSrc.includes("<ActorResource>") &&
      actorsSrc.includes('"actors"'),
    'actors.tsx must useCreateResource<ActorResource>("actors") for preset Actor creation',
  );
});

test("actors.tsx skips preset Actors whose name already exists", () => {
  assert.ok(
    actorsSrc.includes("existingActorNames.has") || actorsSrc.includes(".some((a) => a.name ==="),
    "actors.tsx must skip preset Actors whose name already exists before creating",
  );
});

test("actors.tsx keeps the button disabled when no backend exists", () => {
  assert.ok(
    actorsSrc.includes("backends.length === 0"),
    "actors.tsx must disable the update-preset button when no backend exists",
  );
});

test("actors.tsx surfaces a backend picker in the sync dialog", () => {
  assert.ok(
    actorsSrc.includes("syncBackendId"),
    "actors.tsx must track a selected backend id for the sync dialog",
  );
});

test("@/lib/presets references all four stable seeded preset ids", () => {
  for (const id of STABLE_PRESET_IDS) {
    assert.ok(
      presetsSrc.includes(id),
      `@/lib/presets must reference stable preset id ${id}`,
    );
  }
});

test("@/lib/presets builds the create payload with backend bind + non-zero budget", () => {
  assert.ok(
    presetsSrc.includes("default_llm_backend_id"),
    "@/lib/presets must set default_llm_backend_id on the preset Actor payload",
  );
  assert.ok(
    presetsSrc.includes("default_budget"),
    "@/lib/presets must set default_budget on the preset Actor payload",
  );
  assert.ok(
    presetsSrc.includes("max_usd") && presetsSrc.includes("2.0"),
    "@/lib/presets must default the preset Actor budget max_usd to 2.0",
  );
});

// providers-onboarding.test.ts
//
// Source-text contract test for ISSUE-0005 Phase 3 — Provider onboarding flow.
// Asserts the source of `providers.tsx` carries the onboarding dialog text,
// the stable seeded preset ids, the actor create resource hook, the
// first-backend detection condition, and the editable backend budget fields.
// Same node:test + readFileSync style as misc-pages.test.ts (no jsdom).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const providersSrc = readFileSync(path.join(here, "providers.tsx"), "utf8");

test("providers.tsx ships the onboarding dialog description verbatim", () => {
  assert.ok(
    providersSrc.includes(
      "是否使用当前 LLM backend 创建预设 Actor?（安装后即可使用）",
    ),
    "providers.tsx must include the onboarding dialog description text",
  );
});

test("providers.tsx sources preset ids from the shared @/lib/presets module", () => {
  // The stable seeded preset ids now live in @/lib/presets (covered by
  // actors-update-presets.test). providers.tsx must import from there rather
  // than re-declaring the ids inline.
  assert.ok(
    providersSrc.includes("@/lib/presets"),
    "providers.tsx must source preset definitions from @/lib/presets",
  );
});

test("providers.tsx creates Actors via useCreateResource(\"actors\")", () => {
  assert.ok(
    providersSrc.includes("useCreateResource") &&
      providersSrc.includes("<ActorResource>") &&
      providersSrc.includes('"actors"'),
    'providers.tsx must useCreateResource<ActorResource>("actors") for preset Actor creation',
  );
});

test("providers.tsx captures the first-backend condition before create", () => {
  assert.ok(
    providersSrc.includes("backends.length === 0"),
    "providers.tsx must capture backends.length === 0 before the create call",
  );
});

test("providers.tsx sends editable daily_usd and monthly_usd budget fields", () => {
  assert.ok(
    providersSrc.includes("daily_usd") && providersSrc.includes("monthly_usd"),
    "providers.tsx must send daily_usd and monthly_usd in the backend create payload",
  );
});

test("providers.tsx binds created Actors to the new backend via the shared payload helper", () => {
  // The concrete binding fields live in @/lib/presets (covered by
  // actors-update-presets.test); providers.tsx must route through that helper
  // so the binding is shared.
  assert.ok(
    providersSrc.includes("presetActorCreatePayload"),
    "providers.tsx must build preset Actor payloads via presetActorCreatePayload",
  );
});

test("providers.tsx dialog includes 创建 (primary) and 跳过 (secondary) actions", () => {
  assert.ok(
    providersSrc.includes("创建") && providersSrc.includes("跳过"),
    "providers.tsx onboarding dialog must expose 创建 and 跳过 actions",
  );
});

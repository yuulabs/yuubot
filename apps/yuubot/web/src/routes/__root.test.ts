// __root.test.ts
//
// Source-marker test for ISSUE-0007 S2: the app shell refactor in __root.tsx
// (sidebar brand + two demo nav groups + runner footer + topbar actions
// injection). Mirrors the conversation-entry-via-actor.test.ts /
// baseline.test.ts pattern (node:test + readFileSync). Asserts source-text
// contracts — no component rendering (no jsdom).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const rootSrc = readFileSync(path.join(here, "__root.tsx"), "utf8");
const rootPath = path.join(here, "..", "components", "baseline", "AppShell.tsx");
const appShellSrc = existsSync(rootPath)
  ? readFileSync(rootPath, "utf8")
  : "";
const barrelPath = path.join(here, "..", "components", "baseline", "index.ts");
const barrelSrc = readFileSync(barrelPath, "utf8");

test("__root.tsx wires useHealth()", () => {
  assert.ok(/useHealth\(\)/.test(rootSrc), "__root.tsx must call useHealth()");
});

test('__root.tsx renders the two demo nav group labels (运行时 / 系统)', () => {
  assert.ok(rootSrc.includes("运行时"), "nav must label the runtime group 运行时");
  assert.ok(rootSrc.includes("系统"), "nav must label the system group 系统");
});

test("__root.tsx carries the seven core nav links", () => {
  for (const to of [
    `to="/actors"`,
    `to="/routes"`,
    `to="/capability-sets"`,
    `to="/providers"`,
    `to="/integrations"`,
    `to="/monitor"`,
    `to="/settings"`,
  ]) {
    assert.ok(rootSrc.includes(to), `nav must include ${to}`);
  }
});

test("__root.tsx surfaces a runner footer (daemon / Runner)", () => {
  assert.ok(
    /daemon|Runner/.test(rootSrc),
    "root must reference daemon or Runner for the sidebar footer",
  );
});

test("__root.tsx provides an actions-injection mechanism", () => {
  const ctxInRoot = /createRootRouteWithContext|AppShellActionsContext|useAppShellActions/.test(rootSrc);
  const ctxInShell = /AppShellActionsContext|useAppShellActions/.test(appShellSrc);
  assert.ok(
    ctxInRoot || ctxInShell,
    "root or AppShell must define createRootRouteWithContext / AppShellActionsContext / useAppShellActions",
  );
});

test('__root.tsx drops the legacy neutral-shell "Dashboard" / "Overview" text', () => {
  assert.ok(!rootSrc.includes("Dashboard"), 'no hardcode "Dashboard" string');
  assert.ok(!rootSrc.includes("Overview"), 'no hardcode "Overview" string');
});

test("AppShell.tsx exists and is exported via the baseline barrel", () => {
  assert.ok(/export\s+\*\s+from\s+"\.\/AppShell"/.test(barrelSrc), 'index.ts must re-export AppShell');
});

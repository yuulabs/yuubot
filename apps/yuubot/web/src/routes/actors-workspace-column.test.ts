import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const actorSrc = readFileSync(path.join(here, "actors.tsx"), "utf8");

test("actors list page has a Workspace column driven by capability_set.workspace_path", () => {
  assert.ok(/<TableHead[^>]*>\s*Workspace\s*<\/TableHead>/.test(actorSrc),
    "missing Workspace TableHead");
  assert.ok(
    actorSrc.includes("capability_set?.workspace_path") ||
    actorSrc.includes("capability_set.workspace_path"),
    "missing capability_set.workspace_path reference in actors.tsx",
  );
  assert.ok(
    /\/workspace\/\$/.test(actorSrc) || actorSrc.includes("/workspace/${"),
    "missing /workspace/ href in actors.tsx",
  );
  assert.ok(actorSrc.includes('target="_blank"'),
    "missing target=_blank for external workspace link");
});

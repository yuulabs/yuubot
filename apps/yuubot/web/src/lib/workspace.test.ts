import assert from "node:assert/strict";
import { test } from "node:test";

import { workspaceHref } from "./workspace.ts";

test("workspaceHref links a workspace root", () => {
  assert.equal(workspaceHref("actors/general"), "/workspace/actors/general/");
});

test("workspaceHref links a file inside a workspace", () => {
  assert.equal(
    workspaceHref("actors/general", "src/main.py"),
    "/workspace/actors/general/src/main.py",
  );
});

test("workspaceHref encodes path segments", () => {
  assert.equal(
    workspaceHref("actor space", "notes/a b.md"),
    "/workspace/actor%20space/notes/a%20b.md",
  );
});

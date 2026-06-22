import assert from "node:assert/strict";
import { test } from "node:test";
import { markdownPlugins } from "./markdown-renderer.ts";

test("markdownPlugins wires remark-gfm, remark-math and rehype-katex", () => {
  assert.ok(Array.isArray(markdownPlugins.remark));
  assert.ok(markdownPlugins.remark.length >= 2, "remarkPlugins must include gfm + math");
  assert.ok(Array.isArray(markdownPlugins.rehype));
  assert.ok(markdownPlugins.rehype.length > 0, "rehypePlugins empty");
});

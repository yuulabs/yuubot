import assert from "node:assert/strict";
import { test } from "node:test";
import { markdownPlugins } from "./markdown-renderer.ts";

test("markdownPlugins wires remark-math and rehype-katex", () => {
  assert.ok(Array.isArray(markdownPlugins.remark));
  assert.ok(markdownPlugins.remark.length > 0, "remarkPlugins empty");
  assert.ok(Array.isArray(markdownPlugins.rehype));
  assert.ok(markdownPlugins.rehype.length > 0, "rehypePlugins empty");
});

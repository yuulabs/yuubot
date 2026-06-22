import assert from "node:assert/strict";
import { test } from "node:test";
import {
  extractBashCommand,
  stripAnsi,
  parseEditArgs,
  renderSimpleDiff,
} from "./tool-renderers.ts";

test("extractBashCommand pulls command out of args envelope", () => {
  assert.equal(extractBashCommand('{"command":"ls -la"}'), "ls -la");
  assert.equal(extractBashCommand("garbage"), "garbage");
});

test("stripAnsi removes CSI color sequences", () => {
  assert.equal(stripAnsi("drwxr-xr-x \x1b[32mfoo\x1b[0m"), "drwxr-xr-x foo");
  assert.equal(stripAnsi("plain"), "plain");
});

test("parseEditArgs pulls path / old / new and rejects malformed", () => {
  assert.deepEqual(
    parseEditArgs('{"path":"foo.py","old_string":"a","new_string":"b"}'),
    { path: "foo.py", old_string: "a", new_string: "b" }
  );
  assert.equal(parseEditArgs("{}"), null);
  assert.equal(parseEditArgs("not json"), null);
});

test("renderSimpleDiff produces line-level +/- output", () => {
  const out = renderSimpleDiff("a\nb", "a\nc");
  // First line stays context "a", second "b" becomes -, third "c" becomes +.
  assert.deepEqual(out.map(l => l.kind), ["context", "del", "add"]);
  assert.deepEqual(out.map(l => l.text), ["a", "b", "c"]);
});

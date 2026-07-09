import assert from "node:assert/strict";
import { test } from "node:test";

import { formatWorkspaceRef, parseWorkspaceRefs, segmentsToText } from "./workspace-ref.ts";

test("formatWorkspaceRef trims paths", () => {
  assert.equal(formatWorkspaceRef(" uploads/image-png/cat.png "), "[[ uploads/image-png/cat.png ]]");
});

test("segmentsToText preserves mixed order", () => {
  assert.equal(
    segmentsToText([
      { kind: "file", path: "uploads/image-png/one.png" },
      { kind: "text", value: " cc " },
      { kind: "file", path: "uploads/image-png/two.png" },
    ]),
    "[[ uploads/image-png/one.png ]] cc [[ uploads/image-png/two.png ]]",
  );
});

test("parseWorkspaceRefs splits text and references", () => {
  assert.deepEqual(
    parseWorkspaceRefs("see [[ uploads/text-plain/report.txt ]] now"),
    [
      { type: "text", value: "see " },
      { type: "ref", path: "uploads/text-plain/report.txt" },
      { type: "text", value: " now" },
    ],
  );
});

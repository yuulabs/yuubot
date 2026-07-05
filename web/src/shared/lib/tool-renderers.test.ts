import assert from "node:assert/strict";
import { test } from "node:test";

import { extractToolStringArg } from "./tool-renderers.ts";

test("extractToolStringArg reads complete JSON string args", () => {
  assert.equal(
    extractToolStringArg(JSON.stringify({ code: "print(1)\n" }), "code"),
    "print(1)\n",
  );
});

test("extractToolStringArg streams incomplete JSON string args", () => {
  assert.equal(
    extractToolStringArg("{\"code\":\"import random\\n\\nrolls = [", "code"),
    "import random\n\nrolls = [",
  );
});

test("extractToolStringArg streams incomplete escaped content", () => {
  assert.equal(
    extractToolStringArg("{\"command\":\"printf \\\"hello", "command"),
    "printf \"hello",
  );
});

test("extractToolStringArg returns null before the requested field starts", () => {
  assert.equal(extractToolStringArg("{\"path\":\"a.py\",", "code"), null);
});

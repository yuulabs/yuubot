import assert from "node:assert/strict";
import { test } from "node:test";

import {
  extractToolStringArg,
  parseEditArgsPartial,
  renderTerminalOutput,
} from "./tool-renderers.ts";

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

test("parseEditArgsPartial streams incomplete edit arguments", () => {
  assert.deepEqual(
    parseEditArgsPartial("{\"path\":\"src/a.py\",\"old_string\":\"foo"),
    {
      path: "src/a.py",
      old_string: "foo",
      new_string: "",
    },
  );
});

test("parseEditArgsPartial reads complete edit arguments", () => {
  assert.deepEqual(
    parseEditArgsPartial(JSON.stringify({
      path: "src/a.py",
      old_string: "foo",
      new_string: "bar",
    })),
    {
      path: "src/a.py",
      old_string: "foo",
      new_string: "bar",
    },
  );
});

test("renderTerminalOutput overwrites carriage-return progress lines", () => {
  const raw = "\r 10%|#         |\r 50%|#####     |\r 80%|########  |\nDone\n";
  assert.equal(renderTerminalOutput(raw), " 80%|########  |\nDone\n");
});

test("renderTerminalOutput keeps plain multiline output", () => {
  assert.equal(renderTerminalOutput("hello\nworld\n"), "hello\nworld\n");
});

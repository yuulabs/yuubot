import assert from "node:assert/strict";
import test from "node:test";

import { terminalOptions } from "./terminal-options.ts";

test("terminal options enable the proposed API required by its addons", () => {
  assert.equal(terminalOptions(13).allowProposedApi, true);
});

import assert from "node:assert/strict";
import test from "node:test";

import { terminalShortcut } from "./terminal-shortcuts.ts";

const key = (value: string, overrides: Partial<Parameters<typeof terminalShortcut>[0]> = {}) => ({
  key: value, ctrlKey: true, metaKey: false, shiftKey: false, ...overrides,
});

test("Windows and Linux copy preserves Ctrl+C interrupt without a selection", () => {
  assert.equal(terminalShortcut(key("c"), false, false), null);
  assert.equal(terminalShortcut(key("c"), true, false), "copy");
  assert.equal(terminalShortcut(key("c", { shiftKey: true }), false, false), "copy");
  assert.equal(terminalShortcut(key("v"), false, false), null);
  assert.equal(terminalShortcut(key("v", { shiftKey: true }), false, false), "paste");
});

test("macOS Command shortcuts copy, paste, search, and zoom", () => {
  const mac = (value: string) => key(value, { ctrlKey: false, metaKey: true });
  assert.equal(terminalShortcut(mac("c"), false, true), "copy");
  assert.equal(terminalShortcut(mac("v"), false, true), "paste");
  assert.equal(terminalShortcut(mac("f"), false, true), "search");
  assert.equal(terminalShortcut(mac("+"), false, true), "zoom-in");
  assert.equal(terminalShortcut(mac("-"), false, true), "zoom-out");
  assert.equal(terminalShortcut(mac("0"), false, true), "zoom-reset");
});

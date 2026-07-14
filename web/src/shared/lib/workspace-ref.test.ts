import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatWorkspaceRef,
  normalizeNestedMarkdownImageRefs,
  parseWorkspaceRefs,
  resolveMarkdownImageSrc,
  segmentsToText,
} from "./workspace-ref.ts";

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

test("normalizeNestedMarkdownImageRefs unwraps nested workspace refs", () => {
  assert.equal(
    normalizeNestedMarkdownImageRefs("![对比]([[artifacts/asahi.jpg]])"),
    "![对比](artifacts/asahi.jpg)",
  );
  assert.equal(
    normalizeNestedMarkdownImageRefs("![a]([[ artifacts/x.png ]]) and [[ notes/a.md ]]"),
    "![a](artifacts/x.png) and [[ notes/a.md ]]",
  );
});

test("parseWorkspaceRefs keeps nested image refs as markdown text", () => {
  assert.deepEqual(
    parseWorkspaceRefs("![对比]([[artifacts/asahi.jpg]])\n详见 [[ notes/asahi.md ]]"),
    [
      { type: "text", value: "![对比](artifacts/asahi.jpg)\n详见 " },
      { type: "ref", path: "notes/asahi.md" },
    ],
  );
});

test("parseWorkspaceRefs ignores refs inside inline code", () => {
  assert.deepEqual(
    parseWorkspaceRefs("TOML `plugins = [[one], [two]]` and [[ notes.md ]]"),
    [
      { type: "text", value: "TOML `plugins = [[one], [two]]` and " },
      { type: "ref", path: "notes.md" },
    ],
  );
  assert.deepEqual(
    parseWorkspaceRefs("``value = `[[not-a-ref]]` `` [[ yes.md ]]"),
    [
      { type: "text", value: "``value = `[[not-a-ref]]` `` " },
      { type: "ref", path: "yes.md" },
    ],
  );
});

test("parseWorkspaceRefs ignores refs inside fenced code blocks", () => {
  const content = "before [[ yes.md ]]\n```toml\nplugins = [[one], [two]]\nvalue = [[not-a-ref]]\n```\nafter";
  assert.deepEqual(parseWorkspaceRefs(content), [
    { type: "text", value: "before " },
    { type: "ref", path: "yes.md" },
    { type: "text", value: "\n```toml\nplugins = [[one], [two]]\nvalue = [[not-a-ref]]\n```\nafter" },
  ]);
});

test("normalizeNestedMarkdownImageRefs does not rewrite code", () => {
  assert.equal(
    normalizeNestedMarkdownImageRefs("`![image]([[ code.png ]])`\n```md\n![image]([[ fenced.png ]])\n```"),
    "`![image]([[ code.png ]])`\n```md\n![image]([[ fenced.png ]])\n```",
  );
});

test("resolveMarkdownImageSrc leaves absolute and data URLs alone", () => {
  const toUrl = (actorId: string, path: string) => `/api/actors/${actorId}/files/${path}`;
  assert.equal(
    resolveMarkdownImageSrc("amy", "https://cdn.example/a.jpg", toUrl),
    "https://cdn.example/a.jpg",
  );
  assert.equal(
    resolveMarkdownImageSrc("amy", "/api/actors/amy/files/artifacts/x.png", toUrl),
    "/api/actors/amy/files/artifacts/x.png",
  );
  assert.equal(
    resolveMarkdownImageSrc("amy", "artifacts/x.png", toUrl),
    "/api/actors/amy/files/artifacts/x.png",
  );
});

test("resolveMarkdownImageSrc resolves relative paths from a Markdown document", () => {
  const toUrl = (actorId: string, path: string) => `/api/actors/${actorId}/files/${path}`;
  assert.equal(
    resolveMarkdownImageSrc("amy", "images/chart.png", toUrl, "reports/week-one/summary.md"),
    "/api/actors/amy/files/reports/week-one/images/chart.png",
  );
  assert.equal(
    resolveMarkdownImageSrc("amy", "../shared/logo.png", toUrl, "reports/week-one/summary.md"),
    "/api/actors/amy/files/reports/shared/logo.png",
  );
});

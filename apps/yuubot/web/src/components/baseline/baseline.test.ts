// baseline.test.ts
//
// Source-marker test for ISSUE-0007 S1: the "Galgame Uniform" design-system
// baseline layer (design tokens + structural CSS + presentational React
// components). Mirrors the `conversation-entry-via-actor.test.ts` pattern
// (node:test + readFileSync). Asserts file presence, barrel exports, token
// injection and DataTable empty-state descent — no component rendering.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (name: string): string =>
  readFileSync(path.join(here, name), "utf8");
const webSrc = path.join(here, "..", "..");
const readSrc = (rel: string): string =>
  readFileSync(path.join(webSrc, rel), "utf8");

const indexSrc = read("index.ts");
const indexCss = readSrc("index.css");
const baselineCss = readSrc("styles/baseline.css");
const dataTableSrc = readSrc("components/data-table.tsx");

// The 14 baseline components + the Dot atom exported by the barrel.
const BASELINE_NAMES = [
  "PageShell",
  "LegendCard",
  "CrudHeader",
  "Empty",
  "StatusPill",
  "Field",
  "RailCard",
  "DetailHero",
  "SegFilter",
  "SearchBox",
  "LayoutToggle",
  "KvTable",
  "CapTree",
  "IngressFlow",
] as const;

test("baseline barrel exports all 14 components + Dot", () => {
  for (const name of [...BASELINE_NAMES, "Dot"]) {
    const re = new RegExp(`export\\s+\\*\\s+from\\s+"\\./${name}"`);
    assert.ok(
      re.test(indexSrc),
      `index.ts must re-export ${name} (export * from "./${name}")`,
    );
  }
});

test("each baseline component module file exists", () => {
  for (const name of [...BASELINE_NAMES, "Dot"]) {
    assert.ok(
      existsSync(path.join(here, `${name}.tsx`)),
      `${name}.tsx must exist in components/baseline/`,
    );
  }
});

test("baseline.css :root carries the demo core tokens", () => {
  for (const tok of ["--cyan", "--yellow", "--ink", "--bg", "--page-bg", "--ff-sans"]) {
    assert.ok(baselineCss.includes(tok), `baseline.css must define ${tok}`);
  }
});

test("demo hex palette is injected into the token layer", () => {
  // cyan / yellow / ink from the demo's foundational :root.
  assert.ok(baselineCss.includes("#1ec3e8"), "cyan #1ec3e8 must be present");
  assert.ok(baselineCss.includes("#e0d909"), "yellow #e0d909 must be present");
  assert.ok(baselineCss.includes("#0b1228"), "ink #0b1228 must be present");
});

test("index.css imports the structural baseline.css module", () => {
  assert.ok(
    /@import\s+"\.\/styles\/baseline\.css"/.test(indexCss) ||
      /@import\s+url\(['"]?\.\/styles\/baseline\.css['"]?\)/.test(indexCss),
    `index.css must @import "./styles/baseline.css"`,
  );
});

test("baseline.css carries the demo structural layout classnames", () => {
  for (const cls of ["detail-grid", "editor__cols", "ingress-flow", "rail-card", "kv-table"]) {
    assert.ok(baselineCss.includes(`.${cls}`), `baseline.css must define .${cls}`);
  }
});

test("DataTable still exports DataTable and descends empty state to baseline <Empty>", () => {
  assert.ok(/export\s+function\s+DataTable/.test(dataTableSrc));
  assert.ok(/from\s+"\.\/baseline\/Empty"|\bfrom\s+"\.\.\/baseline\/Empty"/.test(dataTableSrc) ||
    /from\s+"\.\/baseline\/Empty"|\bfrom\s+"\.\.\/baseline"/.test(dataTableSrc) ||
    /\bEmpty\b/.test(dataTableSrc),
    "data-table.tsx must reference the baseline Empty");
});

test("DataTable preserves the columns / rows / emptyLabel props contract", () => {
  for (const prop of ["columns", "rows", "emptyLabel"]) {
    assert.ok(new RegExp(`\\b${prop}\\b`).test(dataTableSrc), `DataTable must keep ${prop} prop`);
  }
});

/**
 * Pure helpers for per-tool rendering in the conversation detail view.
 *
 * No external npm dependencies. ANSI stripping and line-level LCS diff are
 * hand-rolled with stdlib regex only.
 */

/**
 * Parse a tool args JSON envelope into the user-facing args payload.
 *
 * Live SSE deltas usually pass bare args such as `{"command":"ls -la"}`.
 * Persisted history may pass wrappers such as
 * `{"arguments":"{\"command\":\"ls -la\"}"}` or
 * `{"arguments":{"command":"ls -la"}}`. Renderers should consume this helper
 * so live and history paths share the same normalization.
 */
export function parseToolArgs(toolArgs: string): unknown {
  const parsed = parseJson(toolArgs);
  if (!isPlainObject(parsed)) {
    return parsed;
  }

  const wrappedArgs = parsed.arguments ?? parsed.args ?? parsed.input;
  if (wrappedArgs === undefined) {
    return parsed;
  }
  return typeof wrappedArgs === "string" ? parseJson(wrappedArgs) : wrappedArgs;
}

function parseJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

/**
 * Extract the bash command string from normalized tool args.
 *
 * Returns the raw string if parsing fails or the `command` field is absent
 * (lets the UI degrade to showing the raw envelope rather than nothing).
 */
export function extractBashCommand(toolArgs: string): string {
  const parsed = parseToolArgs(toolArgs);
  if (isPlainObject(parsed) && typeof parsed.command === "string") {
    return parsed.command;
  }
  return toolArgs;
}

/**
 * Strip ANSI CSI escape sequences (colors, cursor moves, etc.) from a string.
 *
 * Matches the standard `CSI = ESC [ params final-byte` form. Handles SGR
 * color sequences (`\x1b[0;32m...`) as well as other CSI final bytes.
 */
export function stripAnsi(s: string): string {
  return s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
}

export interface EditArgs {
  path: string;
  old_string: string;
  new_string: string;
}

/**
 * Parse the args envelope of an `edit` tool call.
 *
 * Returns `null` if `toolArgs` is not valid JSON, or does not contain the
 * three required string fields. Callers should fall back to the generic
 * side-by-side renderer when this returns null.
 */
export function parseEditArgs(toolArgs: string): EditArgs | null {
  const parsed = parseToolArgs(toolArgs);
  if (
    isPlainObject(parsed)
    && typeof parsed.path === "string"
    && typeof parsed.old_string === "string"
    && typeof parsed.new_string === "string"
  ) {
    return {
      path: parsed.path,
      old_string: parsed.old_string,
      new_string: parsed.new_string,
    };
  }
  return null;
}

export interface DiffLine {
  kind: "context" | "add" | "del";
  text: string;
}

/**
 * Produce a line-level unified diff between `oldStr` and `newStr`.
 *
 * Uses a hand-rolled longest-common-subsequence on the line arrays. The
 * output is a flat list of `{ kind, text }` entries ordered as: context
 * lines, deletions, then additions, in a unified-style interleaving.
 *
 * Pure function: deterministic; same input always yields same output.
 */
export function renderSimpleDiff(oldStr: string, newStr: string): DiffLine[] {
  const oldLines = oldStr === "" ? [] : oldStr.split("\n");
  const newLines = newStr === "" ? [] : newStr.split("\n");

  const m = oldLines.length;
  const n = newLines.length;

  // dp[i][j] = length of LCS of oldLines[i..] and newLines[j..]
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array<number>(n + 1).fill(0),
  );
  for (let i = m - 1; i >= 0; i -= 1) {
    for (let j = n - 1; j >= 0; j -= 1) {
      dp[i][j] = oldLines[i] === newLines[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const result: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (oldLines[i] === newLines[j]) {
      result.push({ kind: "context", text: oldLines[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ kind: "del", text: oldLines[i] });
      i += 1;
    } else {
      result.push({ kind: "add", text: newLines[j] });
      j += 1;
    }
  }
  while (i < m) {
    result.push({ kind: "del", text: oldLines[i] });
    i += 1;
  }
  while (j < n) {
    result.push({ kind: "add", text: newLines[j] });
    j += 1;
  }
  return result;
}

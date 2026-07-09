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

export function extractToolStringArg(toolArgs: string, key: string): string | null {
  const parsed = parseJson(toolArgs);
  if (isPlainObject(parsed)) {
    const wrappedArgs = parsed.arguments ?? parsed.args ?? parsed.input;
    const normalized = wrappedArgs === undefined
      ? parsed
      : typeof wrappedArgs === "string"
        ? parseJson(wrappedArgs)
        : wrappedArgs;
    if (isPlainObject(normalized) && typeof normalized[key] === "string") {
      return normalized[key];
    }
    if (typeof wrappedArgs === "string") {
      const wrappedPartial = extractPartialJsonStringField(wrappedArgs, key);
      if (wrappedPartial !== null) {
        return wrappedPartial;
      }
    }
  }
  return extractPartialJsonStringField(toolArgs, key);
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

function extractPartialJsonStringField(input: string, key: string): string | null {
  const pattern = new RegExp(`"${escapeRegExp(key)}"\\s*:\\s*"`);
  const match = pattern.exec(input);
  if (!match) {
    return null;
  }

  let raw = "";
  let escaped = false;
  for (let index = match.index + match[0].length; index < input.length; index += 1) {
    const char = input[index];
    if (escaped) {
      raw += `\\${char}`;
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char === "\"") {
      return decodeJsonStringFragment(raw);
    }
    raw += char;
  }
  if (escaped) {
    raw += "\\";
  }
  return decodeJsonStringFragment(raw);
}

function decodeJsonStringFragment(input: string): string {
  let output = "";
  for (let index = 0; index < input.length; index += 1) {
    const char = input[index];
    if (char !== "\\") {
      output += char;
      continue;
    }

    const next = input[index + 1];
    if (next === undefined) {
      break;
    }
    index += 1;
    if (next === "\"" || next === "\\" || next === "/") {
      output += next;
    } else if (next === "b") {
      output += "\b";
    } else if (next === "f") {
      output += "\f";
    } else if (next === "n") {
      output += "\n";
    } else if (next === "r") {
      output += "\r";
    } else if (next === "t") {
      output += "\t";
    } else if (next === "u") {
      const hex = input.slice(index + 1, index + 5);
      if (/^[0-9a-fA-F]{4}$/.test(hex)) {
        output += String.fromCharCode(Number.parseInt(hex, 16));
        index += 4;
      }
    } else {
      output += next;
    }
  }
  return output;
}

function escapeRegExp(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
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

const TAB_WIDTH = 8;
const OSC_CONTROL_RE = /\x1b\][^\x1b\x07]*(?:\x07|\x1b\\)/g;
const C0_CONTROL_RE = /[\x00-\x08\x0b-\x0d\x0e-\x1f\x7f]/g;

class PtyDisplayBuffer {
  private lines: string[] = [""];
  private row = 0;
  private col = 0;
  private pending = "";

  feed(chunk: string): void {
    if (!chunk) return;
    const data = `${this.pending}${chunk}`;
    this.pending = "";
    let index = 0;
    while (index < data.length) {
      const char = data[index];
      if (char === "\x1b") {
        const consumed = this.consumeEscape(data, index);
        if (consumed === 0) {
          this.pending = data.slice(index);
          return;
        }
        index += consumed;
        continue;
      }
      if (char === "\r") {
        this.col = 0;
        index += 1;
        continue;
      }
      if (char === "\n") {
        this.newline();
        index += 1;
        continue;
      }
      if (char === "\b") {
        this.col = Math.max(0, this.col - 1);
        index += 1;
        continue;
      }
      if (char === "\t") {
        this.col = this.col + TAB_WIDTH - (this.col % TAB_WIDTH);
        index += 1;
        continue;
      }
      if (char < " " || char === "\x7f") {
        index += 1;
        continue;
      }
      this.writeChar(char);
      index += 1;
    }
  }

  snapshot(): string {
    if (this.lines.length === 0) return "";
    let end = this.row;
    while (end + 1 < this.lines.length && this.lines[end + 1]) {
      end += 1;
    }
    return this.lines.slice(0, end + 1).join("\n");
  }

  private consumeEscape(data: string, start: number): number {
    if (data[start] !== "\x1b") return 0;
    if (start + 1 >= data.length) return 0;
    const nextChar = data[start + 1];
    if (nextChar === "]") {
      const end = this.findOscEnd(data, start + 2);
      if (end === null) return 0;
      return end - start;
    }
    if (nextChar === "[") {
      const end = this.findCsiEnd(data, start + 2);
      if (end === null) return 0;
      const body = data.slice(start + 2, end);
      const final = data[end];
      this.dispatchCsi(body, final);
      return end - start + 1;
    }
    return 2;
  }

  private findOscEnd(data: string, start: number): number | null {
    let index = start;
    while (index < data.length) {
      if (data[index] === "\x07") return index + 1;
      if (data[index] === "\x1b" && index + 1 < data.length && data[index + 1] === "\\") {
        return index + 2;
      }
      index += 1;
    }
    return null;
  }

  private findCsiEnd(data: string, start: number): number | null {
    let index = start;
    while (index < data.length) {
      const char = data[index];
      if (/[A-Za-z@`~]/.test(char)) return index;
      index += 1;
    }
    return null;
  }

  private parseParams(body: string): number[] {
    if (!body) return [];
    return body.split(";").map((part) => {
      if (!part || !/^\d+$/.test(part)) return 0;
      return Number.parseInt(part, 10);
    });
  }

  private dispatchCsi(body: string, final: string): void {
    const params = this.parseParams(body);
    if (final === "A") {
      const step = params[0] || 1;
      this.row = Math.max(0, this.row - step);
      this.clampCol();
      return;
    }
    if (final === "B") {
      const step = params[0] || 1;
      this.ensureRow(this.row + step);
      this.row += step;
      this.clampCol();
      return;
    }
    if (final === "C") {
      this.col += params[0] || 1;
      return;
    }
    if (final === "D") {
      this.col = Math.max(0, this.col - (params[0] || 1));
      return;
    }
    if (final === "G") {
      const column = params[0] || 1;
      this.col = Math.max(0, column - 1);
      return;
    }
    if (final === "H" || final === "f") {
      const row = (params[0] || 1) - 1;
      const col = ((params.length > 1 ? params[1] : 1) || 1) - 1;
      this.ensureRow(Math.max(0, row));
      this.row = Math.max(0, row);
      this.col = Math.max(0, col);
      return;
    }
    if (final === "K") {
      const mode = params[0] || 0;
      const line = this.lineAt(this.row);
      if (mode === 0) {
        this.lines[this.row] = line.slice(0, this.col);
      } else if (mode === 1) {
        this.lines[this.row] = `${" ".repeat(this.col)}${line.slice(this.col)}`;
      } else if (mode === 2) {
        this.lines[this.row] = "";
        this.col = 0;
      }
      return;
    }
    if (final === "J") {
      const mode = params[0] || 0;
      if (mode === 0) {
        this.lines[this.row] = this.lineAt(this.row).slice(0, this.col);
        for (let row = this.row + 1; row < this.lines.length; row += 1) {
          this.lines[row] = "";
        }
      } else if (mode === 2) {
        this.lines = [""];
        this.row = 0;
        this.col = 0;
      }
      return;
    }
    if (final === "m") {
      return;
    }
  }

  private newline(): void {
    this.row += 1;
    this.ensureRow(this.row);
    this.col = 0;
  }

  private writeChar(char: string): void {
    this.ensureRow(this.row);
    let line = this.lineAt(this.row);
    if (this.col >= line.length) {
      line = `${line}${" ".repeat(this.col - line.length)}${char}`;
    } else {
      line = `${line.slice(0, this.col)}${char}${line.slice(this.col + 1)}`;
    }
    this.lines[this.row] = line;
    this.col += 1;
  }

  private ensureRow(row: number): void {
    while (this.lines.length <= row) {
      this.lines.push("");
    }
  }

  private lineAt(row: number): string {
    this.ensureRow(row);
    return this.lines[row];
  }

  private clampCol(): void {
    this.col = Math.min(this.col, this.lineAt(this.row).length);
  }
}

/** Render PTY output with terminal overwrite semantics, then strip remaining controls. */
export function renderTerminalOutput(raw: string): string {
  const buffer = new PtyDisplayBuffer();
  buffer.feed(raw);
  const rendered = buffer.snapshot();
  return stripAnsi(rendered.replace(OSC_CONTROL_RE, "")).replace(C0_CONTROL_RE, "");
}

/** Format tool stdout/stderr for display in conversation and monitor views. */
export function formatToolOutput(raw: string): string {
  return renderTerminalOutput(raw);
}

export interface EditArgs {
  path: string;
  old_string: string;
  new_string: string;
}

export function extractToolPath(toolArgs: string): string | null {
  const partial = extractToolStringArg(toolArgs, "path");
  if (partial !== null && partial.trim()) {
    return partial;
  }
  const parsed = parseToolArgs(toolArgs);
  if (isPlainObject(parsed) && typeof parsed.path === "string" && parsed.path.trim()) {
    return parsed.path;
  }
  return null;
}

/**
 * Parse the args envelope of an `edit` tool call.
 *
 * Returns `null` if `toolArgs` is not valid JSON, or does not contain the
 * three required string fields. Callers should fall back to the generic
 * side-by-side renderer when this returns null.
 */
export function parseEditArgsPartial(toolArgs: string): EditArgs {
  return {
    path: extractToolStringArg(toolArgs, "path") ?? "",
    old_string: extractToolStringArg(toolArgs, "old_string") ?? "",
    new_string: extractToolStringArg(toolArgs, "new_string") ?? "",
  };
}

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

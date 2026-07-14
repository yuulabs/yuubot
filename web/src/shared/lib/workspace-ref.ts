export type ComposerSegment =
  | { kind: "text"; value: string }
  | { kind: "file"; path: string; mime?: string; meta?: Record<string, unknown> };

export type WorkspaceRefSegment =
  | { type: "text"; value: string }
  | { type: "ref"; path: string };

const WORKSPACE_REF_PATTERN = /\[\[\s*([^\]]+?)\s*\]\]/g;
const NESTED_MD_IMAGE_REF_PATTERN = /!\[([^\]]*)\]\(\[\[\s*([^\]]+?)\s*\]\]\)/g;

type TextRange = { start: number; end: number };

function markdownCodeRanges(text: string): TextRange[] {
  const ranges: TextRange[] = [];
  let fence: { marker: string; size: number; start: number } | undefined;
  let lineStart = 0;

  while (lineStart < text.length) {
    const newline = text.indexOf("\n", lineStart);
    const lineEnd = newline === -1 ? text.length : newline + 1;
    const line = text.slice(lineStart, newline === -1 ? text.length : newline);
    if (fence) {
      const closing = line.match(/^ {0,3}(`{3,}|~{3,})[ \t]*$/)?.[1];
      if (closing?.[0] === fence.marker && closing.length >= fence.size) {
        ranges.push({ start: fence.start, end: lineEnd });
        fence = undefined;
      }
    } else {
      const opening = line.match(/^ {0,3}(`{3,}|~{3,})/)?.[1];
      if (opening) {
        fence = { marker: opening[0], size: opening.length, start: lineStart };
      }
    }
    lineStart = lineEnd;
  }
  if (fence) ranges.push({ start: fence.start, end: text.length });

  let cursor = 0;
  while (cursor < text.length) {
    const fenced = ranges.find((range) => cursor >= range.start && cursor < range.end);
    if (fenced) {
      cursor = fenced.end;
      continue;
    }
    if (text[cursor] !== "`") {
      cursor += 1;
      continue;
    }
    let delimiterEnd = cursor + 1;
    while (text[delimiterEnd] === "`") delimiterEnd += 1;
    const delimiter = text.slice(cursor, delimiterEnd);
    let closing = text.indexOf(delimiter, delimiterEnd);
    while (closing !== -1 && (text[closing - 1] === "`" || text[closing + delimiter.length] === "`")) {
      closing = text.indexOf(delimiter, closing + delimiter.length);
    }
    if (closing === -1) {
      cursor = delimiterEnd;
      continue;
    }
    ranges.push({ start: cursor, end: closing + delimiter.length });
    cursor = closing + delimiter.length;
  }
  return ranges.sort((left, right) => left.start - right.start);
}

function isInsideRange(start: number, end: number, ranges: TextRange[]): boolean {
  return ranges.some((range) => start < range.end && end > range.start);
}

export function formatWorkspaceRef(path: string): string {
  return `[[ ${path.trim()} ]]`;
}

/** Unwrap `![alt]([[ path ]])` so parseWorkspaceRefs does not split inside MD image URLs. */
export function normalizeNestedMarkdownImageRefs(text: string): string {
  const codeRanges = markdownCodeRanges(text);
  return text.replace(NESTED_MD_IMAGE_REF_PATTERN, (match, alt: string, path: string, offset: number) => {
    if (isInsideRange(offset, offset + match.length, codeRanges)) return match;
    return `![${alt}](${path.trim()})`;
  });
}

export function parseWorkspaceRefs(text: string): WorkspaceRefSegment[] {
  const normalized = normalizeNestedMarkdownImageRefs(text);
  const codeRanges = markdownCodeRanges(normalized);
  const segments: WorkspaceRefSegment[] = [];
  let cursor = 0;
  for (const match of normalized.matchAll(WORKSPACE_REF_PATTERN)) {
    const index = match.index ?? 0;
    if (isInsideRange(index, index + match[0].length, codeRanges)) continue;
    if (index > cursor) {
      segments.push({ type: "text", value: normalized.slice(cursor, index) });
    }
    const path = match[1]?.trim() ?? "";
    if (path) {
      segments.push({ type: "ref", path });
    } else {
      segments.push({ type: "text", value: match[0] });
    }
    cursor = index + match[0].length;
  }
  if (cursor < normalized.length) {
    segments.push({ type: "text", value: normalized.slice(cursor) });
  }
  return segments.length ? segments : [{ type: "text", value: normalized }];
}

export function segmentsToText(segments: ComposerSegment[]): string {
  return segments
    .map((segment) => segment.kind === "text" ? segment.value : formatWorkspaceRef(segment.path))
    .join("");
}

/** Resolve a Markdown image `src` to a fetchable URL. Absolute/data URLs stay as-is. */
export function resolveMarkdownImageSrc(
  actorId: string,
  src: string,
  toActorFileUrl: (actorId: string, path: string) => string,
  workspacePath = "",
): string {
  const trimmed = src.trim();
  if (!trimmed) return trimmed;
  if (/^(?:https?:|data:|blob:)/i.test(trimmed) || trimmed.startsWith("/")) {
    return trimmed;
  }
  if (!actorId) return trimmed;
  return toActorFileUrl(actorId, resolveWorkspaceRelativePath(workspacePath, trimmed));
}

function resolveWorkspaceRelativePath(workspacePath: string, path: string): string {
  const parts = workspacePath.split("/").filter(Boolean).slice(0, -1);
  for (const part of path.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return parts.join("/");
}

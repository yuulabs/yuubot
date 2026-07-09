export type ComposerSegment =
  | { kind: "text"; value: string }
  | { kind: "file"; path: string; mime?: string; meta?: Record<string, unknown> };

export type WorkspaceRefSegment =
  | { type: "text"; value: string }
  | { type: "ref"; path: string };

const WORKSPACE_REF_PATTERN = /\[\[\s*([^\]]+?)\s*\]\]/g;

export function formatWorkspaceRef(path: string): string {
  return `[[ ${path.trim()} ]]`;
}

export function parseWorkspaceRefs(text: string): WorkspaceRefSegment[] {
  const segments: WorkspaceRefSegment[] = [];
  let cursor = 0;
  for (const match of text.matchAll(WORKSPACE_REF_PATTERN)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      segments.push({ type: "text", value: text.slice(cursor, index) });
    }
    const path = match[1]?.trim() ?? "";
    if (path) {
      segments.push({ type: "ref", path });
    } else {
      segments.push({ type: "text", value: match[0] });
    }
    cursor = index + match[0].length;
  }
  if (cursor < text.length) {
    segments.push({ type: "text", value: text.slice(cursor) });
  }
  return segments.length ? segments : [{ type: "text", value: text }];
}

export function segmentsToText(segments: ComposerSegment[]): string {
  return segments
    .map((segment) => segment.kind === "text" ? segment.value : formatWorkspaceRef(segment.path))
    .join("");
}

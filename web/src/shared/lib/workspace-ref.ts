export type ComposerSegment =
  | { kind: "text"; value: string }
  | { kind: "file"; path: string; mime?: string; meta?: Record<string, unknown> };

export type WorkspaceRefSegment =
  | { type: "text"; value: string }
  | { type: "ref"; path: string };

const WORKSPACE_REF_PATTERN = /\[\[\s*([^\]]+?)\s*\]\]/g;
const NESTED_MD_IMAGE_REF_PATTERN = /!\[([^\]]*)\]\(\[\[\s*([^\]]+?)\s*\]\]\)/g;

export function formatWorkspaceRef(path: string): string {
  return `[[ ${path.trim()} ]]`;
}

/** Unwrap `![alt]([[ path ]])` so parseWorkspaceRefs does not split inside MD image URLs. */
export function normalizeNestedMarkdownImageRefs(text: string): string {
  return text.replace(NESTED_MD_IMAGE_REF_PATTERN, (_match, alt: string, path: string) => {
    return `![${alt}](${path.trim()})`;
  });
}

export function parseWorkspaceRefs(text: string): WorkspaceRefSegment[] {
  const normalized = normalizeNestedMarkdownImageRefs(text);
  const segments: WorkspaceRefSegment[] = [];
  let cursor = 0;
  for (const match of normalized.matchAll(WORKSPACE_REF_PATTERN)) {
    const index = match.index ?? 0;
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

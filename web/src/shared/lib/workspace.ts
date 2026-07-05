export function workspaceHref(
  workspacePath: string | null | undefined,
  filePath?: string | null,
): string | null {
  const normalized = workspacePath?.trim().replace(/^\/+/, "").replace(/\/+$/, "");
  if (!normalized) {
    return null;
  }
  const normalizedFile = filePath?.trim().replace(/^\/+/, "").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  if (normalizedFile) {
    parts.push(...normalizedFile.split("/").filter(Boolean));
  }
  const encoded = parts
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return normalizedFile ? `/workspace/${encoded}` : `/workspace/${encoded}/`;
}

export function workspaceHref(workspacePath: string | null | undefined): string | null {
  const normalized = workspacePath?.trim().replace(/^\/+/, "").replace(/\/+$/, "");
  if (!normalized) {
    return null;
  }
  const encoded = normalized
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `/workspace/${encoded}/`;
}

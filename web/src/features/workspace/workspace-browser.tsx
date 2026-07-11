import { useEffect, useMemo, useState, type DragEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileIcon, Folder, FolderPlus, MoveRight, Pencil, Share2, Trash2, Upload } from "lucide-react";

import type { ShareGrant, WorkspaceEntry } from "@/shared/types/api";
import {
  browseActor,
  createShare,
  createWorkspaceDirectory,
  deleteWorkspaceEntries,
  getActorFileUrl,
  moveWorkspaceEntries,
  renameWorkspaceEntry,
  uploadActorFile,
} from "@/shared/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingState, Panel } from "@/shared/components";

type ShareTtlPreset = "1h" | "24h" | "7d" | "never";
type Feedback = { title: string; body: string; copyText?: string };

const DEFAULT_SHARE_TTL: ShareTtlPreset = "24h";
const SHARE_TTL_OPTIONS: Array<{ value: ShareTtlPreset; label: string; ms: number | null }> = [
  { value: "1h", label: "1 hour", ms: 60 * 60 * 1000 },
  { value: "24h", label: "24 hours", ms: 24 * 60 * 60 * 1000 },
  { value: "7d", label: "7 days", ms: 7 * 24 * 60 * 60 * 1000 },
  { value: "never", label: "Never", ms: null },
];

export function WorkspaceBrowser({ actorId }: { actorId: string }) {
  const client = useQueryClient();
  const [path, setPath] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [copiedFeedback, setCopiedFeedback] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingShare, setPendingShare] = useState<WorkspaceEntry | null>(null);
  const [shareTtl, setShareTtl] = useState<ShareTtlPreset>(DEFAULT_SHARE_TTL);
  const [showHidden, setShowHidden] = useState(false);
  const query = useQuery({ queryKey: ["actor-workspace", actorId, path], queryFn: () => browseActor(actorId, path) });
  const entries = query.data?.entries ?? [];
  const visibleEntries = useMemo(
    () => (showHidden ? entries : entries.filter((entry) => !entry.name.startsWith("."))),
    [entries, showHidden],
  );
  const selectedPaths = useMemo(() => Array.from(selected), [selected]);

  useEffect(() => {
    setPath("");
    setSelected(new Set());
    setFeedback(null);
    setCopiedFeedback(false);
    setPendingShare(null);
    setShareTtl(DEFAULT_SHARE_TTL);
  }, [actorId]);

  function changePath(nextPath: string) {
    setPath(nextPath);
    setSelected(new Set());
  }

  async function refreshWorkspace() {
    await client.invalidateQueries({ queryKey: ["actor-workspace", actorId] });
  }

  async function runAction<T>(successMessage: string | Feedback | ((result: T) => string | Feedback), action: () => Promise<T>) {
    setBusy(true);
    try {
      const result = await action();
      const resolved = typeof successMessage === "function" ? successMessage(result) : successMessage;
      setFeedback(typeof resolved === "string" ? { title: "Action completed", body: resolved } : resolved);
      setSelected(new Set());
      await refreshWorkspace();
    } catch (err) {
      setFeedback({ title: "Action failed", body: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  function toggle(path: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  function selectAll() {
    const allVisibleSelected = visibleEntries.length > 0 && visibleEntries.every((entry) => selected.has(entry.path));
    setSelected(allVisibleSelected ? new Set() : new Set(visibleEntries.map((entry) => entry.path)));
  }

  function uploadFiles(files: File[]) {
    if (!files.length) {
      return;
    }
    void runAction(`${files.length} file(s) uploaded`, () => uploadActorFile(actorId, files, path));
  }

  function createDirectory() {
    const name = window.prompt("Directory name");
    if (!name) {
      return;
    }
    void runAction("Directory created", () => createWorkspaceDirectory(actorId, joinPath(path, name)));
  }

  function rename(entry: WorkspaceEntry) {
    const name = window.prompt("New name", entry.name);
    if (!name || name === entry.name) {
      return;
    }
    void runAction("Entry renamed", () => renameWorkspaceEntry(actorId, entry.path, name));
  }

  function remove(paths: string[]) {
    if (!paths.length || !window.confirm(`Delete ${paths.length} selected item(s)?`)) {
      return;
    }
    void runAction("Entry deleted", () => deleteWorkspaceEntries(actorId, paths));
  }

  function move(paths: string[], destination: string) {
    if (!paths.length) {
      return;
    }
    void runAction("Entry moved", () => moveWorkspaceEntries(actorId, paths, destination));
  }

  function moveSelected() {
    const destination = window.prompt("Destination directory", path);
    if (destination === null) {
      return;
    }
    move(selectedPaths, destination);
  }

  function share(entry: WorkspaceEntry) {
    setPendingShare(entry);
    setShareTtl(DEFAULT_SHARE_TTL);
  }

  async function copyFeedbackUrl(value: string) {
    await copyToClipboard(value);
    setCopiedFeedback(true);
    window.setTimeout(() => setCopiedFeedback(false), 1600);
  }

  function confirmShare() {
    if (pendingShare === null) {
      return;
    }
    const entry = pendingShare;
    void runAction(
      (grant: ShareGrant) => {
        const shareUrl = grant.url ?? `/s/${grant.id}`;
        return { title: "Share created", body: shareUrl, copyText: shareUrl };
      },
      async () => {
        const grant = await createShare(actorId, entry.path, expiresAtForPreset(shareTtl));
        await client.invalidateQueries({ queryKey: ["shares"] });
        setPendingShare(null);
        return grant;
      },
    );
  }

  function dragStart(event: DragEvent<HTMLElement>, entry: WorkspaceEntry) {
    const sources = selected.has(entry.path) ? selectedPaths : [entry.path];
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-yuubot-paths", JSON.stringify(sources));
  }

  function dropOnDirectory(event: DragEvent<HTMLElement>, entry: WorkspaceEntry) {
    event.preventDefault();
    event.stopPropagation();
    const payload = event.dataTransfer.getData("application/x-yuubot-paths");
    if (!payload || entry.kind !== "directory") {
      return;
    }
    move(JSON.parse(payload) as string[], entry.path);
  }

  function dropUpload(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files ?? []);
    if (files.length) {
      uploadFiles(files);
    }
  }

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;

  return (
    <>
      <Panel>
        <div className="grid gap-4" onDragOver={(event) => event.preventDefault()} onDrop={dropUpload}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Breadcrumb path={path} onChange={changePath} />
            <div className="flex flex-wrap items-center gap-2">
              <label className="dense-checkbox">
                <input type="checkbox" checked={showHidden} onChange={(event) => setShowHidden(event.target.checked)} />
                Show hidden
              </label>
              <Button variant="outline" size="sm" onClick={createDirectory} disabled={busy}>
                <FolderPlus size={14} /> New folder
              </Button>
              <label className="inline-flex">
                <input
                  className="hidden"
                  type="file"
                  multiple
                  onChange={(event) => {
                    uploadFiles(Array.from(event.target.files ?? []));
                    event.currentTarget.value = "";
                  }}
                />
                <span className="inline-flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium shadow-xs hover:bg-accent">
                  <Upload size={14} /> Upload
                </span>
              </label>
              <Button variant="outline" size="sm" onClick={moveSelected} disabled={busy || !selected.size}>
                <MoveRight size={14} /> Move
              </Button>
              <Button variant="outline" size="sm" onClick={() => remove(selectedPaths)} disabled={busy || !selected.size}>
                <Trash2 size={14} /> Delete
              </Button>
            </div>
          </div>

          {!entries.length ? (
            <EmptyState>Drop files here or create a folder to start this workspace.</EmptyState>
          ) : !visibleEntries.length ? (
            <EmptyState>No visible files. Check Show hidden to reveal dot-prefixed entries.</EmptyState>
          ) : (
            <div className="data-table">
              <div className="grid grid-cols-[32px_minmax(180px,1fr)_120px_190px_210px] gap-3 px-3 py-2 text-xs uppercase tracking-wide text-muted-foreground">
                <button className="text-left" type="button" onClick={selectAll} aria-label="Toggle all entries">
                  {visibleEntries.length > 0 && visibleEntries.every((entry) => selected.has(entry.path)) ? "All" : "Any"}
                </button>
                <span>Name</span>
                <span>Size</span>
                <span>Modified</span>
                <span>Actions</span>
              </div>
              {visibleEntries.map((entry) => (
                <div
                  key={entry.path}
                  className="grid grid-cols-[32px_minmax(180px,1fr)_120px_190px_210px] items-center gap-3 border-t px-3 py-2"
                  draggable
                  onDragStart={(event) => dragStart(event, entry)}
                  onDragOver={(event) => entry.kind === "directory" && event.preventDefault()}
                  onDrop={(event) => dropOnDirectory(event, entry)}
                >
                  <input type="checkbox" checked={selected.has(entry.path)} onChange={() => toggle(entry.path)} />
                  {entry.kind === "directory" ? (
                    <button
                      className="inline-flex cursor-pointer items-center gap-2 text-left font-medium underline-offset-4 hover:underline"
                      type="button"
                      onClick={() => changePath(entry.path)}
                    >
                      <Folder size={16} />
                      <span>{entry.name}/</span>
                    </button>
                  ) : (
                    <a
                      className="inline-flex items-center gap-2 text-left font-medium underline-offset-4 hover:underline"
                      href={isMarkdownPath(entry.path) ? workspaceFilePreviewUrl(actorId, entry.path) : getActorFileUrl(actorId, entry.path)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <FileIcon size={16} />
                      <span>{entry.name}</span>
                    </a>
                  )}
                  <span className="page-sub">{entry.kind === "file" ? formatSize(entry.size) : "folder"}</span>
                  <span className="page-sub">{formatDate(entry.mtime)}</span>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="xs" onClick={() => share(entry)} disabled={busy}>
                      <Share2 size={12} /> Share
                    </Button>
                    <Button variant="outline" size="xs" onClick={() => rename(entry)} disabled={busy}>
                      <Pencil size={12} /> Rename
                    </Button>
                    <Button variant="outline" size="xs" onClick={() => remove([entry.path])} disabled={busy}>
                      <Trash2 size={12} /> Delete
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Panel>
      <Dialog open={pendingShare !== null} onOpenChange={(open) => !open && !busy && setPendingShare(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Publish share?</DialogTitle>
            <DialogDescription>
              This creates a public snapshot under /s/*. Anyone with the generated URL can read it until the TTL expires.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="rounded-md border bg-muted/30 p-3 text-sm">
              <div className="font-medium">{pendingShare?.path ?? ""}</div>
              <p className="page-sub">{pendingShare?.kind ?? "entry"}</p>
            </div>
            <label className="grid gap-1 text-sm font-medium">
              TTL
              <select className="input" value={shareTtl} onChange={(event) => setShareTtl(event.target.value as ShareTtlPreset)}>
                {SHARE_TTL_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={busy}>Cancel</Button>
            </DialogClose>
            <Button onClick={confirmShare} disabled={busy || pendingShare === null}>Create share</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={feedback !== null} onOpenChange={(open) => {
        if (!open) {
          setFeedback(null);
          setCopiedFeedback(false);
        }
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{feedback?.title ?? ""}</DialogTitle>
            <DialogDescription>{feedback?.body ?? ""}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            {feedback?.copyText && (
              <Button variant="outline" onClick={() => void copyFeedbackUrl(feedback.copyText ?? "")}>
                {copiedFeedback ? <><Check size={14} /> Copied</> : "Copy URL"}
              </Button>
            )}
            <DialogClose asChild>
              <Button>OK</Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function Breadcrumb({ path, onChange }: { path: string; onChange: (path: string) => void }) {
  const parts = path.split("/").filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-1 text-sm">
      <Button variant="ghost" size="xs" onClick={() => onChange("")}>root</Button>
      {parts.map((part, index) => {
        const nextPath = parts.slice(0, index + 1).join("/");
        return (
          <span key={nextPath} className="inline-flex items-center gap-1">
            <span className="page-sub">/</span>
            <Button variant="ghost" size="xs" onClick={() => onChange(nextPath)}>{part}</Button>
          </span>
        );
      })}
    </div>
  );
}

function joinPath(parent: string, child: string): string {
  return [parent, child].filter(Boolean).join("/");
}

function isMarkdownPath(path: string): boolean {
  return /\.(?:md|markdown)$/i.test(path);
}

function workspaceFilePreviewUrl(actorId: string, path: string): string {
  const encodedPath = path.split("/").map((part) => encodeURIComponent(part)).join("/");
  return `/workspace/${encodeURIComponent(actorId)}/file/${encodedPath}`;
}

function formatSize(size: number | undefined): string {
  if (size === undefined) {
    return "-";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString();
}

function expiresAtForPreset(preset: ShareTtlPreset): string | null {
  const option = SHARE_TTL_OPTIONS.find((item) => item.value === preset);
  if (!option || option.ms === null) {
    return null;
  }
  return new Date(Date.now() + option.ms).toISOString();
}

async function copyToClipboard(value: string): Promise<void> {
  await navigator.clipboard.writeText(value);
}

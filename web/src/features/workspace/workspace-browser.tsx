import { useEffect, useMemo, useState, type DragEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Check, Code2, Download, File, FileCog, FileIcon, FileText, Folder, FolderPlus, Grid2X2, Image, List, MoveRight, Pencil, Share2, Trash2, Upload } from "lucide-react";

import type { ShareGrant, WorkspaceEntry } from "@/shared/types/api";
import {
  browseActor,
  createShare,
  createWorkspaceDirectory,
  deleteWorkspaceEntries,
  downloadWorkspaceEntries,
  getActorFileUrl,
  getActorFileDownloadUrl,
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
const LARGE_FILE_BYTES = 10 * 1024 * 1024;
type ViewMode = "list" | "grid";
const SHARE_TTL_OPTIONS: Array<{ value: ShareTtlPreset; label: string; ms: number | null }> = [
  { value: "1h", label: "1 hour", ms: 60 * 60 * 1000 },
  { value: "24h", label: "24 hours", ms: 24 * 60 * 60 * 1000 },
  { value: "7d", label: "7 days", ms: 7 * 24 * 60 * 60 * 1000 },
  { value: "never", label: "Never", ms: null },
];

export function WorkspaceBrowser({ actorId, path, onPathChange }: { actorId: string; path: string; onPathChange: (path: string) => void }) {
  const client = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectionMode, setSelectionMode] = useState(false);
  const [view, setView] = useState<ViewMode>(() => (localStorage.getItem("workspace-view") as ViewMode | null) ?? "list");
  const [largeFile, setLargeFile] = useState<WorkspaceEntry | null>(null);
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
    setSelected(new Set());
    setFeedback(null);
    setCopiedFeedback(false);
    setPendingShare(null);
    setShareTtl(DEFAULT_SHARE_TTL);
  }, [actorId, path]);

  function changePath(nextPath: string) {
    setSelected(new Set());
    setSelectionMode(false);
    onPathChange(nextPath);
  }

  function changeView(next: ViewMode) {
    setView(next);
    localStorage.setItem("workspace-view", next);
  }

  function openFile(entry: WorkspaceEntry) {
    if ((entry.size ?? 0) > LARGE_FILE_BYTES) {
      setLargeFile(entry);
      return;
    }
    window.open(fileOpenUrl(actorId, entry.path, entry.mime), "_blank", "noopener,noreferrer");
  }

  function download(entry: WorkspaceEntry) {
    const link = document.createElement("a");
    link.href = getActorFileDownloadUrl(actorId, entry.path);
    link.download = entry.name;
    link.click();
  }

  function downloadSelected() {
    const chosen = visibleEntries.filter((entry) => selected.has(entry.path));
    if (chosen.length === 1 && chosen[0].kind === "file") {
      download(chosen[0]);
      return;
    }
    void runAction("Download prepared", () => downloadWorkspaceEntries(actorId, selectedPaths));
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
              <div className="inline-flex rounded-md border p-0.5">
                <Button variant={view === "list" ? "secondary" : "ghost"} size="xs" onClick={() => changeView("list")} aria-label="List view"><List size={14} /></Button>
                <Button variant={view === "grid" ? "secondary" : "ghost"} size="xs" onClick={() => changeView("grid")} aria-label="Card view"><Grid2X2 size={14} /></Button>
              </div>
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
              <Button variant={selectionMode ? "secondary" : "outline"} size="sm" onClick={() => { setSelectionMode(!selectionMode); setSelected(new Set()); }}>
                <Check size={14} /> {selectionMode ? "Exit selection" : "Select"}
              </Button>
            </div>
          </div>

          {selectionMode && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2">
              <Button variant="ghost" size="sm" onClick={selectAll}>{selected.size === visibleEntries.length ? "Clear all" : "Select all"}</Button>
              <span className="mr-auto text-sm text-muted-foreground">{selected.size} selected</span>
              <Button variant="outline" size="sm" onClick={downloadSelected} disabled={busy || !selected.size}><Download size={14} /> Download</Button>
              <Button variant="outline" size="sm" onClick={moveSelected} disabled={busy || !selected.size}><MoveRight size={14} /> Move</Button>
              <Button variant="outline" size="sm" onClick={() => remove(selectedPaths)} disabled={busy || !selected.size}><Trash2 size={14} /> Delete</Button>
            </div>
          )}

          {!entries.length ? (
            <EmptyState>Drop files here or create a folder to start this workspace.</EmptyState>
          ) : !visibleEntries.length ? (
            <EmptyState>No visible files. Check Show hidden to reveal dot-prefixed entries.</EmptyState>
          ) : view === "list" ? (
            <div className="data-table">
              <div className={`grid ${selectionMode ? "grid-cols-[32px_minmax(180px,1fr)_100px_170px_152px]" : "grid-cols-[minmax(180px,1fr)_100px_170px_152px]"} gap-3 px-3 py-2 text-xs uppercase tracking-wide text-muted-foreground`}>
                {selectionMode && <span />}
                <span>Name</span>
                <span>Size</span>
                <span>Modified</span>
                <span>Actions</span>
              </div>
              {visibleEntries.map((entry) => (
                <div
                  key={entry.path}
                  className={`grid ${selectionMode ? "grid-cols-[32px_minmax(180px,1fr)_100px_170px_152px]" : "grid-cols-[minmax(180px,1fr)_100px_170px_152px]"} items-center gap-3 border-t px-3 py-2`}
                  draggable
                  onDragStart={(event) => dragStart(event, entry)}
                  onDragOver={(event) => entry.kind === "directory" && event.preventDefault()}
                  onDrop={(event) => dropOnDirectory(event, entry)}
                >
                  {selectionMode && <input type="checkbox" checked={selected.has(entry.path)} onChange={() => toggle(entry.path)} />}
                  {entry.kind === "directory" ? (
                    <button
                      className="inline-flex cursor-pointer items-center gap-2 text-left font-medium underline-offset-4 hover:underline"
                      type="button"
                      onClick={() => changePath(entry.path)}
                    >
                      <FileVisual entry={entry} size={16} />
                      <span>{entry.name}/</span>
                    </button>
                  ) : (
                    <button
                      className="inline-flex items-center gap-2 text-left font-medium underline-offset-4 hover:underline"
                      type="button"
                      onClick={() => openFile(entry)}
                    >
                      <FileVisual entry={entry} size={16} />
                      <span>{entry.name}</span>
                    </button>
                  )}
                  <span className="page-sub">{entry.kind === "file" ? formatSize(entry.size) : "folder"}</span>
                  <span className="page-sub">{formatDate(entry.mtime)}</span>
                  <div className="flex flex-wrap gap-2">
                    {entry.kind === "file" && <Button variant="outline" size="icon-xs" onClick={() => download(entry)} disabled={busy} aria-label={`Download ${entry.name}`} title="Download"><Download size={12} /></Button>}
                    <Button variant="outline" size="icon-xs" onClick={() => share(entry)} disabled={busy} aria-label={`Share ${entry.name}`} title="Share">
                      <Share2 size={12} />
                    </Button>
                    <Button variant="outline" size="icon-xs" onClick={() => rename(entry)} disabled={busy} aria-label={`Rename ${entry.name}`} title="Rename">
                      <Pencil size={12} />
                    </Button>
                    <Button variant="outline" size="icon-xs" onClick={() => remove([entry.path])} disabled={busy} aria-label={`Delete ${entry.name}`} title="Delete">
                      <Trash2 size={12} />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {visibleEntries.map((entry) => (
                <div key={entry.path} className={`group relative grid min-h-44 content-between rounded-md border p-3 hover:bg-accent/40 ${selected.has(entry.path) ? "ring-2 ring-primary" : ""}`}>
                  {selectionMode && <input className="absolute left-3 top-3 z-10" type="checkbox" checked={selected.has(entry.path)} onChange={() => toggle(entry.path)} />}
                  <button className="grid min-w-0 gap-3 text-left" type="button" onClick={() => entry.kind === "directory" ? changePath(entry.path) : openFile(entry)}>
                    <div className="flex h-24 items-center justify-center overflow-hidden rounded-md bg-muted/50">
                      {entry.kind === "file" && entry.mime?.startsWith("image/") ? <img className="h-full w-full object-cover" src={getActorFileUrl(actorId, entry.path)} alt="" loading="lazy" /> : <FileVisual entry={entry} size={48} />}
                    </div>
                    <span className="truncate text-sm font-medium" title={entry.name}>{entry.name}{entry.kind === "directory" ? "/" : ""}</span>
                  </button>
                  <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>{entry.kind === "file" ? formatSize(entry.size) : "folder"}</span>
                    <div className="flex items-center">
                      {entry.kind === "file" && <Button variant="ghost" size="icon-xs" onClick={() => download(entry)} aria-label={`Download ${entry.name}`} title="Download"><Download size={12} /></Button>}
                      <Button variant="ghost" size="icon-xs" onClick={() => share(entry)} aria-label={`Share ${entry.name}`} title="Share"><Share2 size={12} /></Button>
                      <Button variant="ghost" size="icon-xs" onClick={() => rename(entry)} aria-label={`Rename ${entry.name}`} title="Rename"><Pencil size={12} /></Button>
                      <Button variant="ghost" size="icon-xs" onClick={() => remove([entry.path])} aria-label={`Delete ${entry.name}`} title="Delete"><Trash2 size={12} /></Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Panel>
      <Dialog open={largeFile !== null} onOpenChange={(open) => !open && setLargeFile(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Open large file?</DialogTitle><DialogDescription>{largeFile?.name} is {formatSize(largeFile?.size)}. Opening it may use significant memory or bandwidth.</DialogDescription></DialogHeader>
          <DialogFooter>
            <DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose>
            <Button variant="outline" onClick={() => { if (largeFile) download(largeFile); setLargeFile(null); }}><Download size={14} /> Download</Button>
            <Button onClick={() => { if (largeFile) window.open(fileOpenUrl(actorId, largeFile.path, largeFile.mime), "_blank", "noopener,noreferrer"); setLargeFile(null); }}>Open anyway</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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

function fileOpenUrl(actorId: string, path: string, mime = ""): string {
  return isMarkdownPath(path) || isEditableTextPath(path, mime) ? workspaceFilePreviewUrl(actorId, path) : getActorFileUrl(actorId, path);
}

function isEditableTextPath(path: string, mime: string): boolean {
  if (mime.startsWith("text/")) return true;
  if (["application/json", "application/javascript", "application/xml", "application/yaml", "application/toml"].some((value) => mime.startsWith(value))) return true;
  return /\.(?:cfg|conf|css|env|ini|js|json|jsx|lock|log|md|markdown|py|sh|toml|ts|tsx|txt|xml|yaml|yml)$/i.test(path);
}

function FileVisual({ entry, size }: { entry: WorkspaceEntry; size: number }) {
  const common = { size, strokeWidth: 1.7 };
  if (entry.kind === "directory") return <Folder {...common} className="text-sky-600 dark:text-sky-400" />;
  const suffix = entry.name.toLowerCase().split(".").pop() ?? "";
  if (isMarkdownPath(entry.path)) return <FileText {...common} className="text-violet-600 dark:text-violet-400" />;
  if (entry.mime?.startsWith("image/")) return <Image {...common} className="text-emerald-600 dark:text-emerald-400" />;
  if (entry.mime === "application/pdf") return <FileText {...common} className="text-rose-600 dark:text-rose-400" />;
  if (["zip", "gz", "tgz", "rar", "7z", "tar"].includes(suffix)) return <Archive {...common} className="text-amber-600 dark:text-amber-400" />;
  if (["py", "js", "jsx", "ts", "tsx", "css", "html", "sh", "rs", "go"].includes(suffix)) return <Code2 {...common} className="text-blue-600 dark:text-blue-400" />;
  if (["json", "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "lock"].includes(suffix)) return <FileCog {...common} className="text-orange-600 dark:text-orange-400" />;
  if (entry.mime?.startsWith("text/")) return <FileIcon {...common} className="text-slate-600 dark:text-slate-300" />;
  return <File {...common} className="text-muted-foreground" />;
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

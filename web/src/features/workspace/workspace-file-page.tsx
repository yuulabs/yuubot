import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, Save, X } from "lucide-react";

import { MarkdownRenderer } from "@/components/conversation/markdown-renderer.tsx";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/shared/components";
import { getActorFileContent, getActorFileDownloadUrl, getActorFileMetadata, getActorFileUrl, putActorFileContent } from "@/shared/lib/api";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const LARGE_FILE_BYTES = 10 * 1024 * 1024;
type ViewMode = "rendered" | "raw" | "edit";

export function WorkspaceFilePage({ actorId, path }: { actorId: string; path: string }) {
  const name = path.split("/").pop() || path;
  const client = useQueryClient();
  const metadata = useQuery({ queryKey: ["actor-workspace-file-metadata", actorId, path], queryFn: () => getActorFileMetadata(actorId, path) });
  const [confirmed, setConfirmed] = useState(false);
  const [view, setView] = useState<ViewMode>("rendered");
  const [draft, setDraft] = useState("");
  const mayRead = metadata.data !== undefined && (metadata.data.size <= LARGE_FILE_BYTES || confirmed);
  const query = useQuery({
    queryKey: ["actor-workspace-file", actorId, path],
    queryFn: () => getActorFileContent(actorId, path),
    enabled: mayRead,
  });
  const isMarkdown = /\.(?:md|markdown)$/i.test(path);
  const dirty = query.data !== undefined && draft !== query.data.content;
  const save = useMutation({
    mutationFn: async () => {
      if (!query.data?.etag) throw new Error("This file has no version identifier. Reload it before saving.");
      return putActorFileContent(actorId, path, draft, query.data.etag);
    },
    onSuccess: async (result) => {
      client.setQueryData(["actor-workspace-file", actorId, path], {
        ...query.data,
        ...result,
        content: draft,
      });
      client.setQueryData(["actor-workspace-file-metadata", actorId, path], result);
      await client.invalidateQueries({ queryKey: ["actor-workspace", actorId] });
      setView(isMarkdown ? "rendered" : "raw");
    },
  });

  useEffect(() => {
    const previousTitle = document.title;
    document.title = name;
    return () => { document.title = previousTitle; };
  }, [name]);

  useEffect(() => {
    if (query.data) setDraft(query.data.content);
  }, [query.data?.etag]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (metadata.isLoading) return <LoadingState />;
  if (metadata.error) return <ErrorState error={metadata.error} />;
  if (metadata.data && metadata.data.size > LARGE_FILE_BYTES && !confirmed) return (
    <Dialog open>
      <DialogContent>
        <DialogHeader><DialogTitle>Open large Markdown file?</DialogTitle><DialogDescription>This file is {formatSize(metadata.data.size)}. Confirm before loading and rendering its contents.</DialogDescription></DialogHeader>
        <DialogFooter>
          <Button variant="outline" asChild><a href={workspaceUrl(actorId, path)}>Cancel</a></Button>
          <Button variant="outline" asChild><a href={getActorFileDownloadUrl(actorId, path)}><Download size={14} /> Download</a></Button>
          <Button onClick={() => setConfirmed(true)}>Open anyway</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
  if (query.isLoading || !mayRead) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!query.data) return <ErrorState error={new Error("File content is unavailable")} />;

  return (
    <main className="min-h-screen bg-background px-5 py-8 text-foreground sm:px-8 sm:py-12">
      <div className="sticky top-4 z-10 mx-auto mb-6 flex max-w-4xl justify-end">
        <div className="flex rounded-md border bg-background/95 p-1 shadow-sm backdrop-blur">
          {isMarkdown && <Button variant={view === "rendered" ? "secondary" : "ghost"} size="sm" onClick={() => setView("rendered")}>Rendered</Button>}
          <Button variant={view === "raw" ? "secondary" : "ghost"} size="sm" onClick={() => setView("raw")}>
            Raw
          </Button>
          <Button variant={view === "edit" ? "secondary" : "ghost"} size="sm" disabled={metadata.data.size > LARGE_FILE_BYTES} onClick={() => setView("edit")}>
            Edit
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <a href={getActorFileUrl(actorId, path)} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> Open in browser</a>
          </Button>
          <Button variant="ghost" size="sm" asChild><a href={getActorFileDownloadUrl(actorId, path)}><Download size={14} /> Download</a></Button>
        </div>
      </div>
      <article className={view === "edit" ? "mx-auto max-w-6xl" : "mx-auto max-w-4xl"}>
        {view === "edit" ? (
          <div className="grid gap-3">
            <textarea
              className="textarea min-h-[70vh] w-full font-mono text-sm leading-6"
              aria-label={`Edit ${name}`}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              spellCheck={false}
              autoFocus
            />
            <div className="sticky bottom-4 flex items-center justify-end gap-2 rounded-md border bg-background/95 p-2 shadow-sm backdrop-blur">
              {save.error && <p className="mr-auto text-sm text-destructive">{save.error instanceof Error ? save.error.message : String(save.error)}</p>}
              <Button variant="outline" disabled={save.isPending} onClick={() => { setDraft(query.data.content); setView(isMarkdown ? "rendered" : "raw"); }}><X size={14} /> Cancel</Button>
              <Button disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save size={14} /> {save.isPending ? "Saving…" : "Save"}</Button>
            </div>
          </div>
        ) : view === "rendered" && isMarkdown ? (
          <MarkdownRenderer actorId={actorId} workspacePath={path} content={query.data.content} />
        ) : (
          <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-sm leading-6">{query.data.content}</pre>
        )}
      </article>
    </main>
  );
}

function workspaceUrl(actorId: string, path: string): string {
  const parent = path.split("/").slice(0, -1).join("/");
  return `/workspace?actor=${encodeURIComponent(actorId)}&path=${encodeURIComponent(parent)}`;
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

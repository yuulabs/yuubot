import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, FileText, ExternalLink } from "lucide-react";

import { MarkdownRenderer } from "@/components/conversation/markdown-renderer.tsx";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, Page, Panel } from "@/shared/components";
import { getActorFileContent, getActorFileDownloadUrl, getActorFileMetadata, getActorFileUrl } from "@/shared/lib/api";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const LARGE_FILE_BYTES = 10 * 1024 * 1024;

export function WorkspaceFilePage({ actorId, path }: { actorId: string; path: string }) {
  const metadata = useQuery({ queryKey: ["actor-workspace-file-metadata", actorId, path], queryFn: () => getActorFileMetadata(actorId, path) });
  const [confirmed, setConfirmed] = useState(false);
  const mayRead = metadata.data !== undefined && (metadata.data.size <= LARGE_FILE_BYTES || confirmed);
  const query = useQuery({
    queryKey: ["actor-workspace-file", actorId, path],
    queryFn: () => getActorFileContent(actorId, path),
    enabled: mayRead,
  });

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

  const file = query.data;
  const name = path.split("/").pop() || path;
  const parentUrl = workspaceUrl(actorId, path);

  return (
    <Page
      title={name}
      sub={path}
      actions={
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" asChild>
            <a href={parentUrl}><ArrowLeft size={14} /> Workspace</a>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <a href={getActorFileUrl(actorId, path)} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Raw</a>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <a href={getActorFileDownloadUrl(actorId, path)}><Download size={14} /> Download</a>
          </Button>
        </div>
      }
    >
      <Panel>
        <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-1 border-b pb-4 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5"><FileText size={14} /> Markdown</span>
          {file.size > 0 && <span>{formatSize(file.size)}</span>}
          {file.mtime && <span>{formatDate(file.mtime)}</span>}
        </div>
        <article className="mx-auto max-w-4xl">
          <MarkdownRenderer actorId={actorId} workspacePath={path} content={file.content} />
        </article>
      </Panel>
    </Page>
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

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

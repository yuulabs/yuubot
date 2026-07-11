import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, FileText, ExternalLink } from "lucide-react";

import { MarkdownRenderer } from "@/components/conversation/markdown-renderer.tsx";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, Page, Panel } from "@/shared/components";
import { getActorFileContent, getActorFileUrl } from "@/shared/lib/api";

export function WorkspaceFilePage({ actorId, path }: { actorId: string; path: string }) {
  const query = useQuery({
    queryKey: ["actor-workspace-file", actorId, path],
    queryFn: () => getActorFileContent(actorId, path),
  });

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} />;
  if (!query.data) return <ErrorState error={new Error("File content is unavailable")} />;

  const file = query.data;
  const name = path.split("/").pop() || path;
  const workspaceUrl = `/workspace?actor=${encodeURIComponent(actorId)}`;

  return (
    <Page
      title={name}
      sub={path}
      actions={
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" asChild>
            <a href={workspaceUrl} target="_blank" rel="noreferrer"><ArrowLeft size={14} /> Workspace</a>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <a href={getActorFileUrl(actorId, path)} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Raw</a>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <a href={getActorFileUrl(actorId, path)} target="_blank" rel="noreferrer" download={name}><Download size={14} /> Download</a>
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

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

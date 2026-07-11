import { useNavigate } from "@tanstack/react-router";

import { Page, EmptyState, ErrorState, LoadingState } from "@/shared/components";
import { useBootstrap } from "@/shared/hooks";
import { WorkspaceBrowser } from "./workspace-browser";
import { Route } from "@/routes/workspace";

export function WorkspacePage() {
  const { data, error, isLoading } = useBootstrap();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/workspace" });

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;

  const actors = data?.actors ?? [];
  const selectedActor = actors.find((actor) => actor.id === search.actor) ?? actors[0];

  function selectActor(nextActorId: string) {
    void navigate({ search: { actor: nextActorId, path: "" } });
  }

  return (
    <Page
      title="Workspace"
      sub="Browse actor workspaces, open raw files, share snapshots, and manage files."
      actions={
        <select
          className="input min-w-48"
          value={selectedActor?.id ?? ""}
          onChange={(event) => selectActor(event.target.value)}
          disabled={!actors.length}
        >
          {actors.map((actor) => (
            <option key={actor.id} value={actor.id}>{actor.name || actor.id}</option>
          ))}
        </select>
      }
    >
      {!selectedActor ? (
        <EmptyState>No actors have been configured.</EmptyState>
      ) : (
        <WorkspaceBrowser
          actorId={selectedActor.id}
          path={search.path}
          onPathChange={(path) => void navigate({ search: { actor: selectedActor.id, path } })}
        />
      )}
    </Page>
  );
}

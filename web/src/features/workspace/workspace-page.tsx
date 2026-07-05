import { useState } from "react";

import { Page, EmptyState, ErrorState, LoadingState, ResourceMeta } from "@/shared/components";
import { useBootstrap } from "@/shared/hooks";
import { WorkspaceBrowser } from "./workspace-browser";

export function WorkspacePage() {
  const { data, error, isLoading } = useBootstrap();
  const [actorId, setActorId] = useState(() => {
    if (typeof window === "undefined") {
      return "";
    }
    return new URLSearchParams(window.location.search).get("actor") ?? "";
  });

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;

  const actors = data?.actors ?? [];
  const selectedActor = actors.find((actor) => actor.id === actorId) ?? actors[0];

  function selectActor(nextActorId: string) {
    setActorId(nextActorId);
    const url = new URL(window.location.href);
    url.searchParams.set("actor", nextActorId);
    window.history.replaceState(null, "", url);
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
        <div className="grid gap-3">
          <ResourceMeta
            items={[
              { label: "Actor", value: selectedActor.name || selectedActor.id },
              { label: "Workspace", value: selectedActor.workspace },
              { label: "Status", value: selectedActor.status, tone: selectedActor.enabled ? "ok" : "muted" },
            ]}
          />
          <WorkspaceBrowser actorId={selectedActor.id} />
        </div>
      )}
    </Page>
  );
}

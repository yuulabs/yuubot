import { Link, Outlet, useRouterState } from "@tanstack/react-router";

import { deleteActor, disableActor, enableActor } from "@/shared/lib/api";
import { Button } from "@/components/ui/button";
import {
  DeleteButton,
  EmptyState,
  ErrorState,
  LoadingState,
  Page,
  ResourceCard,
  ResourceCardGrid,
  ResourceMeta,
  Status,
} from "@/shared/components";
import { useApiMutation, useBootstrap } from "@/shared/hooks";
import { formatModelSelector } from "./model-selector";

export function ActorsListPage() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname !== "/actors") {
    return <Outlet />;
  }
  const { data, error, isLoading } = useBootstrap();
  const enable = useApiMutation((actorId: string) => enableActor(actorId));
  const disable = useApiMutation((actorId: string) => disableActor(actorId));
  const remove = useApiMutation((actorId: string) => deleteActor(actorId));

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  const actors = data?.actors ?? [];

  return (
    <Page
      title="Actors"
      sub="Always-on identities that bind an LLM, model, workspace, and inbound routes."
      actions={
        <Button asChild>
          <Link to="/actors/new">New Actor</Link>
        </Button>
      }
    >
      {!actors.length ? <EmptyState>No actors configured.</EmptyState> : (
        <ResourceCardGrid>
          {actors.map((actor) => (
            <ResourceCard
              key={actor.id}
              variant="actor"
              label={actor.id}
              title={<Link className="font-medium underline-offset-4 hover:underline" to="/actors/$id" params={{ id: actor.id }}>{actor.name || actor.id}</Link>}
              subtitle={actor.description || "No description"}
              status={<Status enabled={actor.enabled} label={actor.status} />}
              actions={
                <>
                  <Button variant="outline" size="sm" asChild>
                    <Link to="/admin/conversations/new" search={{ actor: actor.id }}>Chat</Link>
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => (actor.enabled ? disable.mutate(actor.id) : enable.mutate(actor.id))}>
                    {actor.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Button variant="outline" size="sm" asChild>
                    <Link to="/actors/$id/edit" params={{ id: actor.id }}>Edit</Link>
                  </Button>
                  <DeleteButton onDelete={() => remove.mutate(actor.id)} />
                </>
              }
            >
              <ResourceMeta
                items={[
                  { label: "Model", value: formatModelSelector(actor.model), tone: actor.model ? "default" : "warning" },
                  { label: "Workspace", value: actor.workspace || actor.id, tone: "default" },
                  { label: "Health", value: actor.last_error ? "error" : "ready", tone: actor.last_error ? "warning" : "ok" },
                ]}
              />
              {actor.last_error && <pre className="resource-preview">{String(actor.last_error)}</pre>}
            </ResourceCard>
          ))}
        </ResourceCardGrid>
      )}
    </Page>
  );
}

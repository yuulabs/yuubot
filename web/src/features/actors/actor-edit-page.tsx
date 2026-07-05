import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import { getActor, putActor } from "@/shared/lib/api";
import { EmptyState, ErrorState, LoadingState, Page } from "@/shared/components";
import { useBootstrap } from "@/shared/hooks";
import { ActorForm } from "./actor-form";

export function ActorEditPage({ id }: { id: string }) {
  const navigate = useNavigate();
  const actor = useQuery({ queryKey: ["actor", id], queryFn: () => getActor(id) });
  const bootstrap = useBootstrap();
  if (actor.isLoading || bootstrap.isLoading) return <LoadingState />;
  if (actor.error) return <ErrorState error={actor.error} />;
  if (bootstrap.error) return <ErrorState error={bootstrap.error} />;
  if (!actor.data || !bootstrap.data) return <EmptyState>Actor not found.</EmptyState>;
  return (
    <Page title={`Edit ${actor.data.name || actor.data.id}`} sub="Edit the full ActorRecord loaded from the backend.">
      <ActorForm
        initial={actor.data}
        bootstrap={bootstrap.data}
        saveLabel="Save Actor"
        onSave={async (actor) => {
          await putActor(actor);
          await navigate({ to: "/actors/$id", params: { id: actor.id } });
        }}
      />
    </Page>
  );
}

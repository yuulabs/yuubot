import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";

import { getActor, putActor } from "@/shared/lib/api";
import { EmptyState, ErrorState, LoadingState, Page } from "@/shared/components";
import { useBootstrap, useSetBootstrapSnapshot } from "@/shared/hooks";
import type { ActorRecord } from "@/shared/types/api";
import { ActorForm } from "./actor-form";

export function ActorEditPage({ id }: { id: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const setBootstrapSnapshot = useSetBootstrapSnapshot();
  const actor = useQuery({ queryKey: ["actor", id], queryFn: () => getActor(id) });
  const bootstrap = useBootstrap();
  const [draft, setDraft] = useState<ActorRecord | null>(null);

  useEffect(() => {
    if (actor.data) {
      setDraft(actor.data);
    }
  }, [actor.data]);

  if ((actor.isLoading && !draft) || (bootstrap.isLoading && !bootstrap.data)) {
    return <LoadingState />;
  }
  if (actor.isError && !draft) {
    return <ErrorState error={actor.error} />;
  }
  if (bootstrap.isError && !bootstrap.data) {
    return <ErrorState error={bootstrap.error} />;
  }
  if (!draft || !bootstrap.data) {
    return <EmptyState>Actor not found.</EmptyState>;
  }

  return (
    <Page title={`Edit ${draft.name || draft.id}`} sub="Edit the full ActorRecord loaded from the backend.">
      <ActorForm
        initial={draft}
        bootstrap={bootstrap.data}
        saveLabel="Save Actor"
        onSave={async (record) => {
          const snapshot = await putActor(record);
          setBootstrapSnapshot(snapshot);
          client.setQueryData(["actor", record.id], record);
          if (record.id !== id) {
            client.removeQueries({ queryKey: ["actor", id] });
          }
          await navigate({ to: "/actors/$id", params: { id: record.id } });
        }}
      />
    </Page>
  );
}

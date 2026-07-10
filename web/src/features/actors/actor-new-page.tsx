import { useNavigate } from "@tanstack/react-router";

import { putActor } from "@/shared/lib/api";
import type { ActorRecord } from "@/shared/types/api";
import { ErrorState, LoadingState, Page } from "@/shared/components";
import { useBootstrap, useRefreshBootstrap } from "@/shared/hooks";
import { ActorForm } from "./actor-form";

export function ActorNewPage() {
  const navigate = useNavigate();
  const refreshBootstrap = useRefreshBootstrap();
  const { data, error, isLoading } = useBootstrap();
  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  const initialActor: ActorRecord = {
    id: "amy",
    name: "Amy",
    description: "Default assistant",
    workspace: "amy",
    persona: "",
    model: { type: "alias", alias: "" },
    context_compression_tokens: 262144,
  };
  return (
    <Page title="New Actor" sub="Create an ActorRecord directly against the new backend API.">
      {data && <ActorForm
        initial={initialActor}
        bootstrap={data}
        saveLabel="Create Actor"
        onSave={async (actorId, input) => {
          await putActor(actorId, input);
          refreshBootstrap();
          await navigate({ to: "/actors/$id", params: { id: actorId } });
        }}
      />}
    </Page>
  );
}

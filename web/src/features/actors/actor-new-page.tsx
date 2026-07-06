import { useNavigate } from "@tanstack/react-router";

import { putActor } from "@/shared/lib/api";
import type { ActorRecord } from "@/shared/types/api";
import { ErrorState, LoadingState, Page } from "@/shared/components";
import { useBootstrap, useSetBootstrapSnapshot } from "@/shared/hooks";
import { ActorForm } from "./actor-form";

export function ActorNewPage() {
  const navigate = useNavigate();
  const setBootstrapSnapshot = useSetBootstrapSnapshot();
  const { data, error, isLoading } = useBootstrap();
  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  const firstProvider = data?.providers[0]?.id ?? "";
  const initialActor: ActorRecord = {
    id: "amy",
    name: "Amy",
    description: "Default assistant",
    workspace: "amy",
    persona: "",
    model: { selector: "", toolcall: true, json: true },
    provider: firstProvider,
  };
  return (
    <Page title="New Actor" sub="Create an ActorRecord directly against the new backend API.">
      {data && <ActorForm
        initial={initialActor}
        bootstrap={data}
        saveLabel="Create Actor"
        onSave={async (actor) => {
          const snapshot = await putActor(actor);
          setBootstrapSnapshot(snapshot);
          await navigate({ to: "/actors/$id", params: { id: actor.id } });
        }}
      />}
    </Page>
  );
}

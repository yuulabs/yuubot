import { createFileRoute } from "@tanstack/react-router";

import { ActorEditPage } from "@/features/actors";

export const Route = createFileRoute("/actors/$id/edit")({
  component: () => {
    const { id } = Route.useParams();
    return <ActorEditPage id={id} />;
  },
});

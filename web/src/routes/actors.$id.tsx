import { createFileRoute } from "@tanstack/react-router";

import { ActorDetailPage } from "@/features/actors";

export const Route = createFileRoute("/actors/$id")({
  component: () => {
    const { id } = Route.useParams();
    return <ActorDetailPage id={id} />;
  },
});

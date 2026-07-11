import { createFileRoute } from "@tanstack/react-router";

import { WorkspaceFilePage } from "@/features/workspace";

export const Route = createFileRoute("/workspace/$actorId/file/$")({
  component: () => {
    const { actorId, _splat } = Route.useParams();
    return <WorkspaceFilePage actorId={actorId} path={_splat ?? ""} />;
  },
});

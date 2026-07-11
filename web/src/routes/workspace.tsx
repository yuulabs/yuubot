import { createFileRoute } from "@tanstack/react-router";

import { WorkspacePage } from "@/features/workspace";

export const Route = createFileRoute("/workspace")({
  validateSearch: (search: Record<string, unknown>) => ({
    actor: typeof search.actor === "string" ? search.actor : "",
    path: typeof search.path === "string" ? search.path : "",
  }),
  component: WorkspacePage,
});

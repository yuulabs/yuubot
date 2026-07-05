import { createFileRoute } from "@tanstack/react-router";

import { WorkspacePage } from "@/features/workspace";

export const Route = createFileRoute("/workspace")({
  component: WorkspacePage,
});

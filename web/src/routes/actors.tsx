import { createFileRoute } from "@tanstack/react-router";

import { ActorsListPage } from "@/features/actors";

export const Route = createFileRoute("/actors")({
  component: ActorsListPage,
});

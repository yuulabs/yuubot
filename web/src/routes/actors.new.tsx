import { createFileRoute } from "@tanstack/react-router";

import { ActorNewPage } from "@/features/actors";

export const Route = createFileRoute("/actors/new")({
  component: ActorNewPage,
});

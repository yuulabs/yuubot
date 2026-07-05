import { createFileRoute } from "@tanstack/react-router";

import { ProvidersListPage } from "@/features/providers";

export const Route = createFileRoute("/providers")({
  component: ProvidersListPage,
});

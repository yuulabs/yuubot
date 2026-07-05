import { createFileRoute } from "@tanstack/react-router";

import { IntegrationsListPage } from "@/features/integrations";

export const Route = createFileRoute("/integrations")({
  component: IntegrationsListPage,
});

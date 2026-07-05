import { createFileRoute } from "@tanstack/react-router";

import { IntegrationDetailPage } from "@/features/integrations";

export const Route = createFileRoute("/integrations/$id")({
  component: () => {
    const { id } = Route.useParams();
    return <IntegrationDetailPage id={id} />;
  },
});

import { createFileRoute } from "@tanstack/react-router";

import { ProviderDetailPage } from "@/features/providers";

export const Route = createFileRoute("/providers/$id")({
  component: () => {
    const { id } = Route.useParams();
    return <ProviderDetailPage id={id} />;
  },
});

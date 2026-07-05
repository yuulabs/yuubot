import { createFileRoute } from "@tanstack/react-router";

import { IngressRoutesPage } from "@/features/ingress-routes/ingress-routes-page";

export const Route = createFileRoute("/routes")({
  component: IngressRoutesPage,
});

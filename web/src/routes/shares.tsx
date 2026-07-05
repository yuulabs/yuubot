import { createFileRoute } from "@tanstack/react-router";

import { SharesPage } from "@/features/shares/shares-page";

export const Route = createFileRoute("/shares")({
  component: SharesPage,
});

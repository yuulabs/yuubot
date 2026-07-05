import { createRootRoute } from "@tanstack/react-router";

import { AppLayout } from "@/features/shell/app-layout";

export const Route = createRootRoute({
  component: AppLayout,
});

import { createFileRoute } from "@tanstack/react-router";

import { CronPage } from "@/features/cron";

export const Route = createFileRoute("/cron")({
  component: CronPage,
});

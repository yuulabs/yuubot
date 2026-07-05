import { createFileRoute } from "@tanstack/react-router";

import { CronNewPage } from "@/features/cron";

export const Route = createFileRoute("/cron/new")({
  component: CronNewPage,
});

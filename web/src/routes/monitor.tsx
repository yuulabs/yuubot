import { createFileRoute } from "@tanstack/react-router";

import { MonitorPage } from "@/features/monitor/monitor-page";

export const Route = createFileRoute("/monitor")({
  component: MonitorPage,
});

import { createFileRoute } from "@tanstack/react-router";

import { CronDetailPage } from "@/features/cron";

export const Route = createFileRoute("/cron/$jobId")({
  component: CronJobRoute,
});

function CronJobRoute() {
  const { jobId } = Route.useParams();
  return <CronDetailPage jobId={jobId} />;
}

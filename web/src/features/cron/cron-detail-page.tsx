import { Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Pause, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { deleteCronJob, getCronJob, pauseCronJob, resumeCronJob } from "@/shared/lib/api";
import { DeleteButton, DenseMeta, DenseSection, ErrorState, LoadingState, Page, Status } from "@/shared/components";
import type { CronJobRecord } from "@/shared/types/api";
import {
  actionDetailText,
  actionIntro,
  actionKindLabel,
  formatCronTime,
  lifecycleLabel,
  parseCronOwner,
  scheduleSummary,
} from "./cron-shared";

export function CronDetailPage({ jobId }: { jobId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: ["cron-job", jobId],
    queryFn: () => getCronJob(jobId),
    refetchInterval: 10_000,
  });
  const pause = useMutation({
    mutationFn: () => pauseCronJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["cron-job", jobId] });
      await queryClient.invalidateQueries({ queryKey: ["cron-jobs"] });
    },
  });
  const resume = useMutation({
    mutationFn: () => resumeCronJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["cron-job", jobId] });
      await queryClient.invalidateQueries({ queryKey: ["cron-jobs"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => deleteCronJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["cron-jobs"] });
      await navigate({ to: "/cron" });
    },
  });

  if (job.isLoading) return <LoadingState />;
  if (job.error) return <ErrorState error={job.error} />;
  if (!job.data) return <ErrorState error={new Error("Cron job not found")} />;

  return (
    <Page
      title={job.data.name}
      sub={job.data.id}
      actions={
        <>
          <Button variant="outline" asChild>
            <Link to="/cron">
              <ArrowLeft size={16} />
              <span>Back</span>
            </Link>
          </Button>
          {job.data.status === "active" ? (
            <Button variant="outline" onClick={() => pause.mutate()}>
              <Pause size={16} />
              <span>Pause</span>
            </Button>
          ) : job.data.status === "paused" ? (
            <Button variant="outline" onClick={() => resume.mutate()}>
              <Play size={16} />
              <span>Resume</span>
            </Button>
          ) : null}
          <DeleteButton onDelete={() => remove.mutate()} />
        </>
      }
    >
      <CronJobDetail job={job.data} />
    </Page>
  );
}

function CronJobDetail({ job }: { job: CronJobRecord }) {
  const owner = parseCronOwner(job.owner);
  const intro = actionIntro(job.action);

  return (
    <div className="dense-stack">
      <DenseSection
        title="Overview"
        actions={<Status enabled={job.status === "active"} label={job.status} />}
      >
        <DenseMeta
          items={[
            { label: "Type", value: actionKindLabel(job.action.kind) },
            { label: "Lifecycle", value: lifecycleLabel(job), tone: job.schedule.kind === "at" ? "warning" : "default" },
            { label: "Owner", value: job.owner },
            { label: "Actor", value: owner?.actorId ?? "—", tone: owner ? "default" : "muted" },
            { label: "Conversation", value: owner?.conversationId ?? "—", tone: owner ? "default" : "muted" },
          ]}
        />
      </DenseSection>

      <DenseSection title="Schedule">
        <DenseMeta
          items={[
            { label: "Schedule", value: scheduleSummary(job) },
            { label: "Timezone", value: job.schedule.timezone },
            { label: "Next run", value: formatCronTime(job.next_run_at, job.schedule.timezone), tone: job.next_run_at ? "default" : "muted" },
            { label: "Last run", value: formatCronTime(job.last_run_at, job.schedule.timezone), tone: job.last_run_at ? "default" : "muted" },
            { label: "Created", value: formatCronTime(job.created_at, job.schedule.timezone), tone: job.created_at ? "default" : "muted" },
            { label: "Updated", value: formatCronTime(job.updated_at, job.schedule.timezone), tone: job.updated_at ? "default" : "muted" },
          ]}
        />
      </DenseSection>

      <DenseSection title="Intro Preview">
        <pre className="resource-preview">{intro}</pre>
      </DenseSection>

      <DenseSection title="Action Detail">
        <pre className="resource-preview resource-preview--tall">{actionDetailText(job.action)}</pre>
      </DenseSection>
    </div>
  );
}

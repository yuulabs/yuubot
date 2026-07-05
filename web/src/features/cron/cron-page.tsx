import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { deleteCronJob, listCronJobs, pauseCronJob, resumeCronJob } from "@/shared/lib/api";
import type { CronJobRecord } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import {
  DeleteButton,
  EmptyState,
  ErrorState,
  LoadingState,
  Page,
  Panel,
  ResourceCard,
  ResourceCardGrid,
  ResourceMeta,
  Status,
} from "@/shared/components";
import {
  actionKindLabel,
  actionSummary,
  formatCronTime,
  parseCronOwner,
  scheduleSummary,
  statusTone,
} from "./cron-shared";

export function CronPage() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname !== "/cron") {
    return <Outlet />;
  }

  const queryClient = useQueryClient();
  const [ownerFilter, setOwnerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const jobs = useQuery({
    queryKey: ["cron-jobs", ownerFilter, statusFilter],
    queryFn: () => listCronJobs({ owner: ownerFilter || undefined, status: statusFilter || undefined }),
    refetchInterval: 10_000,
  });
  const pause = useMutation({
    mutationFn: (jobId: string) => pauseCronJob(jobId),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["cron-jobs"] }),
  });
  const resume = useMutation({
    mutationFn: (jobId: string) => resumeCronJob(jobId),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["cron-jobs"] }),
  });
  const remove = useMutation({
    mutationFn: (jobId: string) => deleteCronJob(jobId),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["cron-jobs"] }),
  });

  const sortedJobs = useMemo(() => jobs.data ?? [], [jobs.data]);

  if (jobs.isLoading) return <LoadingState />;
  if (jobs.error) return <ErrorState error={jobs.error} />;

  return (
    <Page
      title="Cron Jobs"
      sub="Durable scheduled shell tasks, actor wakeups, and reminders."
      actions={
        <Button asChild>
          <Link to="/cron/new">New Cron Job</Link>
        </Button>
      }
    >
      <div className="grid gap-3">
        <Panel>
          <div className="mb-2">
            <h2 className="text-lg font-semibold">Filters</h2>
            <p className="page-sub">Narrow the list by owner or lifecycle status.</p>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            <label className="grid gap-1">
              <span className="text-sm font-medium">Owner</span>
              <input
                className="input"
                placeholder="actor:id:conv:conversation"
                value={ownerFilter}
                onChange={(event) => setOwnerFilter(event.target.value)}
              />
            </label>
            <label className="grid gap-1">
              <span className="text-sm font-medium">Status</span>
              <select className="input" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="">All statuses</option>
                <option value="active">Active</option>
                <option value="paused">Paused</option>
                <option value="completed">Completed</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </label>
          </div>
        </Panel>

        {!sortedJobs.length ? (
          <EmptyState>No cron jobs yet. Create one to schedule durable work.</EmptyState>
        ) : (
          <ResourceCardGrid>
            {sortedJobs.map((job) => (
              <CronJobCard
                key={job.id}
                job={job}
                onPause={() => pause.mutate(job.id)}
                onResume={() => resume.mutate(job.id)}
                onDelete={() => remove.mutate(job.id)}
              />
            ))}
          </ResourceCardGrid>
        )}
      </div>
    </Page>
  );
}

function CronJobCard({
  job,
  onPause,
  onResume,
  onDelete,
}: {
  job: CronJobRecord;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
}) {
  const owner = parseCronOwner(job.owner);

  return (
    <ResourceCard
      variant="task"
      label={actionKindLabel(job.action.kind)}
      title={job.name}
      subtitle={job.id}
      status={<Status enabled={job.status === "active"} label={job.status} />}
      actions={
        <>
          {job.status === "active" ? (
            <Button variant="outline" size="sm" onClick={onPause}>Pause</Button>
          ) : job.status === "paused" ? (
            <Button variant="outline" size="sm" onClick={onResume}>Resume</Button>
          ) : null}
          <DeleteButton onDelete={onDelete} />
        </>
      }
    >
      <div className="resource-flow">
        <span className="resource-flow__node">{job.schedule.kind === "cron" ? "Recurring" : "One-shot"}</span>
        <span className="resource-flow__arrow">-&gt;</span>
        <span className="resource-flow__node">{actionKindLabel(job.action.kind)}</span>
        <span className="resource-flow__arrow">-&gt;</span>
        <span className="resource-flow__node">{actionSummary(job.action)}</span>
      </div>
      <ResourceMeta
        items={[
          { label: "Schedule", value: scheduleSummary(job) },
          { label: "Action", value: actionSummary(job.action) },
          {
            label: "Actor",
            value: owner?.actorId ?? job.owner,
            tone: owner ? "default" : "muted",
          },
          {
            label: "Conversation",
            value: owner?.conversationId ?? "—",
            tone: owner ? "default" : "muted",
          },
          { label: "Next run", value: formatCronTime(job.next_run_at), tone: job.next_run_at ? "default" : "muted" },
          { label: "Last run", value: formatCronTime(job.last_run_at), tone: job.last_run_at ? "default" : "muted" },
          { label: "Lifecycle", value: job.once ? "Run once" : "Repeat", tone: job.once ? "warning" : "default" },
          { label: "Status", value: job.status, tone: statusTone(job.status) },
        ]}
      />
    </ResourceCard>
  );
}

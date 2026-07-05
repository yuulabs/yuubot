import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ComponentType } from "react";
import { Activity, CircleDot, DollarSign, FileText } from "lucide-react";

import { cancelTask, getRuntime, getTask, listTasks } from "@/shared/lib/api";
import type { TaskRecord } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import {
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
import { useBootstrap, useHealth } from "@/shared/hooks";
import { CostDashboard } from "./components/cost-dashboard";

export function MonitorPage() {
  const queryClient = useQueryClient();
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: getRuntime, refetchInterval: 5_000 });
  const [taskOwner, setTaskOwner] = useState("");
  const [taskNameGlob, setTaskNameGlob] = useState("");
  const tasks = useQuery({
    queryKey: ["tasks", taskOwner, taskNameGlob],
    queryFn: () => listTasks({ owner: taskOwner, nameGlob: taskNameGlob }),
    refetchInterval: 5_000,
  });
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const taskDetail = useQuery({
    queryKey: ["task", selectedTaskId],
    queryFn: () => getTask(selectedTaskId),
    enabled: Boolean(selectedTaskId),
    refetchInterval: 5_000,
  });
  const cancel = useMutation({
    mutationFn: (taskId: string) => cancelTask(taskId),
    onSuccess: async (_record, taskId) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["runtime"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["task", taskId] }),
      ]);
    },
  });
  const { data: bootstrap } = useBootstrap();
  const { data: health } = useHealth();

  if (runtime.isLoading || tasks.isLoading) return <LoadingState />;
  if (runtime.error) return <ErrorState error={runtime.error} />;
  if (tasks.error) return <ErrorState error={tasks.error} />;

  const actors = bootstrap?.actors ?? [];
  const routes = bootstrap?.routes ?? [];

  return (
    <Page title="Runtime" sub="Live runtime, tasks, integrations, actors, and cost analytics.">
      <div className="monitor-page">
        <div className="monitor-stats monitor-stats--four">
          <MonitorStat icon={Activity} label="Active Actors" value={actors.filter((actor) => actor.enabled).length} sub={`of ${actors.length} total`} />
          <MonitorStat icon={CircleDot} label="Providers" value={bootstrap?.providers.length ?? 0} sub="configured" />
          <MonitorStat icon={FileText} label="Routes" value={routes.length} sub="ingress bindings" />
          <MonitorStat icon={DollarSign} label="Health" value={health?.status === "ok" || health?.ok ? "OK" : "N/A"} sub="system status" />
        </div>

        <div className="grid gap-3">
          <Panel>
            <h2 className="text-lg font-semibold">Paths</h2>
            <p>{runtime.data?.data_dir}</p>
            <p>{runtime.data?.workspace_dir}</p>
          </Panel>
          <Panel>
            <h2 className="text-lg font-semibold">Tasks</h2>
            <div className="mb-2 grid gap-2 md:grid-cols-2">
              <input className="input" value={taskOwner} placeholder="owner" onChange={(event) => setTaskOwner(event.target.value)} />
              <input className="input" value={taskNameGlob} placeholder="name glob" onChange={(event) => setTaskNameGlob(event.target.value)} />
            </div>
            {!tasks.data?.length ? <EmptyState>No tasks.</EmptyState> : (
              <div className="grid gap-3">
                <ResourceCardGrid>
                {tasks.data.map((task) => (
                  <ResourceCard
                    key={task.id}
                    variant="task"
                    label={task.kind}
                    title={<button className="font-medium text-left underline-offset-4 hover:underline" type="button" onClick={() => setSelectedTaskId(task.id)}>{task.name}</button>}
                    subtitle={task.intro || task.owner}
                    status={<Status enabled={task.status === "running" || task.status === "done"} label={task.status} />}
                    selected={selectedTaskId === task.id}
                    actions={canCancelTask(task) ? <Button variant="outline" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate(task.id)}>Cancel</Button> : undefined}
                  >
                    <ResourceMeta
                      items={[
                        { label: "Owner", value: task.owner },
                        { label: "Delivery", value: task.delivery_state ?? "unknown", tone: task.delivery_state === "delivered" ? "ok" : "muted" },
                        { label: "Exit", value: task.exit_code ?? "pending", tone: task.exit_code === 0 ? "ok" : task.exit_code == null ? "muted" : "danger" },
                        { label: "Task id", value: task.id },
                      ]}
                    />
                    {task.error && <pre className="resource-preview">{task.error}</pre>}
                  </ResourceCard>
                ))}
                </ResourceCardGrid>
                {taskDetail.data && (
                  <ResourceCard
                    variant="task"
                    title="Task output"
                    subtitle={taskDetail.data.id}
                    status={<Status enabled={taskDetail.data.status === "running" || taskDetail.data.status === "done"} label={taskDetail.data.status} />}
                    actions={canCancelTask(taskDetail.data) ? <Button variant="outline" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate(taskDetail.data.id)}>Cancel</Button> : undefined}
                  >
                    <ResourceMeta
                      items={[
                        { label: "Owner", value: taskDetail.data.owner },
                        { label: "Kind", value: taskDetail.data.kind },
                        { label: "Delivery", value: taskDetail.data.delivery_state ?? "unknown", tone: taskDetail.data.delivery_state === "delivered" ? "ok" : "muted" },
                        { label: "Exit", value: taskDetail.data.exit_code ?? "pending", tone: taskDetail.data.exit_code === 0 ? "ok" : taskDetail.data.exit_code == null ? "muted" : "danger" },
                      ]}
                    />
                    {taskDetail.data.error && <pre className="resource-preview">{taskDetail.data.error}</pre>}
                    <pre className="resource-preview">{taskDetail.data.stdout_tail || "No stdout."}</pre>
                  </ResourceCard>
                )}
                {taskDetail.error && <p className="text-sm text-destructive">{taskDetail.error instanceof Error ? taskDetail.error.message : String(taskDetail.error)}</p>}
              </div>
            )}
          </Panel>
          <Panel>
            <h2 className="text-lg font-semibold">Events</h2>
            {!runtime.data?.events.length ? <EmptyState>No events.</EmptyState> : runtime.data.events.map((event) => (
              <pre key={`${event.ts}-${event.kind}`} className="overflow-auto rounded border p-3 text-xs">{event.kind} {JSON.stringify(event.payload)}</pre>
            ))}
          </Panel>
        </div>

        <CostDashboard />
      </div>
    </Page>
  );
}

function canCancelTask(task: TaskRecord): boolean {
  return task.status === "pending" || task.status === "running";
}

function MonitorStat({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: ComponentType<{ className?: string; size?: number }>;
  label: string;
  value: string | number;
  sub: string;
}) {
  return (
    <article className="monitor-stat">
      <div className="monitor-stat__icon">
        <Icon size={18} />
      </div>
      <div className="monitor-stat__body">
        <p className="monitor-stat__label">{label}</p>
        <p className="monitor-stat__value">{value}</p>
        <p className="monitor-stat__sub">{sub}</p>
      </div>
    </article>
  );
}

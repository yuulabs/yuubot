import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ComponentType, FormEvent } from "react";
import { Activity, CircleDot, Cpu, DollarSign, FileText, HardDrive, MemoryStick } from "lucide-react";

import { formatToolOutput } from "@/shared/lib/tool-renderers";
import { cancelTask, getRuntime, getTask, listTasks, sendTaskStdin } from "@/shared/lib/api";
import type { RuntimeEvent, TaskRecord } from "@/shared/types/api";
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
import { useTaskStream } from "./hooks/use-task-stream";

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
  const [stdinDraft, setStdinDraft] = useState("");
  const taskDetail = useQuery({
    queryKey: ["task", selectedTaskId],
    queryFn: () => getTask(selectedTaskId),
    enabled: Boolean(selectedTaskId),
    refetchInterval: 5_000,
  });
  const { liveStdout, liveStatus } = useTaskStream(selectedTaskId || undefined);
  const stdin = useMutation({
    mutationFn: ({ taskId, text }: { taskId: string; text: string }) => sendTaskStdin(taskId, text),
    onSuccess: async (_record, variables) => {
      setStdinDraft("");
      await queryClient.invalidateQueries({ queryKey: ["task", variables.taskId] });
    },
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
  const host = runtime.data?.host;
  const diskWarn = (host?.disk_percent ?? 0) >= 85;

  return (
    <Page title="Runtime" sub="Live runtime, tasks, integrations, actors, and cost analytics.">
      <div className="monitor-page">
        <div className="monitor-stats monitor-stats--four">
          <MonitorStat icon={Activity} label="Active Actors" value={actors.filter((actor) => actor.enabled).length} sub={`of ${actors.length} total`} />
          <MonitorStat icon={CircleDot} label="Providers" value={bootstrap?.providers.length ?? 0} sub="configured" />
          <MonitorStat icon={FileText} label="Routes" value={routes.length} sub="ingress bindings" />
          <MonitorStat icon={DollarSign} label="Health" value={health?.status === "ok" || health?.ok ? "OK" : "N/A"} sub="system status" />
        </div>
        {host && (
          <div className="monitor-stats monitor-stats--three">
            <MonitorStat icon={Cpu} label="CPU" value={`${host.cpu_percent.toFixed(1)}%`} sub="host utilization" />
            <MonitorStat icon={MemoryStick} label="Memory" value={`${host.memory_percent.toFixed(1)}%`} sub={formatBytes(host.memory_used_bytes, host.memory_total_bytes)} />
            <MonitorStat icon={HardDrive} label="Disk" value={`${host.disk_percent.toFixed(1)}%`} sub={formatBytes(host.disk_used_bytes, host.disk_total_bytes)} danger={diskWarn} />
          </div>
        )}

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
                    status={<Status enabled={displayStatus(taskDetail.data, liveStatus) === "running" || displayStatus(taskDetail.data, liveStatus) === "done"} label={displayStatus(taskDetail.data, liveStatus)} />}
                    actions={canCancelTask(taskDetail.data, liveStatus) ? <Button variant="outline" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate(taskDetail.data.id)}>Cancel</Button> : undefined}
                  >
                    <ResourceMeta
                      items={[
                        { label: "Owner", value: taskDetail.data.owner },
                        { label: "Kind", value: taskDetail.data.kind },
                        { label: "Delivery", value: taskDetail.data.delivery_state ?? "unknown", tone: taskDetail.data.delivery_state === "delivered" ? "ok" : "muted" },
                        { label: "Exit", value: taskDetail.data.exit_code ?? "pending", tone: taskDetail.data.exit_code === 0 ? "ok" : taskDetail.data.exit_code == null ? "muted" : "danger" },
                        { label: "Created", value: taskDetail.data.created_at ?? "unknown" },
                        { label: "Started", value: taskDetail.data.started_at ?? "pending" },
                        { label: "Finished", value: taskDetail.data.finished_at ?? "pending" },
                      ]}
                    />
                    {taskDetail.data.error && <pre className="resource-preview">{taskDetail.data.error}</pre>}
                    <pre className="resource-preview">{displayStdout(taskDetail.data, liveStdout) || "No stdout."}</pre>
                    {canSendStdin(taskDetail.data, liveStatus) && (
                      <form
                        className="mt-2 flex gap-2"
                        onSubmit={(event: FormEvent<HTMLFormElement>) => {
                          event.preventDefault();
                          if (!stdinDraft.trim()) return;
                          stdin.mutate({ taskId: taskDetail.data.id, text: stdinDraft });
                        }}
                      >
                        <input
                          className="input flex-1"
                          value={stdinDraft}
                          placeholder="Send stdin to running task"
                          onChange={(event) => setStdinDraft(event.target.value)}
                        />
                        <Button type="submit" size="sm" disabled={stdin.isPending || !stdinDraft}>
                          Send
                        </Button>
                      </form>
                    )}
                  </ResourceCard>
                )}
                {taskDetail.error && <p className="text-sm text-destructive">{taskDetail.error instanceof Error ? taskDetail.error.message : String(taskDetail.error)}</p>}
              </div>
            )}
          </Panel>
          <Panel>
            <h2 className="text-lg font-semibold">Events</h2>
            {!runtime.data?.events.length ? <EmptyState>No events.</EmptyState> : (
              <div className="runtime-events">
                {runtime.data.events.map((event, index) => (
                  <RuntimeEventRow key={`${event.ts}-${event.kind}-${index}`} event={event} />
                ))}
              </div>
            )}
          </Panel>
        </div>

        <CostDashboard />
      </div>
    </Page>
  );
}

function canCancelTask(task: TaskRecord, liveStatus?: string): boolean {
  const status = liveStatus ?? task.status;
  return status === "pending" || status === "running";
}

function canSendStdin(task: TaskRecord, liveStatus?: string): boolean {
  return (liveStatus ?? task.status) === "running" && task.interactive !== false;
}

function displayStatus(task: TaskRecord, liveStatus?: string): string {
  return liveStatus ?? task.status;
}

function displayStdout(task: TaskRecord, liveStdout: string): string {
  if (liveStdout) return formatToolOutput(liveStdout);
  return formatToolOutput(task.stdout_tail ?? "");
}

function RuntimeEventRow({ event }: { event: RuntimeEvent }) {
  const context = Object.entries(event.context ?? {});
  return (
    <article className="runtime-event">
      <div className="runtime-event__time">{formatEventTime(event.ts)}</div>
      <div className="runtime-event__body">
        <div className="runtime-event__head">
          <span className="runtime-event__title">{event.title || event.kind}</span>
          <code className="runtime-event__kind">{event.kind}</code>
        </div>
        {event.detail && <p className="runtime-event__detail">{event.detail}</p>}
        {context.length > 0 && (
          <div className="runtime-event__context">
            {context.map(([key, value]) => (
              <span key={key} className="runtime-event__chip">
                <span>{key}</span>
                <strong>{formatContextValue(value)}</strong>
              </span>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function formatEventTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatContextValue(value: unknown): string {
  if (value == null) return "null";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function formatBytes(used: number, total: number): string {
  return `${formatByteCount(used)} / ${formatByteCount(total)}`;
}

function formatByteCount(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${value} B`;
}

function MonitorStat({
  icon: Icon,
  label,
  value,
  sub,
  danger = false,
}: {
  icon: ComponentType<{ className?: string; size?: number }>;
  label: string;
  value: string | number;
  sub: string;
  danger?: boolean;
}) {
  return (
    <article className={`monitor-stat${danger ? " monitor-stat--danger" : ""}`}>
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

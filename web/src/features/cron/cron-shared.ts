import type { CronActionRecord, CronJobRecord, CronScheduleRecord } from "@/shared/types/api";

export const TIMEZONE_PRESETS = ["UTC", "Asia/Shanghai", "America/New_York", "Europe/London"] as const;

export type CronDraft = {
  name: string;
  actorId: string;
  conversationId: string;
  scheduleKind: "cron" | "at";
  timezone: string;
  cron: string;
  at: string;
  once: boolean;
  actionKind: CronActionRecord["kind"];
  shellName: string;
  shell: string;
  intro: string;
  wakeupText: string;
  reminderTitle: string;
  reminderBody: string;
  reminderBrowser: boolean;
  reminderPush: boolean;
};

export const emptyCronDraft: CronDraft = {
  name: "",
  actorId: "",
  conversationId: "",
  scheduleKind: "cron",
  timezone: "Asia/Shanghai",
  cron: "0 9 * * mon-fri",
  at: "",
  once: false,
  actionKind: "reminder",
  shellName: "",
  shell: "",
  intro: "",
  wakeupText: "",
  reminderTitle: "",
  reminderBody: "",
  reminderBrowser: true,
  reminderPush: true,
};

export function buildCronOwner(actorId: string, conversationId: string): string {
  return `actor:${actorId}:conv:${conversationId}`;
}

export function parseCronOwner(owner: string): { actorId: string; conversationId: string } | null {
  const match = /^actor:(.+):conv:(.+)$/.exec(owner);
  if (!match) {
    return null;
  }
  return { actorId: match[1], conversationId: match[2] };
}

export function buildCronSchedule(draft: CronDraft): CronScheduleRecord {
  if (draft.scheduleKind === "cron") {
    return { kind: "cron", timezone: draft.timezone, cron: draft.cron };
  }
  return { kind: "at", timezone: draft.timezone, at: draft.at };
}

export function buildCronAction(draft: CronDraft): CronActionRecord {
  if (draft.actionKind === "shell") {
    return { kind: "shell", name: draft.shellName, shell: draft.shell, intro: draft.intro };
  }
  if (draft.actionKind === "wakeup") {
    return { kind: "wakeup", text: draft.wakeupText };
  }
  const channels: Array<{ kind: string }> = [];
  if (draft.reminderBrowser) channels.push({ kind: "browser" });
  if (draft.reminderPush) channels.push({ kind: "web_push" });
  return { kind: "reminder", title: draft.reminderTitle, body: draft.reminderBody, channels };
}

export function canCreateCronDraft(draft: CronDraft): boolean {
  if (!draft.name || !draft.actorId || !draft.conversationId || !draft.timezone) {
    return false;
  }
  if (draft.scheduleKind === "cron") {
    return Boolean(draft.cron);
  }
  return Boolean(draft.at);
}

export function scheduleSummary(job: CronJobRecord): string {
  const { schedule } = job;
  if (schedule.kind === "cron") {
    return `${schedule.cron} @ ${schedule.timezone}`;
  }
  return `${schedule.at} @ ${schedule.timezone}`;
}

export function actionKindLabel(kind: CronActionRecord["kind"]): string {
  if (kind === "shell") return "Shell task";
  if (kind === "wakeup") return "Actor wakeup";
  return "Reminder";
}

export function actionSummary(action: CronActionRecord): string {
  if (action.kind === "shell") {
    return action.name || action.shell || "Shell task";
  }
  if (action.kind === "wakeup") {
    return action.text?.trim() || "Wake actor and run loop";
  }
  return action.title?.trim() || "Reminder";
}

export function formatCronTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

export function statusTone(status: CronJobRecord["status"]): "default" | "ok" | "warning" | "muted" {
  if (status === "active") return "ok";
  if (status === "paused") return "warning";
  return "muted";
}

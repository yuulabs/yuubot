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
  actionKind: CronActionRecord["kind"];
  shellName: string;
  shell: string;
  intro: string;
  messageText: string;
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
  actionKind: "reminder",
  shellName: "",
  shell: "",
  intro: "",
  messageText: "",
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
  if (draft.actionKind === "actor_message") {
    return { kind: "actor_message", text: draft.messageText };
  }
  if (draft.actionKind === "conversation_callback") {
    return { kind: "conversation_callback", text: draft.messageText };
  }
  if (draft.actionKind === "wakeup") {
    return { kind: "wakeup", text: draft.messageText };
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
    if (!draft.cron) return false;
  } else if (!draft.at) {
    return false;
  }
  if (draft.actionKind === "actor_message" || draft.actionKind === "conversation_callback") {
    return Boolean(draft.messageText.trim());
  }
  return true;
}

export function scheduleSummary(job: CronJobRecord): string {
  const { schedule } = job;
  if (schedule.kind === "cron") {
    return `${schedule.cron} @ ${schedule.timezone}`;
  }
  return `${formatCronLocalTime(schedule.at)} @ ${schedule.timezone}`;
}

export function actionKindLabel(kind: CronActionRecord["kind"]): string {
  if (kind === "shell") return "Shell task";
  if (kind === "actor_message") return "Actor message";
  if (kind === "conversation_callback") return "Conversation callback";
  if (kind === "wakeup") return "Actor message";
  return "Reminder";
}

export function actionSummary(action: CronActionRecord): string {
  if (action.kind === "shell") {
    return action.name || action.shell || "Shell task";
  }
  if (action.kind === "actor_message" || action.kind === "wakeup") {
    return action.text?.trim() || "Send actor message";
  }
  if (action.kind === "conversation_callback") {
    return action.text?.trim() || "Continue owner conversation";
  }
  return action.title?.trim() || "Reminder";
}

export function actionIntro(action: CronActionRecord): string {
  if (action.kind === "shell") {
    return action.intro?.trim() || action.shell?.trim() || action.name?.trim() || "No intro provided.";
  }
  if (action.kind === "actor_message" || action.kind === "wakeup") {
    return action.text?.trim() || "Send actor message.";
  }
  if (action.kind === "conversation_callback") {
    return action.text?.trim() || "Continue owner conversation.";
  }
  return action.body?.trim() || action.title?.trim() || "No reminder body provided.";
}

export function actionDetailText(action: CronActionRecord): string {
  if (action.kind === "shell") {
    return [
      action.intro ? `Intro:\n${action.intro}` : "",
      action.shell ? `Shell:\n${action.shell}` : "",
    ].filter(Boolean).join("\n\n") || actionSummary(action);
  }
  if (action.kind === "actor_message" || action.kind === "wakeup") {
    return action.text?.trim() || "Send actor message.";
  }
  if (action.kind === "conversation_callback") {
    return action.text?.trim() || "Continue owner conversation.";
  }
  return [
    action.title ? `Title:\n${action.title}` : "",
    action.body ? `Body:\n${action.body}` : "",
    action.channels?.length ? `Channels:\n${action.channels.map((channel) => channel.kind).join(", ")}` : "",
  ].filter(Boolean).join("\n\n") || actionSummary(action);
}

export function lifecycleLabel(job: Pick<CronJobRecord, "schedule">): "One-shot" | "Repeat" {
  return job.schedule.kind === "at" ? "One-shot" : "Repeat";
}

export function formatCronTime(value: string | null | undefined, timezone?: string): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const targetTimezone = timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: targetTimezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}/${part("month")}/${part("day")} ${part("hour")}:${part("minute")}:${part("second")} @ ${targetTimezone}`;
}

function formatCronLocalTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const [date, time = ""] = value.split("T", 2);
  if (!date) {
    return value;
  }
  return `${date.replace(/-/g, "/")}${time ? ` ${time}` : ""}`;
}

export function statusTone(status: CronJobRecord["status"]): "default" | "ok" | "warning" | "muted" {
  if (status === "active") return "ok";
  if (status === "paused") return "warning";
  return "muted";
}

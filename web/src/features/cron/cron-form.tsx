import { useState } from "react";

import type { BootstrapSnapshot } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import { DenseSection } from "@/shared/components";
import {
  TIMEZONE_PRESETS,
  type CronDraft,
  canCreateCronDraft,
  emptyCronDraft,
} from "./cron-shared";

export function CronForm({
  bootstrap,
  initial = emptyCronDraft,
  saveLabel,
  onSave,
}: {
  bootstrap: BootstrapSnapshot;
  initial?: CronDraft;
  saveLabel: string;
  onSave: (draft: CronDraft) => Promise<unknown>;
}) {
  const [draft, setDraft] = useState<CronDraft>(initial);
  const [message, setMessage] = useState("");
  const actors = bootstrap.actors;
  const canSave = canCreateCronDraft(draft);

  return (
    <div className="dense-stack">
      <DenseSection title="Identity" description="Name the job and bind it to an actor conversation.">
        <div className="dense-form-grid">
          <Field label="Job name" value={draft.name} onChange={(name) => setDraft({ ...draft, name })} />
          <SelectField
            label="Actor"
            value={draft.actorId}
            onChange={(actorId) => setDraft({ ...draft, actorId })}
            options={[
              { value: "", label: "Select actor" },
              ...actors.map((actor) => ({ value: actor.id, label: actor.name || actor.id })),
            ]}
          />
          <Field
            label="Conversation id"
            value={draft.conversationId}
            onChange={(conversationId) => setDraft({ ...draft, conversationId })}
            placeholder="e.g. actor-amy"
          />
        </div>
      </DenseSection>

      <DenseSection title="Schedule" description="Timezone is required. Use weekday names (mon, tue, …) in cron expressions.">
        <div className="dense-form-grid">
          <SelectField
            label="Schedule type"
            value={draft.scheduleKind}
            onChange={(scheduleKind) => setDraft({ ...draft, scheduleKind: scheduleKind as CronDraft["scheduleKind"] })}
            options={[
              { value: "cron", label: "Recurring cron" },
              { value: "at", label: "One-shot at" },
            ]}
          />
          <Field
            label="Timezone"
            value={draft.timezone}
            onChange={(timezone) => setDraft({ ...draft, timezone })}
            list="cron-timezone-presets"
            placeholder="IANA timezone"
          />
          <datalist id="cron-timezone-presets">
            {TIMEZONE_PRESETS.map((tz) => <option key={tz} value={tz} />)}
          </datalist>
          {draft.scheduleKind === "cron" ? (
            <Field
              label="Cron expression"
              value={draft.cron}
              onChange={(cron) => setDraft({ ...draft, cron })}
              placeholder="min hour dom month dow"
              className="md:col-span-2"
            />
          ) : (
            <Field
              label="Run at"
              value={draft.at}
              onChange={(at) => setDraft({ ...draft, at })}
              placeholder="YYYY-MM-DDTHH:MM:SS local"
              className="md:col-span-2"
            />
          )}
          <CheckboxField
            label="Run once then complete"
            checked={draft.once}
            onChange={(once) => setDraft({ ...draft, once })}
          />
        </div>
      </DenseSection>

      <DenseSection title="Action" description="Choose what happens when the schedule fires.">
        <div className="dense-form-grid">
          <SelectField
            label="Action type"
            value={draft.actionKind}
            onChange={(actionKind) => setDraft({ ...draft, actionKind: actionKind as CronDraft["actionKind"] })}
            options={[
              { value: "shell", label: "Shell task" },
              { value: "wakeup", label: "Actor wakeup" },
              { value: "reminder", label: "Reminder" },
            ]}
          />
        </div>
        {draft.actionKind === "shell" && (
          <div className="mt-3 dense-form-grid">
            <Field label="Task name" value={draft.shellName} onChange={(shellName) => setDraft({ ...draft, shellName })} />
            <Field
              label="Shell command"
              value={draft.shell}
              onChange={(shell) => setDraft({ ...draft, shell })}
              className="md:col-span-2"
            />
            <Field
              label="Intro"
              value={draft.intro}
              onChange={(intro) => setDraft({ ...draft, intro })}
              className="md:col-span-3"
            />
          </div>
        )}
        {draft.actionKind === "wakeup" && (
          <label className="mt-3 grid gap-1">
            <span className="text-sm font-medium">Wakeup message</span>
            <textarea
              className="input min-h-24"
              value={draft.wakeupText}
              onChange={(event) => setDraft({ ...draft, wakeupText: event.target.value })}
            />
          </label>
        )}
        {draft.actionKind === "reminder" && (
          <div className="mt-3 grid gap-2">
            <Field label="Title" value={draft.reminderTitle} onChange={(reminderTitle) => setDraft({ ...draft, reminderTitle })} />
            <label className="grid gap-1">
              <span className="text-sm font-medium">Body</span>
              <textarea
                className="input min-h-24"
                value={draft.reminderBody}
                onChange={(event) => setDraft({ ...draft, reminderBody: event.target.value })}
              />
            </label>
            <div className="flex flex-wrap gap-4 text-sm">
              <CheckboxField
                label="Browser toast"
                checked={draft.reminderBrowser}
                onChange={(reminderBrowser) => setDraft({ ...draft, reminderBrowser })}
              />
              <CheckboxField
                label="Web push"
                checked={draft.reminderPush}
                onChange={(reminderPush) => setDraft({ ...draft, reminderPush })}
              />
            </div>
          </div>
        )}
      </DenseSection>

      <div className="dense-actions-bar">
        <div className="dense-actions-bar__status">{message || "Review schedule, timezone, and action before saving."}</div>
        <div className="dense-actions-bar__buttons">
          <Button
            disabled={!canSave}
            onClick={async () => {
              try {
                setMessage("");
                await onSave(draft);
              } catch (err) {
                setMessage(err instanceof Error ? err.message : String(err));
              }
            }}
          >
            {saveLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  list,
  className,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  list?: string;
  className?: string;
}) {
  return (
    <label className={className ? `grid gap-1 ${className}` : "grid gap-1"}>
      <span className="text-sm font-medium">{label}</span>
      <input className="input" value={value} placeholder={placeholder} list={list} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
  className,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  className?: string;
}) {
  return (
    <label className={className ? `grid gap-1 ${className}` : "grid gap-1"}>
      <span className="text-sm font-medium">{label}</span>
      <select className="input" value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => <option key={option.value || option.label} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}

function CheckboxField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, RefreshCw, Save, Trash2 } from "lucide-react";

import { deleteSkill, getSkill, listInstalledSkills, listSkills, putSkill, runSkillCommand } from "@/shared/lib/api";
import type { SkillCliAction, SkillInput, SkillRecord, SkillSummary } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  DenseMeta,
  DenseSection,
  ErrorState,
  LoadingState,
  Page,
  ResourceActions,
  ResourceList,
  ResourceListPrimary,
} from "@/shared/components";

const queryKey = ["skills"] as const;
const installedQueryKey = ["skills", "installed"] as const;

const emptyDraft: Pick<SkillRecord, "id" | "name" | "description" | "body"> = {
  id: "",
  name: "",
  description: "",
  body: "",
};

export function SkillsPage() {
  const query = useQuery({ queryKey, queryFn: listSkills });
  const installedQuery = useQuery({ queryKey: installedQueryKey, queryFn: listInstalledSkills });
  const client = useQueryClient();
  const [source, setSource] = useState("");
  const [draft, setDraft] = useState(emptyDraft);
  const [message, setMessage] = useState("");
  const save = useMutation({
    mutationFn: (skill: typeof draft) => putSkill(skill.id, skillInput(skill)),
    onSuccess: ({ record }) => {
      setDraft(toDraft(record));
      setMessage(`saved ${record.id}`);
      client.invalidateQueries({ queryKey });
    },
  });
  const remove = useMutation({
    mutationFn: (skillId: string) => deleteSkill(skillId),
    onSuccess: ({ id }) => {
      setDraft(emptyDraft);
      setMessage(`deleted ${id}`);
      client.invalidateQueries({ queryKey });
    },
  });
  const load = useMutation({
    mutationFn: (skillId: string) => getSkill(skillId),
    onSuccess: (record) => {
      setDraft(toDraft(record));
      setMessage(`editing ${record.id}`);
    },
  });
  const command = useMutation({
    mutationFn: ({ action, target }: { action: SkillCliAction; target?: string }) => runSkillCommand(action, target ?? ""),
    onSuccess: () => {
      setSource("");
      setMessage("skills command completed");
      client.invalidateQueries({ queryKey: installedQueryKey });
    },
  });
  const error = query.error ?? installedQuery.error ?? save.error ?? remove.error ?? load.error ?? command.error;
  const canInstall = Boolean(source.trim());
  const canSave = Boolean(draft.id.trim() && draft.name.trim());

  if (query.isLoading) return <LoadingState />;

  return (
    <Page title="Skills" sub="Global prompt skills injected into actors, plus packages installed through the public skills CLI.">
      <div className="dense-stack">
        {error && <ErrorState error={error} />}

        <DenseSection
          title="Local skills"
          description={`${query.data?.length ?? 0} prompt skills available to yuubot actors.`}
          actions={
            <Button variant="outline" onClick={() => {
              setDraft(emptyDraft);
              setMessage("new skill draft");
            }}>
              <Plus size={14} />
              <span>New Skill</span>
            </Button>
          }
        >
          <div className="crud-split crud-split--wide-form">
            <ResourceList
              rows={query.data ?? []}
              getRowId={(skill) => skill.id}
              emptyLabel="No local skills configured."
              columns={[
                {
                  key: "skill",
                  label: "Skill",
                  render: (skill) => (
                    <ResourceListPrimary
                      title={skill.name}
                      subtitle={skill.description || "No description provided."}
                      meta={<DenseMeta items={[
                        { label: "ID", value: skill.id },
                        { label: "Inspect", value: skill.inspect_hint },
                      ]} />}
                    />
                  ),
                },
                {
                  key: "actions",
                  label: "",
                  className: "is-actions",
                  render: (skill) => (
                    <SkillActions
                      skill={skill}
                      disabled={load.isPending || remove.isPending}
                      primaryLabel="Edit"
                      onEdit={(target) => load.mutate(target)}
                      onDelete={(target) => {
                        if (window.confirm(`Delete ${skill.name}?`)) {
                          remove.mutate(target);
                        }
                      }}
                    />
                  ),
                },
              ]}
            />
            <div className="crud-form">
              <div className="crud-form__head">
                <div className="crud-form__icon">
                  <Save />
                </div>
                <div>
                  <div className="crud-form__title">{draft.id ? `Edit ${draft.id}` : "New skill"}</div>
                  <div className="crud-form__sub">Saved skills are included in prompt context.</div>
                </div>
              </div>
              <div className="crud-form__body">
                <LabeledInput label="Skill ID" value={draft.id} onChange={(value) => setDraft({ ...draft, id: value })} />
                <LabeledInput label="Name" value={draft.name} onChange={(value) => setDraft({ ...draft, name: value })} />
                <label className="grid gap-1">
                  <span className="text-sm font-medium">Description</span>
                  <Textarea name="description" autoComplete="off" rows={3} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
                </label>
                <label className="grid gap-1">
                  <span className="text-sm font-medium">Body</span>
                  <Textarea name="body" autoComplete="off" className="font-mono" rows={12} value={draft.body} onChange={(event) => setDraft({ ...draft, body: event.target.value })} />
                </label>
                <div className="dense-actions-bar__status">{message || "Create or edit a local skill."}</div>
                <div className="resource-actions">
                  <Button disabled={!canSave || save.isPending} onClick={() => save.mutate(draft)}>
                    <Save size={14} />
                    <span>Save Skill</span>
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </DenseSection>

        <DenseSection
          title="Installed packages"
          description={`${installedQuery.data?.length ?? 0} global skills reported by npx skills.`}
          actions={
            <Button variant="outline" disabled={command.isPending} onClick={() => command.mutate({ action: "update" })}>
              <RefreshCw size={14} />
              <span>Update All</span>
            </Button>
          }
        >
          <label className="skill-install-row">
            <span className="skill-install-row__prefix">npx skills add -g -y</span>
            <Input name="skill-package-source" autoComplete="off" value={source} onChange={(event) => setSource(event.target.value)} placeholder="vercel-labs/skills" />
            <Button disabled={!canInstall || command.isPending} onClick={() => command.mutate({ action: "add", target: source })}>
              <Download size={14} />
              <span>Install</span>
            </Button>
          </label>
          <ResourceList
            rows={installedQuery.data ?? []}
            getRowId={(skill) => skill.id}
            emptyLabel="No global skills reported by npx skills."
            columns={[
              {
                key: "skill",
                label: "Skill",
                render: (skill) => (
                  <ResourceListPrimary title={skill.name} subtitle={skill.description || "No description provided."} />
                ),
              },
              {
                key: "actions",
                label: "",
                className: "is-actions",
                render: (skill) => (
                  <SkillActions
                    skill={skill}
                    disabled={command.isPending}
                    primaryLabel="Update"
                    onEdit={(target) => command.mutate({ action: "update", target })}
                    onDelete={(target) => {
                      if (window.confirm(`Remove ${skill.name}?`)) {
                        command.mutate({ action: "remove", target });
                      }
                    }}
                  />
                ),
              },
            ]}
          />
        </DenseSection>
      </div>
    </Page>
  );
}

function skillInput(skill: typeof emptyDraft): SkillInput {
  return {
    name: skill.name,
    description: skill.description,
    body: skill.body,
    scope: "global",
  };
}

function SkillActions({
  skill,
  disabled,
  primaryLabel,
  onEdit,
  onDelete,
}: {
  skill: SkillSummary;
  disabled: boolean;
  primaryLabel: string;
  onEdit: (target: string) => void;
  onDelete: (target: string) => void;
}) {
  return (
    <ResourceActions>
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => onEdit(skill.id)}>
        <RefreshCw size={14} />
        <span>{primaryLabel}</span>
      </Button>
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => onDelete(skill.id)}>
        <Trash2 size={14} />
        <span>Remove</span>
      </Button>
    </ResourceActions>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid gap-1">
      <span className="text-sm font-medium">{label}</span>
      <Input name={fieldName(label)} autoComplete="off" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function toDraft(record: SkillRecord): typeof emptyDraft {
  return {
    id: record.id,
    name: record.name,
    description: record.description,
    body: record.body,
  };
}

function fieldName(label: string): string {
  return label.toLowerCase().replace(/\s+/g, "-");
}

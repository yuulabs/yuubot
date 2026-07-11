import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Eye, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { getActorFileContent, listActorSkills, setActorSkillLoaded } from "@/shared/lib/api";
import type { WorkspaceSkillSummary } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { DenseSection } from "@/shared/components";

interface ParsedSkill {
  body: string;
}

export function ActorSkillsPanel({ actorId }: { actorId: string }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detailSkill, setDetailSkill] = useState<WorkspaceSkillSummary | null>(null);
  const skillsQuery = useQuery({ queryKey: ["actor-skills", actorId], queryFn: () => listActorSkills(actorId) });

  const items = skillsQuery.data?.items ?? [];
  const loadedItems = useMemo(() => items.filter((item) => item.loaded), [items]);

  const setLoaded = useMutation({
    mutationFn: async ({ skillIds, loaded }: { skillIds: string[]; loaded: boolean }) => {
      await Promise.all(skillIds.map((id) => setActorSkillLoaded(actorId, id, loaded)));
    },
    onSuccess: (_, variables) => {
      void skillsQuery.refetch();
      setSelected(new Set());
      toast.success(variables.loaded ? "Skills loaded" : "Skills banned", {
        description: `${variables.skillIds.length} skill(s) updated.`,
      });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : String(error));
    },
  });

  const toggle = (skillId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(skillId)) next.delete(skillId);
      else next.add(skillId);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((current) => (current.size === items.length ? new Set() : new Set(items.map((item) => item.id))));
  };

  const selectedLoaded = useMemo(
    () => Array.from(selected).filter((id) => items.find((item) => item.id === id)?.loaded),
    [selected, items],
  );
  const selectedBanned = useMemo(
    () => Array.from(selected).filter((id) => !items.find((item) => item.id === id)?.loaded),
    [selected, items],
  );

  return (
    <DenseSection
      title="Workspace skills"
      description={`${items.length} installed for this Actor. ${loadedItems.length} loaded, ${items.length - loadedItems.length} banned.`}
      actions={
        selected.size > 0 && (
          <div className="flex flex-wrap gap-2">
            <span className="self-center text-xs text-muted-foreground">{selected.size} selected</span>
            {selectedLoaded.length > 0 && (
              <Button
                size="sm"
                variant="outline"
                disabled={setLoaded.isPending}
                onClick={() => setLoaded.mutate({ skillIds: selectedLoaded, loaded: false })}
              >
                Ban selected
              </Button>
            )}
            {selectedBanned.length > 0 && (
              <Button
                size="sm"
                variant="outline"
                disabled={setLoaded.isPending}
                onClick={() => setLoaded.mutate({ skillIds: selectedBanned, loaded: true })}
              >
                Load selected
              </Button>
            )}
          </div>
        )
      }
    >
      {skillsQuery.isLoading ? (
        <div className="actor-skills__empty"><Loader2 size={16} className="animate-spin" /> Loading workspace skills...</div>
      ) : !items.length ? (
        <div className="actor-skills__empty">No workspace skills.</div>
      ) : (
        <div className="actor-skills">
          <label className="actor-skills__row actor-skills__row--header">
            <input type="checkbox" checked={selected.size === items.length && items.length > 0} onChange={toggleAll} aria-label="Select all skills" />
            <span className="actor-skills__name">Name</span>
            <span className="actor-skills__description">Description</span>
            <span className="actor-skills__status">Status</span>
            <span className="actor-skills__actions" />
          </label>
          {items.map((skill) => (
            <SkillRow
              key={skill.id}
              skill={skill}
              selected={selected.has(skill.id)}
              pending={setLoaded.isPending}
              onToggle={() => toggle(skill.id)}
              onBan={() => setLoaded.mutate({ skillIds: [skill.id], loaded: false })}
              onLoad={() => setLoaded.mutate({ skillIds: [skill.id], loaded: true })}
              onView={() => setDetailSkill(skill)}
            />
          ))}
        </div>
      )}
      <SkillDetailDialog actorId={actorId} skill={detailSkill} onOpenChange={(open) => !open && setDetailSkill(null)} />
    </DenseSection>
  );
}

function SkillRow({
  skill,
  selected,
  pending,
  onToggle,
  onBan,
  onLoad,
  onView,
}: {
  skill: WorkspaceSkillSummary;
  selected: boolean;
  pending: boolean;
  onToggle: () => void;
  onBan: () => void;
  onLoad: () => void;
  onView: () => void;
}) {
  const statusLabel = skill.loaded ? "Loaded" : "Banned";
  return (
    <div className="actor-skills__row">
      <input type="checkbox" checked={selected} onChange={onToggle} aria-label={`Select ${skill.name}`} />
      <button type="button" className="actor-skills__name" onClick={onView} title="View details">
        {skill.name}
      </button>
      <button type="button" className="actor-skills__description" onClick={onView} title="View details">
        <span>{skill.description}</span>
      </button>
      <button type="button" className={`actor-skills__status is-${skill.loaded ? "loaded" : "banned"}`} onClick={onView} title="View details">
        {statusLabel}
      </button>
      <div className="actor-skills__actions">
        <Button size="icon" variant="ghost" title="View details" onClick={onView}><Eye size={14} /></Button>
        {skill.loaded ? (
          <Button size="sm" variant="outline" disabled={pending} onClick={onBan}>Ban</Button>
        ) : (
          <Button size="sm" variant="outline" disabled={pending} onClick={onLoad}>Load</Button>
        )}
      </div>
    </div>
  );
}

function SkillDetailDialog({ actorId, skill, onOpenChange }: { actorId: string; skill: WorkspaceSkillSummary | null; onOpenChange: (open: boolean) => void }) {
  const content = useQuery({
    queryKey: ["actor-skills", actorId, skill?.id, "body"],
    queryFn: () => (skill ? getActorFileContent(actorId, `.agents/skills/${skill.id}/SKILL.md`) : Promise.reject(new Error("no skill"))),
    enabled: Boolean(skill),
  });
  const parsed = useMemo(() => {
    if (!content.data) return null;
    return parseSkillMarkdown(content.data.content);
  }, [content.data]);

  return (
    <Dialog open={Boolean(skill)} onOpenChange={onOpenChange}>
      <DialogContent className="actor-skills__dialog">
        <DialogHeader>
          <DialogTitle>{skill?.name}</DialogTitle>
          <DialogDescription>{skill?.description}</DialogDescription>
        </DialogHeader>
        {content.isLoading && <div className="actor-skills__empty"><Loader2 size={16} className="animate-spin" /> Loading skill body...</div>}
        {content.error && <div className="skills-inline-error">{content.error instanceof Error ? content.error.message : String(content.error)}</div>}
        {parsed && (
          <div className="actor-skills__detail">
            <div className="actor-skills__detail-field">
              <span>Body</span>
              <pre>{parsed.body || "No body content."}</pre>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function parseSkillMarkdown(text: string): ParsedSkill {
  if (!text.startsWith("---\n")) return { body: text };
  const end = text.indexOf("\n---", 4);
  if (end === -1) return { body: text };
  return { body: text.slice(end + 4).replace(/^\n/, "") };
}

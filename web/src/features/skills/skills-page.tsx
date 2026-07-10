import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, MoreHorizontal, PackagePlus, Pencil, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  addSkillPackage,
  copySkill,
  createSkill,
  deleteSkill,
  getSkill,
  getSkillCopyPreview,
  listActors,
  listSkills,
  putSkill,
  refreshSkills,
  updateSkill,
  updateSkillPackages,
} from "@/shared/lib/api";
import type { SkillInput, SkillPackageBody, SkillRecord, SkillSummary } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { DenseSection, ErrorState, LoadingState, Page, ResourceList, ResourceListPrimary } from "@/shared/components";

const queryKey = ["skills"] as const;
const emptyDraft = { id: "", name: "", description: "", body: "" };
const emptyPackage: SkillPackageBody = { source: "", skills: [], agents: [], copy: false };
type Draft = typeof emptyDraft;

export function SkillsPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey, queryFn: listSkills });
  const actorsQuery = useQuery({ queryKey: ["actors"], queryFn: listActors });
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [newOpen, setNewOpen] = useState(false);
  const [editId, setEditId] = useState("");
  const [copyId, setCopyId] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<SkillSummary | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [packageDraft, setPackageDraft] = useState<SkillPackageBody>(emptyPackage);

  const items = query.data?.items ?? [];
  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return items.filter((skill) => {
      const matchesSource = sourceFilter === "all" || skill.source === sourceFilter;
      const matchesSearch = !term || `${skill.name} ${skill.id} ${skill.description}`.toLowerCase().includes(term);
      return matchesSource && matchesSearch;
    });
  }, [items, search, sourceFilter]);

  const refresh = useMutation({
    mutationFn: refreshSkills,
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      toast.success("Skill catalog refreshed");
    },
  });
  const updateAll = useMutation({
    mutationFn: updateSkillPackages,
    onSuccess: async (result) => {
      await client.invalidateQueries({ queryKey });
      toast.success("Package skills updated", { description: result.warning || undefined });
    },
  });
  const create = useMutation({
    mutationFn: () => createSkill(draft),
    onSuccess: async ({ record }) => {
      await client.invalidateQueries({ queryKey });
      setNewOpen(false);
      setDraft(emptyDraft);
      toast.success(`Created ${record.name}`);
    },
  });
  const addPackage = useMutation({
    mutationFn: () => addSkillPackage(packageDraft),
    onSuccess: async (result) => {
      await client.invalidateQueries({ queryKey });
      setNewOpen(false);
      setPackageDraft(emptyPackage);
      toast.success("Package source added", { description: result.warning || undefined });
    },
  });
  const remove = useMutation({
    mutationFn: (skill: SkillSummary) => deleteSkill(skill.id, skill.source),
    onSuccess: async ({ id }) => {
      await client.invalidateQueries({ queryKey });
      setDeleteTarget(null);
      toast.success(`Deleted ${id}`);
    },
  });
  const updateOne = useMutation({
    mutationFn: updateSkill,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey });
      toast.success("Package skill updated");
    },
  });

  if (query.isLoading) return <LoadingState />;

  const pageError = query.error ?? actorsQuery.error;
  return (
    <Page title="Skills" sub="One global catalog for built-in, custom, and package skills available to every Actor.">
      <DenseSection
        className="skills-catalog"
        title="Global skills"
        description={`${items.length} catalog entries. ${visible.length} shown.`}
        actions={<Button onClick={() => { setDraft(emptyDraft); setPackageDraft(emptyPackage); setNewOpen(true); }}><Plus size={14} />New Skill</Button>}
      >
        {pageError && <ErrorState error={pageError} />}
        {query.data?.warning && <div className="skills-warning" role="status"><strong>Discovery degraded.</strong> {query.data.warning}</div>}
        <div className="skills-toolbar" aria-label="Skill catalog controls">
          <label className="skills-search">
            <span className="sr-only">Search skills</span>
            <Search size={15} aria-hidden="true" />
            <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, ID, or description" />
          </label>
          <label>
            <span className="sr-only">Filter by source</span>
            <select className="input" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
              <option value="all">All sources</option>
              <option value="builtin">Built-in</option>
              <option value="custom">Custom</option>
              <option value="package">Package</option>
            </select>
          </label>
          <Button variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw size={14} />Refresh</Button>
          <Button variant="outline" disabled={updateAll.isPending} onClick={() => updateAll.mutate()}>Update all</Button>
        </div>
        {(refresh.error || updateAll.error || updateOne.error || remove.error) && <div className="skills-inline-error" role="alert">{errorText(refresh.error ?? updateAll.error ?? updateOne.error ?? remove.error)}</div>}
        <ResourceList
          className="skills-table"
          rows={visible}
          getRowId={(skill) => `${skill.source}:${skill.id}:${skill.name}`}
          emptyLabel={items.length ? "No skills match these filters." : "No global skills found. Create one manually or add a package source."}
          columns={[
            { key: "skill", label: "Skill", render: (skill) => <ResourceListPrimary title={skill.name} subtitle={skill.description || "No description provided."} /> },
            { key: "id", label: "ID", className: "skills-table__id", render: (skill) => <code>{skill.id}</code> },
            { key: "source", label: "Source", className: "skills-table__source", render: (skill) => <SourceLabel source={skill.source} error={skill.error} /> },
            { key: "actions", label: <span className="sr-only">Actions</span>, className: "is-actions", render: (skill) => (
              <div className="skills-row-actions">
                <Button size="sm" variant="outline" disabled={!skill.can_copy} onClick={() => setCopyId(skill.id)}><Copy size={13} />Copy to</Button>
                {skill.can_edit && <Button size="sm" variant="outline" onClick={async () => {
                  try {
                    const record = await getSkill(skill.id);
                    setDraft(toDraft(record));
                    setEditId(skill.id);
                  } catch (error) { toast.error(errorText(error)); }
                }}><Pencil size={13} />Edit</Button>}
                {skill.can_update && <Button size="sm" variant="outline" disabled={updateOne.isPending} onClick={() => updateOne.mutate(skill.id)}><RefreshCw size={13} />Update</Button>}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild><Button size="icon" variant="ghost" aria-label={`More actions for ${skill.name}`}><MoreHorizontal size={16} /></Button></DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem variant="destructive" disabled={!skill.can_delete} onSelect={() => setDeleteTarget(skill)}><Trash2 size={14} />Delete</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            )},
          ]}
        />
      </DenseSection>

      <NewSkillDialog open={newOpen} onOpenChange={setNewOpen} draft={draft} setDraft={setDraft} packageDraft={packageDraft} setPackageDraft={setPackageDraft} create={create} addPackage={addPackage} />
      <EditSkillDialog skillId={editId} onOpenChange={(open) => !open && setEditId("")} draft={draft} setDraft={setDraft} onSaved={async () => { await client.invalidateQueries({ queryKey }); setEditId(""); }} />
      <CopySkillDialog skillId={copyId} actors={actorsQuery.data ?? []} onOpenChange={(open) => !open && setCopyId("")} />
      <DeleteSkillDialog target={deleteTarget} pending={remove.isPending} error={remove.error} onOpenChange={(open) => !open && setDeleteTarget(null)} onDelete={() => deleteTarget && remove.mutate(deleteTarget)} />
    </Page>
  );
}

function NewSkillDialog({ open, onOpenChange, draft, setDraft, packageDraft, setPackageDraft, create, addPackage }: {
  open: boolean; onOpenChange: (open: boolean) => void; draft: Draft; setDraft: (draft: Draft) => void;
  packageDraft: SkillPackageBody; setPackageDraft: (draft: SkillPackageBody) => void;
  create: { mutate: () => void; isPending: boolean; error: unknown };
  addPackage: { mutate: () => void; isPending: boolean; error: unknown };
}) {
  const command = ["npx -y skills add", packageDraft.source || "<source>", ...packageDraft.skills.flatMap((item) => ["--skill", item]), ...packageDraft.agents.flatMap((item) => ["--agent", item]), ...(packageDraft.copy ? ["--copy"] : []), "--global --yes"].join(" ");
  return <Dialog open={open} onOpenChange={(next) => !create.isPending && !addPackage.isPending && onOpenChange(next)}><DialogContent className="skills-dialog">
    <DialogHeader><DialogTitle>New Skill</DialogTitle><DialogDescription>Create a catalog entry manually or discover skills from a package source.</DialogDescription></DialogHeader>
    <Tabs defaultValue="manual">
      <TabsList><TabsTrigger value="manual">Create manually</TabsTrigger><TabsTrigger value="package">Add from source</TabsTrigger></TabsList>
      <TabsContent value="manual" className="skills-form">
        <Field id="new-skill-id" label="Skill ID"><Input id="new-skill-id" value={draft.id} onChange={(e) => setDraft({ ...draft, id: e.target.value })} /></Field>
        <Field id="new-skill-name" label="Name"><Input id="new-skill-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
        <Field id="new-skill-description" label="Description"><Textarea id="new-skill-description" rows={2} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
        <Field id="new-skill-body" label="SKILL.md Body"><Textarea id="new-skill-body" className="font-mono" rows={12} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} /></Field>
        {Boolean(create.error) && <DialogError error={create.error} />}
        <DialogFooter><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><Button disabled={!draft.id.trim() || !draft.name.trim() || create.isPending} onClick={() => create.mutate()}>Create skill</Button></DialogFooter>
      </TabsContent>
      <TabsContent value="package" className="skills-form">
        <Field id="package-source" label="Repository, URL, or local path"><Input id="package-source" value={packageDraft.source} onChange={(e) => setPackageDraft({ ...packageDraft, source: e.target.value })} /></Field>
        <Field id="package-skills" label="Skill names" hint="Optional, comma separated."><Input id="package-skills" value={packageDraft.skills.join(", ")} onChange={(e) => setPackageDraft({ ...packageDraft, skills: splitList(e.target.value) })} /></Field>
        <Field id="package-agents" label="Agents" hint="Optional, comma separated."><Input id="package-agents" value={packageDraft.agents.join(", ")} onChange={(e) => setPackageDraft({ ...packageDraft, agents: splitList(e.target.value) })} /></Field>
        <label className="skills-checkbox"><input type="checkbox" checked={packageDraft.copy} onChange={(e) => setPackageDraft({ ...packageDraft, copy: e.target.checked })} /><span>Copy package files</span></label>
        <div className="skills-command"><span>Command</span><code>{command}</code></div>
        {Boolean(addPackage.error) && <DialogError error={addPackage.error} />}
        <DialogFooter><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><Button disabled={!packageDraft.source.trim() || addPackage.isPending} onClick={() => addPackage.mutate()}><PackagePlus size={14} />Add source</Button></DialogFooter>
      </TabsContent>
    </Tabs>
  </DialogContent></Dialog>;
}

function EditSkillDialog({ skillId, onOpenChange, draft, setDraft, onSaved }: { skillId: string; onOpenChange: (open: boolean) => void; draft: Draft; setDraft: (draft: Draft) => void; onSaved: () => Promise<void> }) {
  const save = useMutation({ mutationFn: () => putSkill(skillId, skillInput(draft)), onSuccess: async ({ record }) => { await onSaved(); toast.success(`Saved ${record.name}`); } });
  return <Dialog open={Boolean(skillId)} onOpenChange={onOpenChange}><DialogContent className="skills-dialog">
    <DialogHeader><DialogTitle>Edit {draft.name}</DialogTitle><DialogDescription>The skill ID is permanent. Changes affect future Actor prompts and copies.</DialogDescription></DialogHeader>
    <div className="skills-form">
      <Field id="edit-skill-id" label="Skill ID"><Input id="edit-skill-id" value={draft.id} disabled /></Field>
      <Field id="edit-skill-name" label="Name"><Input id="edit-skill-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
      <Field id="edit-skill-description" label="Description"><Textarea id="edit-skill-description" rows={2} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
      <Field id="edit-skill-body" label="SKILL.md Body"><Textarea id="edit-skill-body" className="font-mono" rows={13} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} /></Field>
      {save.error && <DialogError error={save.error} />}
      <DialogFooter><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><Button disabled={!draft.name.trim() || save.isPending} onClick={() => save.mutate()}>Save changes</Button></DialogFooter>
    </div>
  </DialogContent></Dialog>;
}

function CopySkillDialog({ skillId, actors, onOpenChange }: { skillId: string; actors: Array<{ id: string; name: string }>; onOpenChange: (open: boolean) => void }) {
  const [actorId, setActorId] = useState("");
  const [replace, setReplace] = useState(false);
  const preview = useQuery({ queryKey: ["skills", skillId, "copy-preview", actorId], queryFn: () => getSkillCopyPreview(skillId, actorId), enabled: Boolean(skillId && actorId) });
  const copy = useMutation({ mutationFn: () => copySkill(skillId, actorId, replace), onSuccess: (result) => { toast.success(`Copied to ${result.path}`); void preview.refetch(); setReplace(false); } });
  const data = preview.data;
  return <Dialog open={Boolean(skillId)} onOpenChange={(open) => { if (!open) { setActorId(""); setReplace(false); } onOpenChange(open); }}><DialogContent className="skills-dialog">
    <DialogHeader><DialogTitle>Copy to Actor</DialogTitle><DialogDescription>Create an independent copy of this skill in one Actor workspace.</DialogDescription></DialogHeader>
    <div className="skills-form">
      <Field id="copy-actor" label="Actor"><select id="copy-actor" className="input" value={actorId} onChange={(e) => { setActorId(e.target.value); setReplace(false); }}><option value="">Choose an Actor</option>{actors.map((actor) => <option key={actor.id} value={actor.id}>{actor.name} ({actor.id})</option>)}</select></Field>
      {!actors.length && <div className="skills-warning">No Actors are configured. Create an Actor before copying a skill.</div>}
      {preview.isFetching && <div className="skills-preview-state">Loading target preview...</div>}
      {data && <div className="skills-copy-preview" aria-live="polite">
        <div><strong>{data.up_to_date ? "Up to date" : data.conflict ? "Local changes found" : "Ready to copy"}</strong><code>{data.path}</code></div>
        <ul>{data.files.filter((file) => file.status !== "unchanged").map((file) => <li key={file.path}><span data-status={file.status}>{file.status}</span><code>{file.path}</code>{file.binary && <small>binary</small>}</li>)}</ul>
        {data.files.map((file) => file.diff && <pre key={file.path} tabIndex={0}>{file.diff}</pre>)}
        {data.conflict && <label className="skills-checkbox"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /><span>Replace the entire workspace skill directory.</span></label>}
      </div>}
      {(preview.error || copy.error) && <DialogError error={preview.error ?? copy.error} />}
      <DialogFooter><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><Button disabled={!data || data.up_to_date || copy.isPending || (data.conflict && !replace)} onClick={() => copy.mutate()}>{data?.conflict ? "Replace copy" : "Copy skill"}</Button></DialogFooter>
    </div>
  </DialogContent></Dialog>;
}

function DeleteSkillDialog({ target, pending, error, onOpenChange, onDelete }: { target: SkillSummary | null; pending: boolean; error: unknown; onOpenChange: (open: boolean) => void; onDelete: () => void }) {
  return <Dialog open={Boolean(target)} onOpenChange={onOpenChange}><DialogContent>
    <DialogHeader><DialogTitle>Delete {target?.name}?</DialogTitle><DialogDescription>{target?.source === "package" ? "This runs the package removal command and refreshes the global catalog." : target?.source === "builtin" ? "This hides the built-in skill until it is created again manually." : "This permanently removes the custom skill from the global catalog."}</DialogDescription></DialogHeader>
    {Boolean(error) && <DialogError error={error} />}
    <DialogFooter><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><Button variant="destructive" disabled={pending} onClick={onDelete}>Delete skill</Button></DialogFooter>
  </DialogContent></Dialog>;
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) { return <div className="skills-field"><label htmlFor={id}>{label}</label>{children}{hint && <small>{hint}</small>}</div>; }
function DialogError({ error }: { error: unknown }) { return <div className="skills-inline-error" role="alert">{errorText(error)}</div>; }
function SourceLabel({ source, error }: { source: SkillSummary["source"]; error: string }) { return <div className="skills-source"><span>{source === "builtin" ? "Built-in" : source === "custom" ? "Custom" : "Package"}</span>{error && <small role="alert">{error}</small>}</div>; }
function splitList(value: string) { return value.split(",").map((item) => item.trim()).filter(Boolean); }
function toDraft(record: SkillRecord): Draft { return { id: record.id, name: record.name, description: record.description, body: record.body }; }
function skillInput(skill: Draft): SkillInput { return { name: skill.name, description: skill.description, body: skill.body, scope: "global" }; }
function errorText(error: unknown): string { return error instanceof Error ? error.message : String(error ?? "Unknown error"); }

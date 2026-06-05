import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Sparkles, Trash2 } from "lucide-react";
import {
  useResourceList,
  useCreateResource,
  useDeleteResource,
  useIntegrationKinds,
} from "@/hooks/use-resources";
import type {
  ActorResource,
  CharacterResource,
  IntegrationKind,
  LLMBackendResource,
} from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";


export const Route = createFileRoute("/actors")({
  component: ActorsPage,
});

interface ActorFormData {
  name: string;
  characterId: string;
  backendId: string;
  model: string;
  maxSteps: string;
  dailyBudget: string;
  workspaceAccess: string;
  memoryEnabled: boolean;
  capabilityIds: string[];
}

const defaultForm: ActorFormData = {
  name: "",
  characterId: "",
  backendId: "",
  model: "",
  maxSteps: "20",
  dailyBudget: "10",
  workspaceAccess: "none",
  memoryEnabled: false,
  capabilityIds: [],
};

function ActorsPage() {
  const { data: actors = [], isLoading, error } = useResourceList<ActorResource>("actors");
  const { data: characters = [] } = useResourceList<CharacterResource>("characters");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: integrationKinds = [] } = useIntegrationKinds();
  const createMutation = useCreateResource<ActorResource>("actors");
  const deleteMutation = useDeleteResource("actors");

  const [form, setForm] = useState<ActorFormData>(defaultForm);
  const [formError, setFormError] = useState("");
  const selectedBackend = backends.find((backend) => backend.id === form.backendId);
  const modelOptions = uniqueModelNames([
    selectedBackend?.default_model,
    ...(selectedBackend?.models?.names ?? []),
  ]);
  const capabilityOptions = capabilityOptionsFromKinds(integrationKinds);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const character = characters.find((c) => c.id === form.characterId);
    const backend = backends.find((b) => b.id === form.backendId);
    if (!character || !backend) return;
    const model = form.model.trim();
    const dailyBudget = Number(form.dailyBudget) || 0;
    if (!model) {
      setFormError("Select a model.");
      return;
    }
    if (dailyBudget > 0 && !hasPricingForModel(backend, model)) {
      setFormError("The selected model needs backend pricing before using a USD budget.");
      return;
    }
    setFormError("");

    await createMutation.mutateAsync({
      name: form.name,
      type: "simple_loop",
      enabled: true,
      model,
      character,
      llm_backend: backend,
      character_id: character.id,
      llm_backend_id: backend.id,
      max_steps: Number(form.maxSteps) || 20,
      daily_budget: dailyBudget,
      workspace_access: form.workspaceAccess as "none" | "read_only" | "read_write",
      memory_enabled: form.memoryEnabled,
      allowed_capability_ids: form.capabilityIds,
    });
    setForm(defaultForm);
  };

  const handleDelete = (id: string) => {
    if (confirm("Delete this actor?")) deleteMutation.mutate(id);
  };

  if (isLoading) return <PageShell>Loading actors...</PageShell>;
  if (error) return <PageShell>Error: {error.message}</PageShell>;

  return (
    <PageShell>
      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Table */}
        <Card className="flex-1">
          <CardHeader>
            <CardTitle>Actors</CardTitle>
            <CardDescription>{actors.length} actors configured</CardDescription>
          </CardHeader>
          <CardContent>
            {actors.length === 0 ? (
              <Empty text="No actors yet" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Character</TableHead>
                    <TableHead>Backend</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {actors.map((actor) => (
                    <TableRow key={actor.id}>
                      <TableCell className="font-medium">
                        <Link
                          to="/actors/$id"
                          params={{ id: actor.id }}
                          className="hover:underline"
                        >
                          {actor.name}
                        </Link>
                      </TableCell>
                      <TableCell className="text-sm">{actor.character?.name ?? "—"}</TableCell>
                      <TableCell className="text-sm">{actor.llm_backend?.name ?? "—"}</TableCell>
                      <TableCell>
                        <code className="text-xs">{actor.model}</code>
                      </TableCell>
                      <TableCell>
                        <Badge variant={actor.enabled ? "default" : "secondary"}>
                          {actor.enabled ? "running" : "stopped"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Link to="/actors/$id" params={{ id: actor.id }}>
                            <Button variant="ghost" size="xs">Edit</Button>
                          </Link>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDelete(actor.id)}
                            disabled={deleteMutation.isPending}
                          >
                            <Trash2 className="size-3.5 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* Creation form */}
        <Card className="w-full lg:w-80">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="size-4" />
              Create Actor
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-4">
              <FormField label="Name" required>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                />
              </FormField>
              <FormField label="Character" required>
                <Select
                  value={form.characterId}
                  onValueChange={(v) => setForm({ ...form, characterId: v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select character" />
                  </SelectTrigger>
                  <SelectContent>
                    {characters.map((c) => (
                      <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormField>
              <FormField label="LLM Backend" required>
                <Select
                  value={form.backendId}
                  onValueChange={(v) => {
                    const backend = backends.find((bk) => bk.id === v);
                    const firstModel = backend?.models?.names?.[0] ?? "";
                    const defaultModel = backend?.default_model ?? "";
                    setForm({ ...form, backendId: v, model: defaultModel || firstModel });
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select backend" />
                  </SelectTrigger>
                  <SelectContent>
                    {backends.map((b) => (
                      <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormField>
              <FormField label="Model" required>
                <ModelSelect
                  value={form.model}
                  models={modelOptions}
                  onValueChange={(model) => setForm({ ...form, model })}
                />
              </FormField>
              <div className="grid grid-cols-2 gap-2">
                <FormField label="Max Steps">
                  <Input
                    type="number"
                    min="1"
                    value={form.maxSteps}
                    onChange={(e) => setForm({ ...form, maxSteps: e.target.value })}
                  />
                </FormField>
                <FormField label="Daily Budget">
                  <Input
                    type="number"
                    min="0"
                    step="0.01"
                    value={form.dailyBudget}
                    onChange={(e) => setForm({ ...form, dailyBudget: e.target.value })}
                  />
                </FormField>
              </div>
              <FormField label="Workspace">
                <Select
                  value={form.workspaceAccess}
                  onValueChange={(v) => setForm({ ...form, workspaceAccess: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">none</SelectItem>
                    <SelectItem value="read_only">read only</SelectItem>
                    <SelectItem value="read_write">read write</SelectItem>
                  </SelectContent>
                </Select>
              </FormField>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.memoryEnabled}
                  onChange={(e) =>
                    setForm({ ...form, memoryEnabled: e.target.checked })
                  }
                  className="size-4 rounded border-input"
                />
                Memory enabled
              </label>
              {capabilityOptions.length > 0 && (
                <FormField label="Capabilities">
                  <div className="max-h-36 space-y-2 overflow-auto rounded-md border p-2">
                    {capabilityOptions.map((capability) => (
                      <label
                        key={capability.id}
                        className="flex items-start gap-2 text-sm"
                      >
                        <input
                          type="checkbox"
                          checked={form.capabilityIds.includes(capability.id)}
                          onChange={(e) =>
                            setForm({
                              ...form,
                              capabilityIds: toggleCapabilityId(
                                form.capabilityIds,
                                capability.id,
                                e.target.checked,
                              ),
                            })
                          }
                          className="mt-0.5 size-4 rounded border-input"
                        />
                        <span className="min-w-0">
                          <span className="block font-medium">{capability.name}</span>
                          <span className="block break-all text-xs text-muted-foreground">
                            {capability.id}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                </FormField>
              )}
              {(formError || createMutation.error) && (
                <p className="text-xs text-destructive">
                  {formError || createMutation.error?.message}
                </p>
              )}
              <Button
                type="submit"
                className="w-full"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? "Creating..." : "Create Actor"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="p-6">{children}</div>;
}

function FormField({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium">
        {label}
        {required && <span className="ml-0.5 text-destructive">*</span>}
      </label>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
      <p className="text-sm">{text}</p>
    </div>
  );
}

function ModelSelect({
  value,
  models,
  onValueChange,
}: {
  value: string;
  models: string[];
  onValueChange: (value: string) => void;
}) {
  return (
    <Select
      value={value}
      onValueChange={onValueChange}
      disabled={models.length === 0}
    >
      <SelectTrigger>
        <SelectValue placeholder="Select model" />
      </SelectTrigger>
      <SelectContent>
        {models.map((model) => (
          <SelectItem key={model} value={model}>
            {model}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function hasPricingForModel(backend: LLMBackendResource, model: string): boolean {
  return backend.pricing.entries.some((entry) => entry.model === model);
}

function uniqueModelNames(models: Array<string | undefined>): string[] {
  return Array.from(
    new Set(models.map((model) => model?.trim()).filter(Boolean) as string[]),
  ).sort();
}

function capabilityOptionsFromKinds(kinds: IntegrationKind[]) {
  const seen = new Set<string>();
  return kinds
    .flatMap((kind) =>
      kind.capabilities.map((capability) => ({
        ...capability,
        name: capability.name || capability.id,
      })),
    )
    .filter((capability) => {
      if (seen.has(capability.id)) {
        return false;
      }
      seen.add(capability.id);
      return true;
    })
    .sort((a, b) => a.id.localeCompare(b.id));
}

function toggleCapabilityId(
  ids: string[],
  id: string,
  checked: boolean,
): string[] {
  if (checked) {
    return Array.from(new Set([...ids, id])).sort();
  }
  return ids.filter((value) => value !== id);
}

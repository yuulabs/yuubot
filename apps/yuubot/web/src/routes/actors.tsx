import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Sparkles, Trash2 } from "lucide-react";
import {
  useResourceList,
  useCreateResource,
  useDeleteResource,
} from "@/hooks/use-resources";
import type {
  ActorResource,
  CapabilitySetResource,
  CharacterResource,
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
  capabilitySetId: string;
  model: string;
  maxSteps: string;
}

const defaultForm: ActorFormData = {
  name: "",
  characterId: "",
  backendId: "",
  capabilitySetId: "",
  model: "",
  maxSteps: "20",
};

function ActorsPage() {
  const { data: actors = [], isLoading, error } = useResourceList<ActorResource>("actors");
  const { data: characters = [] } = useResourceList<CharacterResource>("characters");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: capabilitySets = [] } =
    useResourceList<CapabilitySetResource>("capability-sets");
  const createMutation = useCreateResource<ActorResource>("actors");
  const deleteMutation = useDeleteResource("actors");

  const [form, setForm] = useState<ActorFormData>(defaultForm);
  const [formError, setFormError] = useState("");
  const selectedBackend = backends.find((backend) => backend.id === form.backendId);
  const modelOptions = uniqueModelNames([
    selectedBackend?.default_model,
    ...(selectedBackend?.models?.names ?? []),
  ]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const character = characters.find((c) => c.id === form.characterId);
    const backend = backends.find((b) => b.id === form.backendId);
    if (!character || !backend) return;
    const model = form.model.trim();
    if (!model) {
      setFormError("Select a model.");
      return;
    }
    if (!form.capabilitySetId) {
      setFormError("Select a capability set.");
      return;
    }
    setFormError("");

    await createMutation.mutateAsync({
      name: form.name,
      type: "simple_loop",
      enabled: true,
      default_model: model,
      default_character_id: character.id,
      default_llm_backend_id: backend.id,
      capability_set_id: form.capabilitySetId,
      default_budget: { max_steps: Number(form.maxSteps) || 20 },
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
                    <TableHead>Capability Set</TableHead>
                    <TableHead>Workspace</TableHead>
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
                      <TableCell className="text-sm">{actor.default_character?.name ?? "—"}</TableCell>
                      <TableCell className="text-sm">{actor.default_llm_backend?.name ?? "—"}</TableCell>
                      <TableCell>
                        <code className="text-xs">{actor.default_model}</code>
                      </TableCell>
                      <TableCell className="text-sm">{actor.capability_set?.name ?? "—"}</TableCell>
                      <TableCell className="text-sm">
                        {actor.capability_set?.workspace_path ? (
                          <a
                            href={`/workspace/${actor.capability_set.workspace_path}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
                          >
                            Open
                          </a>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
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
              <FormField label="Max Steps">
                <Input
                  type="number"
                  min="1"
                  value={form.maxSteps}
                  onChange={(e) => setForm({ ...form, maxSteps: e.target.value })}
                />
              </FormField>
              <FormField label="Capability Set" required>
                {capabilitySets.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    No capability sets yet.{" "}
                    <Link to="/capability-sets" className="underline">
                      Create one first
                    </Link>
                    .
                  </p>
                ) : (
                  <Select
                    value={form.capabilitySetId}
                    onValueChange={(v) => setForm({ ...form, capabilitySetId: v })}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select capability set" />
                    </SelectTrigger>
                    <SelectContent>
                      {capabilitySets.map((cs) => (
                        <SelectItem key={cs.id} value={cs.id}>{cs.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </FormField>
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

function uniqueModelNames(models: Array<string | undefined>): string[] {
  return Array.from(
    new Set(models.map((model) => model?.trim()).filter(Boolean) as string[]),
  ).sort();
}

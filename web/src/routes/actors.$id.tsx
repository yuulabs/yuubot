import { useEffect, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Trash2 } from "lucide-react";
import {
  useResourceList,
  useSetResourceEnabled,
  useDeleteResource,
  useUpdateResource,
} from "@/hooks/use-resources";
import type { ActorResource, LLMBackendResource } from "@/types/api";
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
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";

export const Route = createFileRoute("/actors/$id")({
  component: ActorDetailPage,
});

function ActorDetailPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const toggleMutation = useSetResourceEnabled("actors");
  const deleteMutation = useDeleteResource("actors");
  const updateMutation = useUpdateResource<ActorResource>("actors");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");

  const actor = actors.find((a) => a.id === id);
  const backend = backends.find((item) => item.id === actor?.llm_backend?.id);
  const modelOptions = uniqueModelNames([
    backend?.default_model,
    ...(backend?.models?.names ?? []),
    actor?.model,
  ]);

  const [editName, setEditName] = useState(actor?.name ?? "");
  const [editModel, setEditModel] = useState(actor?.model ?? "");
  const [saveError, setSaveError] = useState("");
  const allowedCapabilityIds =
    actor?.allowed_capability_ids ?? actor?.capability_ids ?? [];

  useEffect(() => {
    if (!actor) {
      return;
    }
    setEditName(actor.name);
    setEditModel(actor.model);
    setSaveError("");
  }, [actor]);

  if (!actor) {
    return (
      <div className="p-6">
        <Link to="/actors" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Back to actors
        </Link>
        <p className="text-muted-foreground">Actor not found.</p>
      </div>
    );
  }

  const handleToggle = () => {
    toggleMutation.mutate({ id: actor.id, enabled: !actor.enabled });
  };

  const handleDelete = () => {
    if (confirm(`Delete actor "${actor.name}"?`)) {
      deleteMutation.mutate(actor.id, {
        onSuccess: () => navigate({ to: "/actors" }),
      });
    }
  };

  const handleSave = async () => {
    if (!editModel.trim()) {
      setSaveError("Select a model.");
      return;
    }
    if ((actor.daily_budget ?? 0) > 0 && backend && !hasPricingForModel(backend, editModel)) {
      setSaveError("The selected model needs backend pricing before using a USD budget.");
      return;
    }
    setSaveError("");
    await updateMutation.mutateAsync({
      id: actor.id,
      data: { name: editName, model: editModel },
    });
  };

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/actors">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="size-4" />
            </Button>
          </Link>
          <div>
            <h1 className="text-xl font-bold">{actor.name}</h1>
            <p className="text-sm text-muted-foreground">Actor ID: {actor.id}</p>
          </div>
        </div>
        <Badge variant={actor.enabled ? "default" : "secondary"}>
          {actor.enabled ? "running" : "stopped"}
        </Badge>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Detail card */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Details</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableBody>
                <TableRow>
                  <TableCell className="font-medium">Name</TableCell>
                  <TableCell>{actor.name}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Type</TableCell>
                  <TableCell>{actor.type}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Model</TableCell>
                  <TableCell><code>{actor.model}</code></TableCell>
                </TableRow>
                {actor.character && (
                  <TableRow>
                    <TableCell className="font-medium">Character</TableCell>
                    <TableCell>
                      <Link
                        to="/characters/$id"
                        params={{ id: actor.character.id }}
                        className="hover:underline"
                      >
                        {actor.character.name}
                      </Link>
                    </TableCell>
                  </TableRow>
                )}
                {actor.llm_backend && (
                  <TableRow>
                    <TableCell className="font-medium">LLM Backend</TableCell>
                    <TableCell>
                      <Link
                        to="/providers/$id"
                        params={{ id: actor.llm_backend.id }}
                        className="hover:underline"
                      >
                        {actor.llm_backend.name}
                      </Link>
                      <span className="ml-2 text-xs text-muted-foreground">
                        ({actor.llm_backend.provider})
                      </span>
                    </TableCell>
                  </TableRow>
                )}
                <TableRow>
                  <TableCell className="font-medium">Max Steps</TableCell>
                  <TableCell>{actor.max_steps ?? "unlimited"}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Daily Budget</TableCell>
                  <TableCell>${actor.daily_budget ?? "unlimited"}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Workspace</TableCell>
                  <TableCell>{actor.workspace_access ?? "none"}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Memory</TableCell>
                  <TableCell>{actor.memory_enabled ? "enabled" : "disabled"}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Capabilities</TableCell>
                  <TableCell>
                    {allowedCapabilityIds.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {allowedCapabilityIds.map((capabilityId) => (
                          <Badge key={capabilityId} variant="secondary">
                            {capabilityId}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      "none"
                    )}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* Actions card */}
        <Card>
          <CardHeader>
            <CardTitle>Actions</CardTitle>
            <CardDescription>Manage this actor</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Name</label>
              <Input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Model</label>
              <ModelSelect
                value={editModel}
                models={modelOptions}
                onValueChange={setEditModel}
              />
            </div>
            {(saveError || updateMutation.error) && (
              <p className="text-xs text-destructive">
                {saveError || updateMutation.error?.message}
              </p>
            )}
            <Button
              onClick={handleSave}
              className="w-full"
              disabled={updateMutation.isPending}
            >
              {updateMutation.isPending ? "Saving..." : "Save Changes"}
            </Button>
            <Button
              variant="outline"
              className="w-full"
              onClick={handleToggle}
              disabled={toggleMutation.isPending}
            >
              {actor.enabled ? "Disable" : "Enable"} Actor
            </Button>
            <Button
              variant="destructive"
              className="w-full"
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="size-4" />
              Delete Actor
            </Button>
          </CardContent>
        </Card>
      </div>
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
      <SelectTrigger className="w-full">
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
  return backend.pricing.entries.some((entry) => entry.model === model.trim());
}

function uniqueModelNames(models: Array<string | undefined>): string[] {
  return Array.from(
    new Set(models.map((model) => model?.trim()).filter(Boolean) as string[]),
  ).sort();
}

import { useEffect, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Trash2, MessageSquare } from "lucide-react";
import {
  useResourceList,
  useSetResourceEnabled,
  useDeleteResource,
  useUpdateResource,
} from "@/hooks/use-resources";
import type { ActorResource, CharacterResource, ConversationListItem, LLMBackendResource } from "@/types/api";
import { listConversations } from "@/lib/api";
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
import { Textarea } from "@/components/ui/textarea";

export const Route = createFileRoute("/actors/$id")({
  component: ActorDetailPage,
});

function ActorDetailPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: characters = [] } = useResourceList<CharacterResource>("characters");
  const toggleMutation = useSetResourceEnabled("actors");
  const deleteMutation = useDeleteResource("actors");
  const updateMutation = useUpdateResource<ActorResource>("actors");
  const updateCharacterMutation = useUpdateResource<CharacterResource>("characters");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");

  const actor = actors.find((a) => a.id === id);
  const character = characters.find((c) => c.id === actor?.default_character?.id);
  const backend = backends.find((item) => item.id === actor?.default_llm_backend?.id);
  const modelOptions = uniqueModelNames([
    backend?.default_model,
    ...(backend?.models?.names ?? []),
    actor?.default_model,
  ]);

  const [editName, setEditName] = useState(actor?.name ?? "");
  const [editModel, setEditModel] = useState(actor?.default_model ?? "");
  const [editCharacterPrompt, setEditCharacterPrompt] = useState(character?.system_prompt ?? "");
  const [saveError, setSaveError] = useState("");
  // ISSUE-0010: per-Actor historical conversations. Pure client-side filter
  // of listConversations() by actor_id — no new endpoint.
  const [actorConversations, setActorConversations] = useState<ConversationListItem[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);

  useEffect(() => {
    if (!actor) {
      return;
    }
    setEditName(actor.name);
    setEditModel(actor.default_model);
    setSaveError("");
  }, [actor]);

  useEffect(() => {
    if (character) {
      setEditCharacterPrompt(character.system_prompt);
    }
  }, [character]);

  useEffect(() => {
    if (!actor) {
      return;
    }
    let cancelled = false;
    setConversationsLoading(true);
    void (async () => {
      try {
        const all = await listConversations();
        if (cancelled) return;
        const mine = all
          .filter((c) => c.actor_id === actor.id)
          .sort((left, right) => conversationTime(right) - conversationTime(left));
        setActorConversations(mine);
      } catch {
        if (!cancelled) setActorConversations([]);
      } finally {
        if (!cancelled) setConversationsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
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
    setSaveError("");
    // Fold character under Actor (ISSUE-0011): the persona prompt lives on the
    // Actor's underlying Character record, edited inline here — no Character
    // top-level page is involved from the researcher's view.
    if (character && editCharacterPrompt !== character.system_prompt) {
      await updateCharacterMutation.mutateAsync({
        id: character.id,
        data: { system_prompt: editCharacterPrompt },
      });
    }
    await updateMutation.mutateAsync({
      id: actor.id,
      data: { name: editName, default_model: editModel },
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
                  <TableCell><code>{actor.default_model}</code></TableCell>
                </TableRow>
                {actor.default_character && (
                  <TableRow>
                    <TableCell className="font-medium">Character</TableCell>
                    <TableCell>{actor.default_character.name}</TableCell>
                  </TableRow>
                )}
                {actor.capability_set && (
                  <TableRow>
                    <TableCell className="font-medium">Capability Set</TableCell>
                    <TableCell>
                      <Link
                        to="/capability-sets"
                        className="hover:underline"
                      >
                        {actor.capability_set.name}
                      </Link>
                    </TableCell>
                  </TableRow>
                )}
                {actor.default_llm_backend && (
                  <TableRow>
                    <TableCell className="font-medium">LLM Backend</TableCell>
                    <TableCell>
                      <Link
                        to="/providers/$id"
                        params={{ id: actor.default_llm_backend.id }}
                        className="hover:underline"
                      >
                        {actor.default_llm_backend.name}
                      </Link>
                      <span className="ml-2 text-xs text-muted-foreground">
                        ({actor.default_llm_backend.yuuagents_provider})
                      </span>
                    </TableCell>
                  </TableRow>
                )}
                <TableRow>
                  <TableCell className="font-medium">Max Steps</TableCell>
                  <TableCell>{actor.default_budget?.max_steps ?? "unlimited"}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Workspace</TableCell>
                  <TableCell>{actor.capability_set?.workspace_path || "none"}</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Memory</TableCell>
                  <TableCell>
                    {actor.capability_set?.runtime_policy?.memory_enabled
                      ? "enabled"
                      : "disabled"}
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Capabilities</TableCell>
                  <TableCell>
                    {(actor.capability_set?.integration_capability_ids ?? []).length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {(actor.capability_set?.integration_capability_ids ?? []).map(
                          (capabilityId) => (
                            <Badge key={capabilityId} variant="secondary">
                              {capabilityId}
                            </Badge>
                          ),
                        )}
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
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Character Prompt</label>
              <Textarea
                value={editCharacterPrompt}
                onChange={(e) => setEditCharacterPrompt(e.target.value)}
                rows={8}
                className="font-mono text-sm"
                placeholder="System prompt first section — this Actor's persona."
                disabled={!character}
              />
              {!character && (
                <p className="text-xs text-muted-foreground">
                  This Actor has no persona character attached.
                </p>
              )}
            </div>
            {(saveError || updateMutation.error || updateCharacterMutation.error) && (
              <p className="text-xs text-destructive">
                {saveError || updateMutation.error?.message || updateCharacterMutation.error?.message}
              </p>
            )}
            <Button
              onClick={handleSave}
              className="w-full"
              disabled={updateMutation.isPending || updateCharacterMutation.isPending}
            >
              {(updateMutation.isPending || updateCharacterMutation.isPending) ? "Saving..." : "Save Changes"}
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

      {/* ISSUE-0010: this Actor's historical conversations. The Actor is the
          sole conversation entry point — creation happens via the
          actor-bound draft route below, and prior conversations are listed
          here (client-side filter of listConversations() by actor_id). */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Conversations</CardTitle>
            <CardDescription>
              {conversationsLoading ? "Loading…" : `${actorConversations.length} conversation${actorConversations.length === 1 ? "" : "s"}`}
            </CardDescription>
          </div>
          <Link to="/admin/conversations/$conversationId" params={{ conversationId: `actor-${actor.id}` }}>
            <Button variant="outline" size="sm">
              <MessageSquare className="mr-1.5 size-3.5" />
              Start conversation
            </Button>
          </Link>
        </CardHeader>
        <CardContent>
          {conversationsLoading ? (
            <p className="text-xs text-muted-foreground">Loading conversations…</p>
          ) : actorConversations.length === 0 ? (
            <p className="text-xs text-muted-foreground">No conversations yet. Start one above.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Conversation</TableHead>
                  <TableHead>Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {actorConversations.map((c) => (
                  <TableRow key={c.conversation_id}>
                    <TableCell className="font-mono text-xs">
                      <Link
                        to="/admin/conversations/$conversationId"
                        params={{ conversationId: c.conversation_id }}
                        className="text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
                      >
                        {c.conversation_id}
                      </Link>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {c.updated_at ? formatConversationTime(c.updated_at) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
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

function uniqueModelNames(models: Array<string | undefined>): string[] {
  return Array.from(
    new Set(models.map((model) => model?.trim()).filter(Boolean) as string[]),
  ).sort();
}

function conversationTime(conversation: ConversationListItem): number {
  const value = conversation.updated_at ?? conversation.created_at;
  if (!value) {
    return 0;
  }
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function formatConversationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

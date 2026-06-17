import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  ArrowUpRight,
  BookOpen,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Lightbulb,
  Trash2,
} from "lucide-react";
import { useResourceList, useCreateResource, useDeleteResource, useIntegrationKinds } from "@/hooks/use-resources";
import type { ActorIngressRuleResource, ActorResource, IntegrationResource } from "@/types/api";
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
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export const Route = createFileRoute("/routes")({
  component: RoutesPage,
});

interface RuleFormData {
  actorId: string;
  sourceIdPattern: string;
  sourcePathPattern: string;
  kindPatterns: string;
}

const defaultForm: RuleFormData = {
  actorId: "",
  sourceIdPattern: "im",
  sourcePathPattern: "**",
  kindPatterns: "*",
};

function RoutesPage() {
  const { data: rules = [], isLoading, error } = useResourceList<ActorIngressRuleResource>("ingress-rules");
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: integrations = [] } = useResourceList<IntegrationResource>("integrations");
  const { data: integrationKinds = [] } = useIntegrationKinds();
  const createMutation = useCreateResource<ActorIngressRuleResource>("ingress-rules");
  const deleteMutation = useDeleteResource("ingress-rules");

  const [form, setForm] = useState<RuleFormData>(defaultForm);

  // Derive the integration kind convention from the current sourceIdPattern.
  // If the pattern is a plain ID (not a glob), look up its integration kind
  // and show the path construction convention.
  const matchingKind = form.sourceIdPattern.includes("*")
    ? null
    : (() => {
        const integ = integrations.find((i) => i.id === form.sourceIdPattern);
        if (!integ) return null;
        return integrationKinds.find((k) => k.name === integ.name) ?? null;
      })();

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const kindPatterns = form.kindPatterns
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean);
    await createMutation.mutateAsync({
      source_id_pattern: form.sourceIdPattern,
      source_path_pattern: form.sourcePathPattern,
      kind_patterns: kindPatterns.length > 0 ? kindPatterns : ["*"],
      actor_id: form.actorId,
      enabled: true,
    });
    setForm(defaultForm);
  };

  const handleDelete = (id: string) => {
    if (confirm("Delete this rule?")) deleteMutation.mutate(id);
  };

  const actorName = (actorId: string) =>
    actors.find((a) => a.id === actorId)?.name ?? actorId;

  if (isLoading) return <PageShell>Loading ingress rules...</PageShell>;
  if (error) return <PageShell>Error: {error.message}</PageShell>;

  return (
    <PageShell>
      {/* ── Tutorial / Onboarding ── */}
      <IngressRuleTutorial integrations={integrations} />

      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Table */}
        <Card className="flex-1">
          <CardHeader>
            <CardTitle>Ingress Rules</CardTitle>
            <CardDescription>{rules.length} routing entries</CardDescription>
          </CardHeader>
          <CardContent>
            {rules.length === 0 ? (
              <Empty text="No ingress rules configured — create one using the form on the right" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Source ID</TableHead>
                    <TableHead>Path</TableHead>
                    <TableHead>Kinds</TableHead>
                    <TableHead>Actor</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rules.map((rule) => (
                    <TableRow key={rule.id}>
                      <TableCell className="font-mono text-xs">
                        {rule.source_id_pattern}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {rule.source_path_pattern}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {rule.kind_patterns.map((k) => (
                            <Badge key={k} variant="outline" className="text-xs">
                              {k}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell className="text-sm">
                        {actorName(rule.actor_id)}
                      </TableCell>
                      <TableCell>
                        <Badge variant={rule.enabled ? "default" : "secondary"}>
                          {rule.enabled ? "active" : "inactive"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDelete(rule.id)}
                          disabled={deleteMutation.isPending}
                        >
                          <Trash2 className="size-3.5 text-destructive" />
                        </Button>
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
              <GitBranch className="size-4" />
              New Rule
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-medium">
                  Actor<span className="ml-0.5 text-destructive">*</span>
                </label>
                <Select
                  value={form.actorId}
                  onValueChange={(v) => setForm({ ...form, actorId: v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select actor" />
                  </SelectTrigger>
                  <SelectContent>
                    {actors.map((a) => (
                      <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Which actor should receive matching messages?
                </p>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Source ID</label>
                <Input
                  value={form.sourceIdPattern}
                  onChange={(e) =>
                    setForm({ ...form, sourceIdPattern: e.target.value })
                  }
                  placeholder='e.g. "slack-main", "telegram-*", "*"'
                />
                <p className="text-xs text-muted-foreground">
                  Glob pattern matching the integration's own ID.{" "}
                  <code className="text-[10px] bg-muted px-1 rounded">*</code>{" "}
                  matches all integrations.
                </p>
                {integrations.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    <span className="text-[10px] text-muted-foreground mr-0.5 self-center">
                      Available:
                    </span>
                    {integrations.map((int) => (
                      <button
                        key={int.id}
                        type="button"
                        onClick={() =>
                          setForm({ ...form, sourceIdPattern: int.id })
                        }
                        className="text-[10px] font-mono bg-muted px-1.5 py-0.5 rounded hover:bg-muted/80 transition-colors"
                      >
                        {int.id}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Source Path</label>
                <Input
                  value={form.sourcePathPattern}
                  onChange={(e) =>
                    setForm({ ...form, sourcePathPattern: e.target.value })
                  }
                  placeholder='e.g. "channels/*", "repos/**", "**"'
                />
                <p className="text-xs text-muted-foreground">
                  Glob pattern for the path inside the integration (channel,
                  repo, etc.).{" "}
                  <code className="text-[10px] bg-muted px-1 rounded">**</code>{" "}
                  matches everything.
                </p>
                {matchingKind?.source_path_convention && (
                  <div className="mt-1.5 rounded border border-muted bg-muted/20 p-2">
                    <p className="text-[10px] font-medium text-muted-foreground mb-0.5">
                      Path convention for <code className="text-[10px]">{matchingKind.name}</code>:
                    </p>
                    <p className="text-[10px] text-muted-foreground whitespace-pre-wrap">
                      {matchingKind.source_path_convention}
                    </p>
                  </div>
                )}
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Kinds</label>
                <Input
                  value={form.kindPatterns}
                  onChange={(e) =>
                    setForm({ ...form, kindPatterns: e.target.value })
                  }
                  placeholder="mention, dm, text, *"
                />
                <p className="text-xs text-muted-foreground">
                  Comma-separated glob patterns for message kinds.{" "}
                  <code className="text-[10px] bg-muted px-1 rounded">*</code>{" "}
                  matches all kinds.
                </p>
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? "Saving..." : "Create Rule"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}

// ---------------------------------------------------------------------------
// Tutorial / Onboarding
// ---------------------------------------------------------------------------

function IngressRuleTutorial({ integrations }: { integrations: IntegrationResource[] }) {
  const [open, setOpen] = useState(false);

  return (
    <Card className="mb-6">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between p-4 text-left cursor-pointer hover:bg-muted/30 transition-colors rounded-lg"
      >
        <div className="flex items-center gap-2">
          <BookOpen className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            How Ingress Rules Work
          </span>
          <Badge variant="outline" className="text-[10px] font-normal">
            Tutorial
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <a
            href="/tutorials/ingress-rules.html"
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Interactive Guide
            <ArrowUpRight className="size-3" />
          </a>
          {open ? (
            <ChevronDown className="size-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-4 text-muted-foreground" />
          )}
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-4 text-sm text-muted-foreground">
          <Separator />

          {/* Concept */}
          <div>
            <h4 className="font-medium text-foreground mb-1">
              What is an Ingress Rule?
            </h4>
            <p>
              An Ingress Rule is a routing declaration that tells the system
              <strong className="text-foreground"> which Actor</strong> should
              receive messages from{" "}
              <strong className="text-foreground">which source</strong>. Think
              of it as a traffic sign:&nbsp;
              <em>"messages from Slack channel #dev → go to Developer Bot"</em>.
            </p>
          </div>

          {/* Flow diagram */}
          <div className="flex items-center justify-center gap-2 py-2 text-xs flex-wrap">
            <span className="rounded border px-2 py-1 font-mono bg-muted/50">
              Integration
            </span>
            <span className="text-muted-foreground">→</span>
            <span className="rounded border px-2 py-1 font-mono bg-primary/10 border-primary/30 text-primary">
              Ingress Rule
            </span>
            <span className="text-muted-foreground">→</span>
            <span className="rounded border px-2 py-1 font-mono bg-muted/50">
              Actor Mailbox
            </span>
            <span className="text-muted-foreground">→</span>
            <span className="rounded border px-2 py-1 font-mono bg-muted/50">
              Actor Runtime
            </span>
          </div>

          {/* Key insight: source ID = integration ID */}
          <div className="rounded bg-primary/5 border border-primary/20 p-3">
            <p className="text-xs">
              <strong className="text-foreground">💡 核心：Source ID = 集成的 ID</strong>
              <br />
              当你创建一个集成（Integration）时，你为它指定的 ID（例如{" "}
              <code className="text-[10px] bg-muted px-0.5">slack-main</code>
              、<code className="text-[10px] bg-muted px-0.5">qq-cs</code>
              ），就是每条消息的 <code className="text-[10px] bg-muted px-0.5">source_id</code>。
              集成发送消息时，这个 ID 会被自动打标到消息来源中。
            </p>
            {integrations.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                <span className="text-[10px] text-muted-foreground mr-0.5">
                  当前已有的集成：
                </span>
                {integrations.map((int) => (
                  <span
                    key={int.id}
                    className="text-[10px] font-mono bg-muted px-1.5 py-0.5 rounded"
                  >
                    {int.id}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Three fields */}
          <div>
            <h4 className="font-medium text-foreground mb-2">
              The Three Matching Fields
            </h4>
            <div className="space-y-2">
              <div className="rounded border p-2.5">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <code className="text-[11px] bg-muted px-1 rounded font-semibold text-foreground">
                    source_id_pattern
                  </code>
                </div>
                <p className="text-xs">
                  Matches the integration that sent the message (e.g.&nbsp;
                  <code className="text-[10px] bg-muted px-0.5">slack-main</code>
                  ,&nbsp;
                  <code className="text-[10px] bg-muted px-0.5">telegram-*</code>
                  ). Use <code className="text-[10px] bg-muted px-0.5">*</code>{" "}
                  to match all integrations.
                  <br />
                  <span className="text-muted-foreground/70">
                    → 去 <a href="/integrations" className="text-primary underline">Integrations</a> 页面查看已创建的集成及其 ID。
                  </span>
                </p>
              </div>
              <div className="rounded border p-2.5">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <code className="text-[11px] bg-muted px-1 rounded font-semibold text-foreground">
                    source_path_pattern
                  </code>
                </div>
                <p className="text-xs">
                  Matches the channel, group, or repo path inside the
                  integration (e.g.&nbsp;
                  <code className="text-[10px] bg-muted px-0.5">channels/dev</code>
                  ,&nbsp;
                  <code className="text-[10px] bg-muted px-0.5">repos/**</code>
                  ). Use <code className="text-[10px] bg-muted px-0.5">**</code>{" "}
                  to match all paths.
                </p>
              </div>
              <div className="rounded border p-2.5">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <code className="text-[11px] bg-muted px-1 rounded font-semibold text-foreground">
                    kind_patterns
                  </code>
                </div>
                <p className="text-xs">
                  Filters by message kind —{" "}
                  <code className="text-[10px] bg-muted px-0.5">mention</code>
                  , <code className="text-[10px] bg-muted px-0.5">dm</code>,
                  <code className="text-[10px] bg-muted px-0.5">text</code>,
                  etc. Multiple kinds can be set (comma-separated). Use{" "}
                  <code className="text-[10px] bg-muted px-0.5">*</code> to
                  match all kinds.
                </p>
              </div>
            </div>
          </div>

          {/* Glob quick reference */}
          <div>
            <h4 className="font-medium text-foreground mb-1">
              Glob Pattern Quick Reference
            </h4>
            <table className="text-xs w-full border-collapse">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-1 pr-3 font-medium">Pattern</th>
                  <th className="text-left py-1 font-medium">Meaning</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-muted/50">
                  <td className="py-1 pr-3 font-mono">*</td>
                  <td>Matches any single path segment</td>
                </tr>
                <tr className="border-b border-muted/50">
                  <td className="py-1 pr-3 font-mono">**</td>
                  <td>Matches any number of segments (including none)</td>
                </tr>
                <tr className="border-b border-muted/50">
                  <td className="py-1 pr-3 font-mono">slack-*</td>
                  <td>Matches "slack-main", "slack-dev", etc.</td>
                </tr>
                <tr className="border-b border-muted/50">
                  <td className="py-1 pr-3 font-mono">channels/*</td>
                  <td>Matches "channels/dev", "channels/general", etc.</td>
                </tr>
                <tr>
                  <td className="py-1 pr-3 font-mono">repos/**</td>
                  <td>Matches "repos/org/repo", "repos/org/a/b", etc.</td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Example scenarios */}
          <div>
            <h4 className="font-medium text-foreground mb-2">
              Common Scenarios
            </h4>
            <div className="space-y-2">
              <div className="rounded border border-muted p-2.5">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <Lightbulb className="size-3.5 text-amber-500" />
                  <span className="font-medium text-foreground text-xs">
                    Route all messages from a specific integration
                  </span>
                </div>
                <p className="text-xs">
                  Source ID ={" "}
                  <code className="text-[10px] bg-muted px-0.5">qq-main</code>,
                  Source Path ={" "}
                  <code className="text-[10px] bg-muted px-0.5">**</code>,
                  Kinds ={" "}
                  <code className="text-[10px] bg-muted px-0.5">*</code>
                </p>
              </div>
              <div className="rounded border border-muted p-2.5">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <Lightbulb className="size-3.5 text-amber-500" />
                  <span className="font-medium text-foreground text-xs">
                    Listen to mentions from any integration
                  </span>
                </div>
                <p className="text-xs">
                  Source ID ={" "}
                  <code className="text-[10px] bg-muted px-0.5">*</code>, Source
                  Path ={" "}
                  <code className="text-[10px] bg-muted px-0.5">**</code>,
                  Kinds ={" "}
                  <code className="text-[10px] bg-muted px-0.5">mention</code>
                </p>
              </div>
              <div className="rounded border border-muted p-2.5">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <Lightbulb className="size-3.5 text-amber-500" />
                  <span className="font-medium text-foreground text-xs">
                    Fan-out: send to multiple actors
                  </span>
                </div>
                <p className="text-xs">
                  Create two rules with the same source but different actors.
                  Messages matching both rules reach{" "}
                  <em className="text-foreground">both</em> actors.
                </p>
              </div>
            </div>
          </div>

          {/* Note about system messages */}
          <div className="rounded bg-muted/30 p-2.5 border">
            <p className="text-xs">
              <strong className="text-foreground">Note:</strong> System messages
              (Admin Conversation, scheduled tasks, bridge callbacks) bypass
              Ingress Rules entirely — they are delivered directly to the
              Actor's mailbox via a dedicated system channel.
            </p>
          </div>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="p-6">{children}</div>;
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
      <p className="text-sm">{text}</p>
    </div>
  );
}

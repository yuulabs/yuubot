import { createFileRoute, Link } from "@tanstack/react-router";
import { Activity, Plug, Route as RouteIcon, Zap } from "lucide-react";
import { useHealth } from "@/hooks/use-resources";
import { useResourceList } from "@/hooks/use-resources";
import type { ActorResource, ActorIngressRuleResource, LLMBackendResource, CharacterResource } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export const Route = createFileRoute("/")({
  component: DashboardPage,
});

function DashboardPage() {
  const { data: _health } = useHealth();
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: integrations = [] } = useResourceList("integrations");
  const { data: rules = [] } = useResourceList<ActorIngressRuleResource>("ingress-rules");
  const { data: characters = [] } = useResourceList<CharacterResource>("characters");

  const runningActors = actors.filter((a) => a.enabled).length;

  return (
    <div className="space-y-6 p-6">
      {/* Stats row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          icon={Activity}
          label="Active Actors"
          value={runningActors}
          sub={`of ${actors.length} total`}
        />
        <StatCard
          icon={Zap}
          label="LLM Backends"
          value={backends.length}
          sub="configured"
        />
        <StatCard
          icon={Plug}
          label="Integrations"
          value={integrations.length}
          sub="runtime records"
        />
        <StatCard
          icon={RouteIcon}
          label="Ingress Rules"
          value={rules.length}
          sub="routing entries"
        />
      </div>

      {/* Actor status + Launch path */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>Actor Status</CardTitle>
              <CardDescription>Running and configured actors</CardDescription>
            </div>
            <Link to="/actors">
              <Button variant="outline" size="sm">Manage</Button>
            </Link>
          </CardHeader>
          <CardContent>
            {actors.length === 0 ? (
              <Empty text="No actors yet" />
            ) : (
              <div className="space-y-2">
                {actors.slice(0, 8).map((actor) => (
                  <ActorStatusRow key={actor.id} actor={actor} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>Launch Path</CardTitle>
              <CardDescription>Checklist to get started</CardDescription>
            </div>
            <Link to="/actors">
              <Button variant="outline" size="sm">Create actor</Button>
            </Link>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <PathStep done={backends.length > 0} label="LLM backend" />
              <PathStep done={characters.length > 0} label="Character" />
              <PathStep done={actors.length > 0} label="Actor" />
              <PathStep done={rules.length > 0} label="Ingress rule" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Ingress rules table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Ingress Rules</CardTitle>
            <CardDescription>Message routing configuration</CardDescription>
          </div>
          <Link to="/routes">
            <Button variant="outline" size="sm">Edit rules</Button>
          </Link>
        </CardHeader>
        <CardContent>
          <IngressRulesTable rules={rules} actors={actors} />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: React.ComponentType<{ className?: string; size?: number }>;
  label: string;
  value: string | number;
  sub: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 pt-6">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <Icon className="size-5 text-primary" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <p className="text-2xl font-bold">{value}</p>
          <p className="text-xs text-muted-foreground">{sub}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function ActorStatusRow({ actor }: { actor: ActorResource }) {
  return (
    <div className="flex items-center justify-between rounded-md border px-3 py-2">
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium">{actor.name}</span>
        <code className="text-xs text-muted-foreground">{actor.model}</code>
      </div>
      <Badge variant={actor.enabled ? "default" : "secondary"}>
        {actor.enabled ? "running" : "stopped"}
      </Badge>
    </div>
  );
}

function PathStep({ done, label }: { done: boolean; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <div
        className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
          done
            ? "bg-primary text-primary-foreground"
            : "border-2 border-muted-foreground/30 text-muted-foreground"
        }`}
      >
        {done ? "✓" : "○"}
      </div>
      <span className={`text-sm ${done ? "" : "text-muted-foreground"}`}>
        {label}
      </span>
    </div>
  );
}

function IngressRulesTable({
  rules,
  actors,
}: {
  rules: ActorIngressRuleResource[];
  actors: ActorResource[];
}) {
  if (rules.length === 0) return <Empty text="No ingress rules configured" />;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Source ID</TableHead>
          <TableHead>Path</TableHead>
          <TableHead>Kinds</TableHead>
          <TableHead>Actor</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rules.map((rule) => {
          const actor = actors.find((a) => a.id === rule.actor_id);
          return (
            <TableRow key={rule.id}>
              <TableCell className="font-mono text-xs">{rule.source_id_pattern}</TableCell>
              <TableCell className="font-mono text-xs">{rule.source_path_pattern}</TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {rule.kind_patterns.map((k) => (
                    <Badge key={k} variant="outline" className="text-xs">
                      {k}
                    </Badge>
                  ))}
                </div>
              </TableCell>
              <TableCell className="text-sm">{actor?.name ?? rule.actor_id}</TableCell>
              <TableCell>
                <Badge variant={rule.enabled ? "default" : "secondary"}>
                  {rule.enabled ? "active" : "inactive"}
                </Badge>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
      <p className="text-sm">{text}</p>
    </div>
  );
}

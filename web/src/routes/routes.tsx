import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { GitBranch, Trash2 } from "lucide-react";
import { useResourceList, useCreateResource, useDeleteResource } from "@/hooks/use-resources";
import type { ActorIngressRuleResource, ActorResource } from "@/types/api";
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
  const createMutation = useCreateResource<ActorIngressRuleResource>("ingress-rules");
  const deleteMutation = useDeleteResource("ingress-rules");

  const [form, setForm] = useState<RuleFormData>(defaultForm);

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
      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Table */}
        <Card className="flex-1">
          <CardHeader>
            <CardTitle>Ingress Rules</CardTitle>
            <CardDescription>{rules.length} routing entries</CardDescription>
          </CardHeader>
          <CardContent>
            {rules.length === 0 ? (
              <Empty text="No ingress rules configured" />
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
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Source ID</label>
                <Input
                  value={form.sourceIdPattern}
                  onChange={(e) =>
                    setForm({ ...form, sourceIdPattern: e.target.value })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Source Path</label>
                <Input
                  value={form.sourcePathPattern}
                  onChange={(e) =>
                    setForm({ ...form, sourcePathPattern: e.target.value })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Kinds</label>
                <Input
                  value={form.kindPatterns}
                  onChange={(e) =>
                    setForm({ ...form, kindPatterns: e.target.value })
                  }
                  placeholder="text, image, *"
                />
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

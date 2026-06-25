// routes.tsx — /routes (Ingress 规则页), ISSUE-0007 S4.
//
// Demo `view--ingress` reconstruction. Layout (top → bottom):
//   PageShell(title + sub)        ← page-head only; the shell-level __root
//                                    topbar renders crumbs (运行时 › Ingress)
//                                    + Refresh, so we pass no crumbs/actions
//                                    here to avoid a double topbar.
//   IngressFlow                    ← 4-node pipeline (Integration → Rule →
//                                    Mailbox → Runtime).
//   ingress-list                    ← CrudHeader(规则 + count) + table-wrap
//                                    (data-table--ingress with rule rows +
//                                    inline draft row) + table-foot (新建规则)
//                                    OR the three-part Empty when no rules and
//                                    the draft row is not open.
//
// The "create" affordance is an **inline draft row**: clicking 新建规则 inserts
// an editable row at the bottom of the table (source_id_pattern /
// source_path_pattern / kind_patterns / actor select, status pinned to
// active). Save mutates useCreateResource("ingress-rules"); cancel removes the
// row. The old right-side fixed create-form card and the IngressRuleTutorial
// onboarding card are removed — the routing semantics live in the page-head
// sub + Empty description.
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import {
  useResourceList,
  useCreateResource,
  useDeleteResource,
  useIntegrationKinds,
} from "@/hooks/use-resources";
import type {
  ActorIngressRuleResource,
  ActorResource,
  IntegrationResource,
} from "@/types/api";
import {
  CrudHeader,
  Empty,
  IngressFlow,
  PageShell,
  StatusPill,
} from "@/components/baseline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const Route = createFileRoute("/routes")({
  component: RoutesPage,
});

// ---------------------------------------------------------------------------
// Inline draft row state
// ---------------------------------------------------------------------------

interface DraftForm {
  sourceIdPattern: string;
  sourcePathPattern: string;
  kindPatterns: string;
  actorId: string;
}

const DRAFT_DEFAULT: DraftForm = {
  sourceIdPattern: "*",
  sourcePathPattern: "**",
  kindPatterns: "*",
  actorId: "",
};

function RoutesPage() {
  const { data: rules = [], isLoading, error } = useResourceList<ActorIngressRuleResource>("ingress-rules");
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: integrations = [] } = useResourceList<IntegrationResource>("integrations");
  const { data: integrationKinds = [] } = useIntegrationKinds();
  const createMutation = useCreateResource<ActorIngressRuleResource>("ingress-rules");
  const deleteMutation = useDeleteResource("ingress-rules");

  const [draftOpen, setDraftOpen] = useState(false);
  const [draft, setDraft] = useState<DraftForm>(DRAFT_DEFAULT);

  const openDraft = () => {
    setDraft(DRAFT_DEFAULT);
    setDraftOpen(true);
  };

  const cancelDraft = () => {
    setDraft(DRAFT_DEFAULT);
    setDraftOpen(false);
  };

  const saveDraft = async () => {
    const kindPatterns = draft.kindPatterns
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean);
    await createMutation.mutateAsync({
      source_id_pattern: draft.sourceIdPattern,
      source_path_pattern: draft.sourcePathPattern,
      kind_patterns: kindPatterns.length > 0 ? kindPatterns : ["*"],
      actor_id: draft.actorId,
      enabled: true,
    });
    cancelDraft();
  };

  const handleDelete = (id: string) => {
    if (confirm("Delete this rule?")) deleteMutation.mutate(id);
  };

  const actorName = (actorId: string) =>
    actors.find((a) => a.id === actorId)?.name ?? actorId;

  // Path-convention hint for the draft row's Source Path field: when the
  // source_id_pattern is a plain integration id (no glob), surface the
  // resolved integration kind's path convention. Reuses the pre-S4 convention.
  const matchingKind = draft.sourceIdPattern.includes("*")
    ? null
    : (() => {
        const integ = integrations.find((i) => i.id === draft.sourceIdPattern);
        if (!integ) return null;
        return integrationKinds.find((k) => k.name === integ.name) ?? null;
      })();

  if (isLoading) return <PageShell title="Ingress 规则">Loading ingress rules…</PageShell>;
  if (error) return <PageShell title="Ingress 规则">Error: {error.message}</PageShell>;

  const showEmpty = rules.length === 0 && !draftOpen;

  return (
    <PageShell
      title="Ingress 规则"
      sub="把集成事件路由到 Actor。一条规则声明：来自哪个集成（source id）、哪条路径、哪种事件类型 → 交给哪个 Actor。"
    >
      <IngressFlow />

      <div className="ingress-list">
        <CrudHeader title="规则" count={rules.length} />

        {showEmpty ? (
          <Empty
            illustration="Route"
            title="还没有 Ingress 规则"
            description="没有规则时，Actor 不会接收任何集成事件。点下方「新建规则」添加一条。"
            action={
              <Button onClick={openDraft}>新建规则</Button>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table data-table--ingress">
              <thead>
                <tr>
                  <th>Source ID</th>
                  <th>Path</th>
                  <th>Kinds</th>
                  <th>Actor</th>
                  <th>状态</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id}>
                    <td className="font-mono text-xs">{rule.source_id_pattern}</td>
                    <td className="font-mono text-xs">{rule.source_path_pattern}</td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {rule.kind_patterns.map((k) => (
                          <Badge key={k} variant="outline" className="text-xs">
                            {k}
                          </Badge>
                        ))}
                      </div>
                    </td>
                    <td className="text-sm">{actorName(rule.actor_id)}</td>
                    <td>
                      <StatusPill variant={rule.enabled ? "running" : "default"}>
                        {rule.enabled ? "active" : "inactive"}
                      </StatusPill>
                    </td>
                    <td>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleDelete(rule.id)}
                        disabled={deleteMutation.isPending}
                        aria-label="Delete rule"
                      >
                        <Trash2 className="size-3.5 text-destructive" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
              {/* inline draft row — rendered as a second tbody so it always
                  sits at the bottom of the table when draftOpen is true. */}
              {draftOpen && (
                <tbody id="ingressDraftRow">
                  <tr>
                    <td>
                      <Input
                        value={draft.sourceIdPattern}
                        onChange={(e) =>
                          setDraft({ ...draft, sourceIdPattern: e.target.value })
                        }
                        placeholder='e.g. "slack-main", "telegram-*", "*"'
                        data-draft="source_id_pattern"
                      />
                    </td>
                    <td>
                      <Input
                        value={draft.sourcePathPattern}
                        onChange={(e) =>
                          setDraft({ ...draft, sourcePathPattern: e.target.value })
                        }
                        placeholder="**"
                        data-draft="source_path_pattern"
                      />
                      {matchingKind?.source_path_convention && (
                        <p className="mt-1 text-[10px] text-muted-foreground whitespace-pre-wrap">
                          path convention ({matchingKind.name}): {matchingKind.source_path_convention}
                        </p>
                      )}
                    </td>
                    <td>
                      <Input
                        value={draft.kindPatterns}
                        onChange={(e) =>
                          setDraft({ ...draft, kindPatterns: e.target.value })
                        }
                        placeholder="text, image:*"
                        data-draft="kind_patterns"
                      />
                    </td>
                    <td>
                      <Select
                        value={draft.actorId}
                        onValueChange={(v) => setDraft({ ...draft, actorId: v })}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select actor" />
                        </SelectTrigger>
                        <SelectContent>
                          {actors.map((a) => (
                            <SelectItem key={a.id} value={a.id}>
                              {a.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </td>
                    <td>
                      <StatusPill variant="running">active</StatusPill>
                    </td>
                    <td>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={cancelDraft}
                        >
                          取消
                        </Button>
                        <Button
                          size="sm"
                          onClick={saveDraft}
                          disabled={createMutation.isPending || !draft.actorId}
                        >
                          {createMutation.isPending ? "Saving…" : "保存"}
                        </Button>
                      </div>
                    </td>
                  </tr>
                </tbody>
              )}
            </table>
            <div className="table-foot">
              <Button variant="ghost" size="sm" onClick={openDraft}>
                新建规则
              </Button>
            </div>
          </div>
        )}
      </div>
    </PageShell>
  );
}

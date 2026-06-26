// actors.$id.tsx — /actors/$id pure detail view (ISSUE-0007 S3).
//
// Reuses the same ActorEditor hero + editor__cols shell as /actors/new and
// /actors/$id/edit, but renders fields read-only and injects detail-only
// blocks for runtime context, ingress, capabilities, history, and deletion.
// Editing no longer inlines here — the 编辑 action routes to /actors/$id/edit.
//
// ISSUE-0010 regression (conversation-entry-via-actor test): this page still
// lists this Actor's historical conversations via listConversations() filtered
// by actor_id, each row linking to the conversation view route.
import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import {
  useDeleteResource,
  useLiveCapabilities,
  useResourceList,
} from "@/hooks/use-resources";
import type {
  ActorIngressRuleResource,
  ActorResource,
  CapabilitySetResource,
  CharacterResource,
  ConversationListItem,
  LLMBackendResource,
} from "@/types/api";
import { listConversations } from "@/lib/api";
import {
  Empty,
  ActorEditor,
  LegendCard,
  RailCard,
  StatusPill,
  modelOptionsFor,
  useAppShellActions,
  type ActorEditorState,
} from "@/components/baseline";

export const Route = createFileRoute("/actors/$id")({
  component: ActorDetailPage,
});

function ActorDetailPage() {
  const { id } = Route.useParams();
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: capabilitySets = [] } =
    useResourceList<CapabilitySetResource>("capability-sets");
  const { data: characters = [] } = useResourceList<CharacterResource>("characters");
  const { data: ingressRules = [] } =
    useResourceList<ActorIngressRuleResource>("ingress-rules");
  const { data: liveCaps } = useLiveCapabilities();
  const deleteMutation = useDeleteResource("actors");

  const actor = actors.find((a) => a.id === id);

  // ISSUE-0010: per-Actor historical conversations. Pure client-side filter of
  // listConversations() by actor_id — no new endpoint.
  const [actorConversations, setActorConversations] = useState<ConversationListItem[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);

  // Inject 编辑 + 发起对话 actions into the shell topbar.
  const { setActions } = useAppShellActions();
  useEffect(() => {
    if (!actor) {
      setActions(null);
      return;
    }
    setActions(
      <>
        <Link to="/actors/$id/edit" params={{ id: actor.id }}>
          <button type="button" className="btn btn--ghost">编辑</button>
        </Link>
        <Link
          to="/admin/conversations/$conversationId"
          params={{ conversationId: `actor-${actor.id}` }}
        >
          <button type="button" className="btn btn--primary">发起对话</button>
        </Link>
      </>,
    );
    return () => setActions(null);
  }, [setActions, actor]);

  useEffect(() => {
    if (!actor) return;
    let cancelled = false;
    setConversationsLoading(true);
    void (async () => {
      try {
        const all = await listConversations();
        if (cancelled) return;
        const mine = all
          .filter((c) => c.actor_id === actor.id)
          .sort((a, b) => convTime(b) - convTime(a));
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
      <div className="view">
        <Link to="/actors" className="inline-link">← 返回 Actors</Link>
        <p className="page-sub">未找到该 Actor。</p>
      </div>
    );
  }

  const backend = backends.find((b) => b.id === actor.default_llm_backend?.id);
  const capset = capabilitySets.find((c) => c.id === actor.capability_set?.id);
  const character = actor.default_character
    ? characters.find((c) => c.id === actor.default_character!.id)
    : undefined;
  // Ingress rules routing to this actor (client-side filter of ingress-rules).
  const rulesForActor = ingressRules.filter((r) => r.actor_id === actor.id);
  // Capabilities exposed via this actor's capability set (names resolved via
  // live-capabilities lookup).
  const capIds = actor.capability_set?.integration_capability_ids ?? [];
  const capName = (capId: string) =>
    liveCaps?.find((c) => c.capability_id === capId)?.capability_name ?? capId;
  const editorState: ActorEditorState = {
    name: actor.name ?? "",
    description: actor.default_character?.description ?? character?.description ?? "",
    systemPrompt: character?.system_prompt ?? "",
    actorType: actor.type ?? "simple_loop",
    backendId: actor.default_llm_backend?.id ?? "",
    model: actor.default_model ?? "",
    capabilitySetId: actor.capability_set?.id ?? "",
    maxTokens: String(actor.default_budget?.max_tokens ?? ""),
    maxSteps: String(actor.default_budget?.max_steps ?? ""),
    enabled: actor.enabled ?? true,
  };
  const selectedBackend = backends.find((b) => b.id === editorState.backendId);
  const modelOptions = modelOptionsFor(selectedBackend);
  const setReadOnlyState = () => undefined;

  const handleDelete = () => {
    if (confirm(`删除 Actor “${actor.name}”？`)) {
      deleteMutation.mutate(actor.id);
    }
  };

  return (
    <div className="view">
      <div className="page-head">
        <div>
          <h1 className="page-title">查看 {actor.name}</h1>
          <p className="page-sub">查看这个 Actor 的 LLM 供应商、模型、Capability Set、预算、Persona 与路由状态。</p>
        </div>
      </div>

      <div className="editor">
        <ActorEditor
          mode="view"
          actor={actor}
          state={editorState}
          setState={setReadOnlyState}
          backends={backends}
          capabilitySets={capabilitySets}
          modelOptions={modelOptions}
          isPending={deleteMutation.isPending}
          onDelete={handleDelete}
          mainAfter={
            <>
              <LegendCard
                legend="运行上下文"
                dotColor="indigo"
                lead="Actor 保存后的运行时引用和 Capability Set 派生信息。"
                as="div"
              >
                <div className="grid-2">
                  <div className="readonly-kv"><b>ID</b><code>{actor.id}</code></div>
                  <div className="readonly-kv">
                    <b>LLM 供应商</b>
                    {backend
                      ? <Link to="/providers/$id" params={{ id: backend.id }} className="inline-link">{backend.name}</Link>
                      : <span>-</span>}
                  </div>
                  <div className="readonly-kv">
                    <b>Capability Set</b>
                    {capset
                      ? <Link to="/capability-sets" className="inline-link">{capset.name}</Link>
                      : <span>-</span>}
                  </div>
                  <div className="readonly-kv"><b>Workspace</b><span>{capset?.workspace_path ?? "-"}</span></div>
                  <div className="readonly-kv">
                    <b>Memory</b>
                    <span>{capset?.runtime_policy?.memory_enabled ? "enabled" : "disabled"}</span>
                  </div>
                </div>
              </LegendCard>

              <LegendCard
                legend="事件路由（Ingress）"
                dotColor="amber"
                lead="以下 Ingress 规则把集成事件路由到这个 Actor。没有规则时 Actor 不接收任何外部事件。"
                as="div"
              >
                {rulesForActor.length === 0 ? (
                  <Empty
                    illustration="Route"
                    title="此 Actor 暂无 Ingress 规则"
                    description="没有规则时 Actor 不接收任何外部事件。"
                    action={
                      <Link to="/routes">
                        <button type="button" className="btn btn--ghost" data-jump="ingress">配置 Ingress</button>
                      </Link>
                    }
                  />
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Source ID</th><th>Path</th><th>Kinds</th><th>状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rulesForActor.map((r) => (
                        <tr key={r.id}>
                          <td><code>{r.source_id_pattern}</code></td>
                          <td><code>{r.source_path_pattern}</code></td>
                          <td>{r.kind_patterns.join(", ")}</td>
                          <td>
                            <StatusPill variant={r.enabled === false ? "paused" : "running"}>
                              {r.enabled === false ? "已禁用" : "运行中"}
                            </StatusPill>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </LegendCard>

              <LegendCard
                legend="能力（Capabilities）"
                dotColor="green"
                lead="Actor 通过其 Capability Set 获得可调用的能力。"
                as="div"
              >
                {capIds.length === 0 ? (
                  <Empty
                    illustration="Cap"
                    title="无能力绑定"
                    description="为该 Actor 的 Capability Set 选择能力。"
                    action={
                      <Link to="/actors/$id/edit" params={{ id: actor.id }}>
                        <button type="button" className="btn btn--ghost">去编辑</button>
                      </Link>
                    }
                  />
                ) : (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {capIds.map((capId) => (
                      <StatusPill key={capId} variant="default" dot={false}>
                        {capName(capId)}
                      </StatusPill>
                    ))}
                  </div>
                )}
              </LegendCard>
            </>
          }
          railBefore={
          <RailCard
            title="历史对话"
            hint={conversationsLoading ? undefined : `${actorConversations.length} 条对话`}
          >
            {conversationsLoading ? null : actorConversations.length === 0 ? (
              <Empty title="还没有对话" />
            ) : (
              <div className="detail-conv-list">
                {actorConversations.slice(0, 5).map((c, index) => {
                  const shortId = shortConversationId(c.conversation_id);
                  const displayTitle = conversationDisplayTitle(c, index);
                  return (
                    <Link
                      key={c.conversation_id}
                      to="/admin/conversations/$conversationId"
                      params={{ conversationId: c.conversation_id }}
                      className="detail-conv-item"
                      title={c.conversation_id}
                    >
                      <span className="detail-conv-item__name">{displayTitle}</span>
                      <span className="detail-conv-item__preview">ID {shortId}</span>
                      <span className="detail-conv-item__time">
                        {formatConvTime(c.updated_at ?? c.created_at)}
                      </span>
                    </Link>
                  );
                })}
              </div>
            )}
            {actorConversations.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Link to="/admin/conversations">更多</Link>
              </div>
            )}
          </RailCard>
          }
        />
      </div>
    </div>
  );
}

function convTime(c: ConversationListItem): number {
  const v = c.updated_at ?? c.created_at;
  if (!v) return 0;
  const t = new Date(v).getTime();
  return Number.isNaN(t) ? 0 : t;
}

function shortConversationId(value: string): string {
  return value.replace(/^conversation-/, "").slice(0, 8);
}

function conversationDisplayTitle(item: ConversationListItem, index: number): string {
  return item.title.trim() || `对话 #${index + 1}`;
}

function formatConvTime(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

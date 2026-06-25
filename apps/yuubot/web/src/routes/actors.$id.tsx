// actors.$id.tsx — /actors/$id pure detail view (ISSUE-0007 S3).
//
// Mirrors demo `view--actor-detail`: DetailHero (avatar + name + desc + meta)
// + detail-grid(detail-main: 配置概览 / 事件路由-Ingress / 能力-Capabilities
// three LegendCards + detail-rail: 历史对话 rail + 状态 rail + danger rail).
// Editing no longer inlines here — the 编辑 action routes to /actors/$id/edit.
//
// ISSUE-0010 regression (conversation-entry-via-actor test): this page still
// lists this Actor's historical conversations via listConversations() filtered
// by actor_id, each row linking to the conversation view route.
import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import {
  useDeleteResource,
  useLiveCapabilities,
  useResourceList,
} from "@/hooks/use-resources";
import type {
  ActorIngressRuleResource,
  ActorResource,
  CapabilitySetResource,
  ConversationListItem,
  LLMBackendResource,
} from "@/types/api";
import { listConversations } from "@/lib/api";
import {
  DetailHero,
  Dot,
  Empty,
  KvTable,
  LegendCard,
  RailCard,
  StatusPill,
  useAppShellActions,
  type DotColor,
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
  // Ingress rules routing to this actor (client-side filter of ingress-rules).
  const rulesForActor = ingressRules.filter((r) => r.actor_id === actor.id);
  // Capabilities exposed via this actor's capability set (names resolved via
  // live-capabilities lookup).
  const capIds = actor.capability_set?.integration_capability_ids ?? [];
  const capName = (capId: string) =>
    liveCaps?.find((c) => c.capability_id === capId)?.capability_name ?? capId;

  const handleDelete = () => {
    if (confirm(`删除 Actor “${actor.name}”？`)) {
      deleteMutation.mutate(actor.id);
    }
  };

  const dotIndigo: DotColor = "indigo";
  const dotAmber: DotColor = "amber";
  const dotGreen: DotColor = "green";
  const dotSlate: DotColor = "slate";

  return (
    <div className="view">
      <div className="page-head">
        <DetailHero
          avatar={(actor.name.trim()[0] ?? "A").toUpperCase()}
          title={actor.name}
          sub={actor.default_character?.description || actor.default_character?.name || "该 Actor 暂无描述。"}
          meta={
            <>
              <StatusPill variant={actor.enabled ? "running" : "paused"}>
                {actor.enabled ? "运行中" : "已停止"}
              </StatusPill>
              <span className="kv"><b>ID</b> <code>{actor.id}</code></span>
              <span className="kv"><b>Backend</b> {actor.default_llm_backend?.name ?? "—"}</span>
              <span className="kv"><b>模型</b> <code>{actor.default_model}</code></span>
              <span className="kv"><b>Capability Set</b> {actor.capability_set?.name ?? "—"}</span>
            </>
          }
        />
      </div>

      <div className="detail-grid">
        <div className="detail-main">
          {/* 配置概览 */}
          <LegendCard legend="配置概览" dotColor={dotIndigo} as="div">
            <KvTable
              rows={[
                { key: "Name", value: actor.name },
                { key: "Type", value: actor.type },
                { key: "Model", value: <code>{actor.default_model}</code> },
                { key: "Character", value: actor.default_character?.name ?? "—" },
                {
                  key: "Capability Set",
                  value: capset
                    ? <Link to="/capability-sets" className="inline-link">{capset.name}</Link>
                    : "—",
                },
                {
                  key: "Backend",
                  value: backend
                    ? <Link to="/providers/$id" params={{ id: backend.id }} className="inline-link">{backend.name}</Link>
                    : "—",
                },
                { key: "MaxSteps", value: actor.default_budget?.max_steps ?? "—" },
                { key: "Workspace", value: capset?.workspace_path ?? "—" },
                {
                  key: "Memory",
                  value: capset?.runtime_policy?.memory_enabled ? "enabled" : "disabled",
                },
              ]}
            />
          </LegendCard>

          {/* 事件路由（Ingress） */}
          <LegendCard
            legend="事件路由（Ingress）"
            dotColor={dotAmber}
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

          {/* 能力（Capabilities） */}
          <LegendCard
            legend="能力（Capabilities）"
            dotColor={dotGreen}
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
        </div>

        <aside className="detail-rail">
          {/* 历史对话 */}
          <RailCard
            title="历史对话"
            hint={conversationsLoading ? undefined : `${actorConversations.length} 条对话`}
          >
            <span style={{ marginBottom: 8, display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Dot color={dotSlate} />
            </span>
            {conversationsLoading ? null : actorConversations.length === 0 ? (
              <Empty title="还没有对话" />
            ) : (
              <div className="detail-conv-list">
                {actorConversations.slice(0, 5).map((c) => (
                  <Link
                    key={c.conversation_id}
                    to="/admin/conversations/$conversationId"
                    params={{ conversationId: c.conversation_id }}
                    className="inline-link"
                  >
                    {c.conversation_id}
                  </Link>
                ))}
              </div>
            )}
            {actorConversations.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Link to="/admin/conversations">更多</Link>
              </div>
            )}
          </RailCard>

          {/* 状态 */}
          <RailCard title="状态">
            <StatusPill variant={actor.enabled ? "running" : "paused"}>
              {actor.enabled ? "运行中" : "已停止"}
            </StatusPill>
          </RailCard>

          {/* 危险操作 */}
          <RailCard danger title="危险操作">
            <button
              type="button"
              className="btn btn--danger"
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
            >
              <Trash2 size={14} />
              <span>删除此 Actor</span>
            </button>
          </RailCard>
        </aside>
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

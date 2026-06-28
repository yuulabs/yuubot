// actors.$id.tsx — /actors/$id merged detail + editor view.
//
// Reuses the same ActorEditor hero + editor__cols shell as /actors/new and
// renders editable fields while injecting detail-only blocks for runtime
// context, ingress, capabilities, history, and deletion.
//
// ISSUE-0010 regression (conversation-entry-via-actor test): this page still
// lists this Actor's historical conversations via listConversations() filtered
// by actor_id, each row linking to the conversation view route.
import { useEffect, useMemo, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useDeleteResource,
  useLiveCapabilities,
  useResourceList,
  useUpdateResource,
} from "@/hooks/use-resources";
import type {
  ActorIngressRuleResource,
  ActorResource,
  CapabilitySetResource,
  ConversationListItem,
  LLMBackendResource,
} from "@/types/api";
import {
  deleteActorSkill,
  getActorSkills,
  importActorSkill,
  listConversations,
} from "@/lib/api";
import { workspaceHref } from "@/lib/workspace";
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
  const { data: ingressRules = [] } =
    useResourceList<ActorIngressRuleResource>("ingress-rules");
  const { data: liveCaps } = useLiveCapabilities();
  const updateActorMutation = useUpdateResource<ActorResource>("actors");
  const deleteMutation = useDeleteResource("actors");
  const queryClient = useQueryClient();

  const actor = actors.find((a) => a.id === id);
  const [state, setStateRaw] = useState<ActorEditorState>({
    name: "",
    description: "",
    systemPrompt: "",
    actorType: "simple_loop",
    backendId: "",
    model: "",
    capabilitySetId: "",
    maxTokens: "8192",
    maxSteps: "6",
    enabled: true,
    skillScope: "global_and_local",
  });
  const [error, setError] = useState("");
  const [hydratedActorKey, setHydratedActorKey] = useState("");
  const setState = <K extends keyof ActorEditorState>(key: K, value: ActorEditorState[K]) =>
    setStateRaw((current) => ({ ...current, [key]: value }));

  // ISSUE-0010: per-Actor historical conversations. Pure client-side filter of
  // listConversations() by actor_id — no new endpoint.
  const [actorConversations, setActorConversations] = useState<ConversationListItem[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const actorSnapshotKey = actor
    ? `${actor.id}:${actor.version ?? ""}:${actor.updated_at ?? ""}`
    : "";

  useEffect(() => {
    if (!actor) return;
    if (actorSnapshotKey === hydratedActorKey) return;
    setStateRaw({
      name: actor.name ?? "",
      description: "",
      systemPrompt: actor.persona_prompt ?? "",
      actorType: actor.type ?? "simple_loop",
      backendId: actor.llm_backend_id ?? "",
      model: actor.model ?? "",
      capabilitySetId: actor.capability_set_id ?? "",
      maxTokens: String(actor.per_run_budget?.max_tokens ?? 8192),
      maxSteps: String(actor.per_run_budget?.max_steps ?? 6),
      enabled: actor.enabled ?? true,
      skillScope: actor.skill_scope ?? "global_and_local",
    });
    setHydratedActorKey(actorSnapshotKey);
    setError("");
  }, [actor, actorSnapshotKey, hydratedActorKey]);

  const selectedBackend = backends.find((b) => b.id === state.backendId);
  const modelOptions = useMemo(() => modelOptionsFor(selectedBackend), [selectedBackend]);
  const isPending = updateActorMutation.isPending || deleteMutation.isPending;
  const skillsQueryKey = ["actor-skills", id] as const;
  const { data: skillsView } = useQuery({
    queryKey: skillsQueryKey,
    queryFn: () => getActorSkills(id),
    enabled: Boolean(actor),
  });
  const importSkillMutation = useMutation({
    mutationFn: (name: string) => importActorSkill(id, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: skillsQueryKey });
    },
  });
  const deleteSkillMutation = useMutation({
    mutationFn: (name: string) => deleteActorSkill(id, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: skillsQueryKey });
    },
  });

  // Inject save + conversation actions into the shell topbar.
  const { setActions } = useAppShellActions();
  useEffect(() => {
    if (!actor) {
      setActions(null);
      return;
    }
    const capset = capabilitySets.find((c) => c.id === actor.capability_set_id);
    const workspaceUrl = workspaceHref(actor.capability_set?.workspace_path ?? capset?.workspace_path);
    setActions(
      <>
        <button type="submit" form="actor-editor-form" className="btn btn--primary" disabled={isPending}>
          保存
        </button>
        {workspaceUrl ? (
          <a
            href={workspaceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn--ghost"
          >
            Workspace
          </a>
        ) : null}
        <Link
          to="/admin/conversations/$conversationId"
          params={{ conversationId: `actor-${actor.id}` }}
        >
          <button type="button" className="btn btn--ghost">发起对话</button>
        </Link>
      </>,
    );
    return () => setActions(null);
  }, [setActions, actor, capabilitySets, isPending]);

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

  const backend = backends.find((b) => b.id === actor.llm_backend_id);
  const capset = capabilitySets.find((c) => c.id === actor.capability_set_id);
  const workspaceUrl = workspaceHref(capset?.workspace_path);
  // Ingress rules routing to this actor (client-side filter of ingress-rules).
  const rulesForActor = ingressRules.filter((r) => r.actor_id === actor.id);
  // Capabilities exposed via this actor's capability set (names resolved via
  // live-capabilities lookup).
  const capIds = capset?.integration_ids ?? [];
  const capName = (capId: string) =>
    liveCaps?.find((c) => c.capability_id === capId)?.capability_name ?? capId;

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!state.name.trim()) return setError("请输入名称。");
    if (!state.capabilitySetId) return setError("请选择 Capability Set。");
    if (!state.backendId) return setError("请选择 LLM 供应商。");
    if (!state.model.trim()) return setError("请选择模型。");
    setError("");
    const budget = {
      max_steps: Number(state.maxSteps) || 0,
      max_tokens: Number(state.maxTokens) || 0,
      max_usd: 0,
    };
    try {
      await updateActorMutation.mutateAsync({
        id: actor.id,
        data: {
          name: state.name,
          type: state.actorType,
          persona_prompt: state.systemPrompt,
          model: state.model,
          llm_backend_id: state.backendId,
          capability_set_id: state.capabilitySetId,
          per_run_budget: budget,
          enabled: state.enabled,
          skill_scope: state.skillScope,
        },
      });
    } catch {
      /* mutation error surfaces below */
    }
  };

  const handleDelete = () => {
    if (confirm(`删除 Actor “${actor.name}”？`)) {
      deleteMutation.mutate(actor.id);
    }
  };

  return (
    <div className="view">
      <div className="page-head">
        <div>
          <h1 className="page-title">Actor {actor.name}</h1>
          <p className="page-sub">配置这个 Actor 的 LLM 供应商、模型、Capability Set、预算、Persona 与路由状态。</p>
        </div>
      </div>

      <form className="editor" id="actor-editor-form" onSubmit={handleSave} autoComplete="off">
        <ActorEditor
          mode="edit"
          actor={actor}
          state={state}
          setState={setState}
          backends={backends}
          capabilitySets={capabilitySets}
          modelOptions={modelOptions}
          isPending={isPending}
          error={error || updateActorMutation.error?.message}
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
                  <div className="readonly-kv">
                    <b>Workspace</b>
                    {workspaceUrl ? (
                      <a href={workspaceUrl} target="_blank" rel="noopener noreferrer" className="inline-link">
                        {capset?.workspace_path}
                      </a>
                    ) : (
                      <span>-</span>
                    )}
                  </div>
                  <div className="readonly-kv">
                    <b>Rollover</b>
                    <span>{capset?.loop_policy?.rollover_enabled ? "enabled" : "disabled"}</span>
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
                legend="Skills"
                dotColor="indigo"
                lead="全局 skills 可导入到该 Actor 的本地 workspace；同名时本地覆盖全局。"
                as="div"
              >
                <div className="grid-2">
                  <SkillList
                    title="Loaded"
                    names={(skillsView?.loaded_skills ?? []).map((skill) => `${skill.name} (${skill.source})`)}
                  />
                  <SkillList
                    title="Local"
                    names={(skillsView?.local_skills ?? []).map((skill) => skill.name)}
                    onDelete={(name) => deleteSkillMutation.mutate(name)}
                  />
                </div>
                <div style={{ marginTop: 12 }}>
                  <b>Global</b>
                  {(skillsView?.global_skills ?? []).length === 0 ? (
                    <p className="page-sub">暂无全局 skills。</p>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                      {(skillsView?.global_skills ?? []).map((skill) => (
                        <button
                          key={skill.name}
                          type="button"
                          className="btn btn--ghost"
                          disabled={importSkillMutation.isPending}
                          onClick={() => importSkillMutation.mutate(skill.name)}
                        >
                          导入 {skill.name}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
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
      </form>
    </div>
  );
}

function convTime(c: ConversationListItem): number {
  const v = c.updated_at ?? c.created_at;
  if (!v) return 0;
  const t = new Date(v).getTime();
  return Number.isNaN(t) ? 0 : t;
}

function SkillList({
  title,
  names,
  onDelete,
}: {
  title: string;
  names: string[];
  onDelete?: (name: string) => void;
}) {
  return (
    <div>
      <b>{title}</b>
      {names.length === 0 ? (
        <p className="page-sub">-</p>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {names.map((name) => {
            const rawName = name.split(" ", 1)[0] ?? name;
            return onDelete ? (
              <button
                key={name}
                type="button"
                className="btn btn--ghost"
                onClick={() => onDelete(rawName)}
              >
                删除 {name}
              </button>
            ) : (
              <StatusPill key={name} variant="default" dot={false}>
                {name}
              </StatusPill>
            );
          })}
        </div>
      )}
    </div>
  );
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

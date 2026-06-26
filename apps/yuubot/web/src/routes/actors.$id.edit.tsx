// actors.$id.edit.tsx — /actors/$id/edit editor (edit mode) (ISSUE-0007 S3).
//
// Route owns the <form>, editor state, and the update path: updateCharacter
// (when prompt/description changed) then updateActor — folded character per
// ISSUE-0011. The shared presentational <ActorEditor> renders the bound body;
// the danger zone is visible in edit mode.
import { useEffect, useMemo, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  useDeleteResource,
  useResourceList,
  useUpdateResource,
} from "@/hooks/use-resources";
import type {
  ActorResource,
  CapabilitySetResource,
  CharacterResource,
  LLMBackendResource,
} from "@/types/api";
import {
  ActorEditor,
  modelOptionsFor,
  useAppShellActions,
  type ActorEditorState,
} from "@/components/baseline";

export const Route = createFileRoute("/actors/$id/edit")({
  component: ActorsEditPage,
});

function ActorsEditPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: capabilitySets = [] } =
    useResourceList<CapabilitySetResource>("capability-sets");
  const { data: characters = [] } = useResourceList<CharacterResource>("characters");

  const updateActorMutation = useUpdateResource<ActorResource>("actors");
  const updateCharacterMutation = useUpdateResource<CharacterResource>("characters");
  const deleteMutation = useDeleteResource("actors");

  const actor = actors.find((a) => a.id === id);
  // Resolve the folded Character (ISSUE-0011) for prompt/description prefill.
  const character = useMemo(
    () => (actor?.default_character
      ? characters.find((c) => c.id === actor.default_character!.id)
      : undefined),
    [actor, characters],
  );

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
  });
  const [error, setError] = useState("");
  const setState = <K extends keyof ActorEditorState>(key: K, value: ActorEditorState[K]) =>
    setStateRaw((s) => ({ ...s, [key]: value }));

  // Prefill once the actor + character resolve.
  useEffect(() => {
    if (!actor) return;
    const backend = backends.find((b) => b.id === actor.default_llm_backend?.id);
    setStateRaw({
      name: actor.name ?? "",
      description: actor.default_character?.description ?? character?.description ?? "",
      systemPrompt: character?.system_prompt ?? "",
      actorType: actor.type ?? "simple_loop",
      backendId: actor.default_llm_backend?.id ?? backend?.id ?? "",
      model: actor.default_model ?? "",
      capabilitySetId: actor.capability_set?.id ?? "",
      maxTokens: String(actor.default_budget?.max_tokens ?? 8192),
      maxSteps: String(actor.default_budget?.max_steps ?? 6),
      enabled: actor.enabled ?? true,
    });
    setError("");
  }, [actor, character, backends]);

  const isPending =
    updateActorMutation.isPending || updateCharacterMutation.isPending;
  const selectedBackend = backends.find((b) => b.id === state.backendId);
  const modelOptions = useMemo(() => modelOptionsFor(selectedBackend), [selectedBackend]);

  const { setActions } = useAppShellActions();
  useEffect(() => {
    setActions(
      <>
        <Link to={actor ? "/actors/$id" : "/actors"} params={actor ? { id: actor.id } : {}}>
          <button type="button" className="btn btn--ghost">取消</button>
        </Link>
        <button type="submit" form="actor-editor-form" className="btn btn--primary" disabled={isPending}>
          保存
        </button>
      </>,
    );
    return () => setActions(null);
  }, [setActions, actor, isPending]);

  if (!actor) {
    return (
      <div className="view">
        <Link to="/actors" className="inline-link">← 返回 Actors</Link>
        <p className="page-sub">未找到该 Actor。</p>
      </div>
    );
  }

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
      if (character && (state.systemPrompt !== character.system_prompt ||
        state.description !== character.description)) {
        await updateCharacterMutation.mutateAsync({
          id: character.id,
          data: { system_prompt: state.systemPrompt, description: state.description },
        });
      }
      await updateActorMutation.mutateAsync({
        id: actor.id,
        data: {
          name: state.name,
          type: state.actorType,
          default_model: state.model,
          default_llm_backend_id: state.backendId,
          capability_set_id: state.capabilitySetId,
          default_budget: budget,
          enabled: state.enabled,
        },
      });
      navigate({ to: "/actors/$id", params: { id: actor.id } });
    } catch {
      /* mutation error surfaces below */
    }
  };

  const handleDelete = () => {
    if (confirm(`删除 Actor “${actor.name}”？`)) {
      deleteMutation.mutate(actor.id, {
        onSuccess: () => navigate({ to: "/actors" }),
      });
    }
  };

  return (
    <div className="view">
      <div className="page-head">
        <div>
          <h1 className="page-title">编辑 {actor.name}</h1>
          <p className="page-sub">修改 LLM 供应商、模型、Capability Set、预算与 Character Persona。</p>
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
          isPending={isPending || deleteMutation.isPending}
          error={error || updateActorMutation.error?.message || updateCharacterMutation.error?.message}
          onDelete={handleDelete}
        />
      </form>
    </div>
  );
}

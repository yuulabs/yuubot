// actors.new.tsx — /actors/new editor (new mode) (ISSUE-0007 S3).
//
// Route owns the <form>, editor state, and the create dual-call (create
// Character then Actor, ISSUE-0011); the shared presentational <ActorEditor>
// renders the demo-aligned hero + cols + rails bound to that state. The shell
// topbar Save (form="actor-editor-form") submits this form from outside the
// subtree.
import { useEffect, useMemo, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  useCreateResource,
  useResourceList,
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

export const Route = createFileRoute("/actors/new")({
  component: ActorsNewPage,
});

const DRAFT: ActorEditorState = {
  name: "",
  description: "",
  systemPrompt: "",
  backendId: "",
  model: "",
  capabilitySetId: "",
  maxTokens: "8192",
  maxSteps: "6",
  enabled: true,
};

function ActorsNewPage() {
  const navigate = useNavigate();
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: capabilitySets = [] } =
    useResourceList<CapabilitySetResource>("capability-sets");
  // createCharacter + createActor dual-call (folded character, ISSUE-0011).
  const createCharacterMutation = useCreateResource<CharacterResource>("characters");
  const createActorMutation = useCreateResource<ActorResource>("actors");

  const [state, setStateRaw] = useState<ActorEditorState>(DRAFT);
  const [error, setError] = useState("");
  const setState = <K extends keyof ActorEditorState>(key: K, value: ActorEditorState[K]) =>
    setStateRaw((s) => ({ ...s, [key]: value }));

  const isPending = createActorMutation.isPending || createCharacterMutation.isPending;
  const selectedBackend = backends.find((b) => b.id === state.backendId);
  const modelOptions = useMemo(() => modelOptionsFor(selectedBackend), [selectedBackend]);

  const { setActions } = useAppShellActions();
  useEffect(() => {
    setActions(
      <>
        <Link to="/actors">
          <button type="button" className="btn btn--ghost">取消</button>
        </Link>
        <button type="submit" form="actor-editor-form" className="btn btn--primary" disabled={isPending}>
          保存
        </button>
      </>,
    );
    return () => setActions(null);
  }, [setActions, isPending]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!state.name.trim()) return setError("请输入名称。");
    if (!state.model.trim()) return setError("请选择模型。");
    if (!state.capabilitySetId) return setError("请选择 Capability Set。");
    if (!state.backendId) return setError("请选择 LLM Backend。");
    setError("");
    const budget = {
      max_steps: Number(state.maxSteps) || 0,
      max_tokens: Number(state.maxTokens) || 0,
      max_usd: 0,
    };
    try {
      const character = await createCharacterMutation.mutateAsync({
        name: state.name,
        description: state.description,
        system_prompt: state.systemPrompt,
        facade_module: "yb",
        default_hints: { language: "zh-CN", tone: "" },
        is_builtin: false,
        builtin_version: "",
        cloned_from: "",
      });
      const created = await createActorMutation.mutateAsync({
        name: state.name,
        type: "simple_loop",
        enabled: state.enabled,
        default_model: state.model,
        default_character_id: character.id,
        default_llm_backend_id: state.backendId,
        capability_set_id: state.capabilitySetId,
        default_budget: budget,
      });
      navigate({ to: "/actors/$id", params: { id: created.id } });
    } catch {
      /* mutation error surfaces below */
    }
  };

  return (
    <div className="view">
      <div className="page-head">
        <div>
          <h1 className="page-title">新建 Actor</h1>
          <p className="page-sub">配置 Agent 规格、模型、Capability Set 与预算，创建一个新的 Actor 实例。</p>
        </div>
      </div>
      <form className="editor" id="actor-editor-form" onSubmit={handleSave} autoComplete="off">
        <ActorEditor
          mode="new"
          state={state}
          setState={setState}
          backends={backends}
          capabilitySets={capabilitySets}
          modelOptions={modelOptions}
          isPending={isPending}
          error={error || createActorMutation.error?.message || createCharacterMutation.error?.message}
        />
      </form>
    </div>
  );
}

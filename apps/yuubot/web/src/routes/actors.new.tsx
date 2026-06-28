// actors.new.tsx — /actors/new editor (new mode) (ISSUE-0007 S3).
//
// Route owns the <form>, editor state, and the Actor create call; the shared
// presentational <ActorEditor>
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
  actorType: "simple_loop",
  backendId: "",
  model: "",
  capabilitySetId: "",
  maxTokens: "8192",
  maxSteps: "6",
  enabled: true,
  skillScope: "global_and_local",
};

function ActorsNewPage() {
  const navigate = useNavigate();
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: capabilitySets = [] } =
    useResourceList<CapabilitySetResource>("capability-sets");
  const createActorMutation = useCreateResource<ActorResource>("actors");

  const [state, setStateRaw] = useState<ActorEditorState>(DRAFT);
  const [error, setError] = useState("");
  const setState = <K extends keyof ActorEditorState>(key: K, value: ActorEditorState[K]) =>
    setStateRaw((s) => ({ ...s, [key]: value }));

  const isPending = createActorMutation.isPending;
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
      const created = await createActorMutation.mutateAsync({
        name: state.name,
        type: state.actorType,
        enabled: state.enabled,
        persona_prompt: state.systemPrompt,
        model: state.model,
        llm_backend_id: state.backendId,
        capability_set_id: state.capabilitySetId,
        per_run_budget: budget,
        skill_scope: state.skillScope,
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
          <p className="page-sub">配置 LLM 供应商、模型、Capability Set、预算与 Persona，创建一个新的 Actor 实例。</p>
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
          error={error || createActorMutation.error?.message}
        />
      </form>
    </div>
  );
}

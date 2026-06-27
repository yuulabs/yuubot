// ActorEditor — shared presentational actor body for /actors/new,
// /actors/$id, and /actors/$id/edit (ISSUE-0007 S3).
//
// Mirrors demo `view--editor` layout (hero + editor__cols main/rail) but is
// STATELESS: the owning route holds form state, mutations, and the optional
// <form>; this component renders fields/rails bound to passed state+setters.
// That keeps the schema-deviation surface (D1–D3 + D-extra) identical across
// new/edit while the route files retain the create/update contract evidence.
//
// Schema deviations vs the demo (design.md D1 + D-extra):
//  • per_run_budget.max_tokens  ← demo "单回合上限 (tokens)"
//  • per_run_budget.max_steps   ← demo "步数上限" (demo said max_tool_calls)
import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import type {
  ActorResource,
  CapabilitySetResource,
  LLMBackendResource,
} from "@/types/api";
import type { DotColor } from "./Dot";
import { Field } from "./Field";
import { LegendCard } from "./LegendCard";
import { RailCard } from "./RailCard";
import { StatusPill } from "./StatusPill";

export interface ActorEditorState {
  name: string;
  description: string;
  systemPrompt: string;
  actorType: string;
  backendId: string;
  model: string;
  capabilitySetId: string;
  maxTokens: string;
  maxSteps: string;
  enabled: boolean;
}

export interface ActorEditorProps {
  mode: "new" | "edit" | "view";
  actor?: ActorResource;
  state: ActorEditorState;
  setState: <K extends keyof ActorEditorState>(key: K, value: ActorEditorState[K]) => void;
  backends?: LLMBackendResource[];
  capabilitySets?: CapabilitySetResource[];
  modelOptions: string[];
  isPending: boolean;
  error?: string;
  onDelete?: () => void;
  mainAfter?: ReactNode;
  railBefore?: ReactNode;
  railAfter?: ReactNode;
}

export function ActorEditor({
  mode,
  actor,
  state,
  setState,
  backends = [],
  capabilitySets = [],
  modelOptions,
  isPending,
  error,
  onDelete,
  mainAfter,
  railBefore,
  railAfter,
}: ActorEditorProps) {
  const readOnly = mode === "view";
  const heroAvatar = (state.name.trim()[0] ?? "A").toUpperCase();
  const dotIndigo: DotColor = "indigo";
  const dotGreen: DotColor = "green";
  const dotAmber: DotColor = "amber";
  const actorTypeOptions = actorTypeChoices(state.actorType);
  const selectedBackend = backends.find((b) => b.id === state.backendId);
  const selectedCapabilitySet = capabilitySets.find((cs) => cs.id === state.capabilitySetId);
  const backendName = selectedBackend?.name ?? state.backendId;
  const capabilitySetName = selectedCapabilitySet?.name ?? actor?.capability_set?.name ?? state.capabilitySetId;

  return (
    <>
      {/* Hero / identity */}
      <div className="editor__hero">
        <div className="hero__avatar">{heroAvatar}</div>
        <div className="hero__fields">
          <Field label="名称" required inline>
            {readOnly ? (
              <ReadOnlyValue>{state.name}</ReadOnlyValue>
            ) : (
              <input
                className="input"
                value={state.name}
                onChange={(e) => setState("name", e.target.value)}
                placeholder="例如：QQ 助手"
              />
            )}
          </Field>
          <Field label="一句话描述" inline>
            {readOnly ? (
              <ReadOnlyValue empty="该 Actor 暂无描述。">{state.description}</ReadOnlyValue>
            ) : (
              <input
                className="input"
                value={state.description}
                onChange={(e) => setState("description", e.target.value)}
                placeholder="这个 Actor 负责做什么？"
              />
            )}
          </Field>
          <div className="hero__meta">
            <span className="kv">
              <b>状态</b>{" "}
              <StatusPill variant={state.enabled ? "running" : "paused"}>
                {mode === "new" ? "草稿" : state.enabled ? "运行中" : "已停止"}
              </StatusPill>
            </span>
            <span className="kv">
              <b>ID</b> <code>{actor?.id ?? "-"}</code>
            </span>
          </div>
        </div>
      </div>

      <div className="editor__cols">
        <div className="editor__main">
          <LegendCard legend="Character Persona" dotColor="slate" as="div">
            <Field
              label="Character Persona"
              hint="描述这个 Actor 的身份、语气、边界和工作方式。"
            >
              {readOnly ? (
                <ReadOnlyValue block>{state.systemPrompt}</ReadOnlyValue>
              ) : (
                <textarea
                  className="input"
                  rows={5}
                  value={state.systemPrompt}
                  onChange={(e) => setState("systemPrompt", e.target.value)}
                  placeholder="例如：你是一个可靠、直接的项目助手，回答前先确认上下文，遇到不确定信息要说明假设。"
                />
              )}
            </Field>
          </LegendCard>

          <LegendCard legend="Actor" dotColor={dotGreen}>
            <Field label="Actor Type" hint="决定 Actor 使用的运行时实现。">
              {readOnly ? (
                <ReadOnlyValue>{state.actorType}</ReadOnlyValue>
              ) : (
                <select
                  className="input"
                  value={state.actorType}
                  onChange={(e) => setState("actorType", e.target.value)}
                >
                  {actorTypeOptions.map((type) => (
                    <option key={type} value={type}>{type}</option>
                  ))}
                </select>
              )}
            </Field>
          </LegendCard>

          {/* LLM 与模型 */}
          <LegendCard legend="LLM 与模型" dotColor={dotIndigo}>
            <div className="grid-2">
              <Field label="LLM 供应商" hint="决定此 Actor 调用哪个供应商。">
                {readOnly ? (
                  <ReadOnlyValue>{backendName}</ReadOnlyValue>
                ) : (
                  <select
                    className="input"
                    value={state.backendId}
                    onChange={(e) => {
                      const id = e.target.value;
                      const b = backends.find((bk) => bk.id === id);
                      setState("backendId", id);
                      setState("model", modelOptionsFor(b)[0] ?? "");
                    }}
                  >
                    <option value="">选择 LLM 供应商</option>
                    {backends.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                )}
              </Field>
              <Field label="模型" hint={state.backendId ? undefined : "先选择 LLM 供应商。"}>
                {readOnly ? (
                  <ReadOnlyValue mono>{state.model}</ReadOnlyValue>
                ) : (
                  <select
                    className="input"
                    value={state.model}
                    onChange={(e) => setState("model", e.target.value)}
                    disabled={!state.backendId}
                  >
                    <option value="">{state.backendId ? "选择模型" : "先选择 LLM 供应商"}</option>
                    {modelOptions.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                )}
              </Field>
            </div>
          </LegendCard>

          {/* Capability Set */}
          <LegendCard
            legend="Capability Set"
            dotColor={dotGreen}
            lead="Actor 通过 Capability Set 获得可调用的能力（工具 / 函数）。没有合适的集合？去创建一个。"
          >
            <Field
              label="绑定 Capability Set"
              hint="选择后可在此 Actor 内调用该集合声明的全部能力。"
            >
              {readOnly ? (
                <ReadOnlyValue>{capabilitySetName}</ReadOnlyValue>
              ) : (
                <select
                  className="input"
                  value={state.capabilitySetId}
                  onChange={(e) => setState("capabilitySetId", e.target.value)}
                >
                  <option value="">选择 Capability Set</option>
                  {capabilitySets.map((cs) => (
                    <option key={cs.id} value={cs.id}>{cs.name}</option>
                  ))}
                </select>
              )}
            </Field>
          </LegendCard>

          {/* 预算与节流 */}
          <LegendCard legend="预算与节流" dotColor={dotAmber}>
            <div className="grid-2">
              {/* D1: per_run_budget.max_tokens ← demo 单回合上限 (tokens) */}
              <Field label="单回合上限 (tokens)">
                {readOnly ? (
                  <ReadOnlyValue>{state.maxTokens}</ReadOnlyValue>
                ) : (
                  <input
                    className="input"
                    type="number"
                    min={0}
                    step={256}
                    value={state.maxTokens}
                    onChange={(e) => setState("maxTokens", e.target.value)}
                  />
                )}
              </Field>
              {/* D: per_run_budget.max_steps ← demo 步数上限 (命 max_tool_calls) */}
              <Field label="步数上限">
                {readOnly ? (
                  <ReadOnlyValue>{state.maxSteps}</ReadOnlyValue>
                ) : (
                  <input
                    className="input"
                    type="number"
                    min={0}
                    value={state.maxSteps}
                    onChange={(e) => setState("maxSteps", e.target.value)}
                  />
                )}
              </Field>
            </div>
          </LegendCard>

          {mainAfter}
        </div>

        <aside className="editor__rail">
          {railBefore}

          {/* D-extra: status simplified to enabled/disabled (running/stopped). */}
          <RailCard title="状态">
            {readOnly ? (
              <p className="rail-status">
                <StatusPill variant={state.enabled ? "running" : "paused"}>
                  {state.enabled ? "运行中" : "已停止"}
                </StatusPill>
                <span>{state.enabled ? "正在接收并处理事件" : "不接收事件"}</span>
              </p>
            ) : (
              <div className="status-pick" role="radiogroup">
                <label className="radio">
                  <input
                    type="radio"
                    name="actor-status"
                    checked={state.enabled}
                    onChange={() => setState("enabled", true)}
                  />
                  <span className="rb" />
                  <span><StatusPill variant="running">运行中</StatusPill> 立即接收并处理事件</span>
                </label>
                <label className="radio">
                  <input
                    type="radio"
                    name="actor-status"
                    checked={!state.enabled}
                    onChange={() => setState("enabled", false)}
                  />
                  <span className="rb" />
                  <span><StatusPill variant="paused">已停止</StatusPill> 不接收事件</span>
                </label>
              </div>
            )}
          </RailCard>

          <RailCard
            title="事件来源"
            lead={readOnly
              ? "Actor 不直接绑定集成。事件经 Ingress 规则路由进入，可到 Ingress 页配置。"
              : "Actor 不直接绑定集成。事件经 Ingress 规则路由进入，保存后到 Ingress 页配置。"}
          >
            <Link to="/routes">
              <button type="button" className="btn btn--ghost">配置 Ingress</button>
            </Link>
          </RailCard>

          {railAfter}

          {mode !== "new" && onDelete && (
            <RailCard danger title="危险操作">
              <button
                type="button"
                className="btn btn--danger"
                onClick={onDelete}
                disabled={isPending}
              >
                删除此 Actor
              </button>
            </RailCard>
          )}

          {error && <p className="text-xs text-destructive">{error}</p>}
        </aside>
      </div>
    </>
  );
}

function ReadOnlyValue({
  children,
  empty = "-",
  block,
  mono,
}: {
  children?: ReactNode;
  empty?: string;
  block?: boolean;
  mono?: boolean;
}) {
  const value = typeof children === "string" ? children.trim() : children;
  return (
    <div className={`input input--readonly${block ? " input--readonly-block" : ""}${mono ? " input--mono" : ""}`}>
      {value || empty}
    </div>
  );
}

/** Compute the model option list for a given selected backend. Pure helper. */
export function modelOptionsFor(backend?: LLMBackendResource): string[] {
  if (!backend) return [];
  return Array.from(
    new Set(
      Object.keys(backend.model_configs ?? {})
        .map((m) => m?.trim())
        .filter(Boolean) as string[],
    ),
  ).sort();
}

function actorTypeChoices(current: string): string[] {
  return Array.from(new Set(["simple_loop", current.trim()].filter(Boolean)));
}

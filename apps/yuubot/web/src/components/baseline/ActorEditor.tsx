// ActorEditor — shared presentational editor body for /actors/new and
// /actors/$id/edit (ISSUE-0007 S3).
//
// Mirrors demo `view--editor` layout (hero + editor__cols main/rail) but is
// STATELESS: the owning route holds form state, mutations, and the <form>;
// this component renders the fields/rails bound to the passed state+setters.
// That keeps the schema-deviation surface (D1–D3 + D-extra) identical across
// new/edit while the route files retain the create/update contract evidence.
//
// Schema deviations vs the demo (design.md D1–D3 + D-extra):
//  • default_budget.max_tokens  ← demo "单回合上限 (tokens)"
//  • default_budget.max_steps   ← demo "步数上限" (demo said max_tool_calls)
//  • max_concurrent / cooldown  ← ActorResource has none → disabled + 待后端
//  • strict                     ← per-actor absent; only global BootstrapConfig
//                                 strict → read-only "全局开关" hint
//  • Agent 规格 select          ← no independent Agent pool; read-only "核对话助手 (yb)"
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
  backendId: string;
  model: string;
  capabilitySetId: string;
  maxTokens: string;
  maxSteps: string;
  enabled: boolean;
}

export interface ActorEditorProps {
  mode: "new" | "edit";
  actor?: ActorResource;
  state: ActorEditorState;
  setState: <K extends keyof ActorEditorState>(key: K, value: ActorEditorState[K]) => void;
  backends?: LLMBackendResource[];
  capabilitySets?: CapabilitySetResource[];
  modelOptions: string[];
  isPending: boolean;
  error?: string;
  onDelete?: () => void;
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
}: ActorEditorProps) {
  const heroAvatar = (state.name.trim()[0] ?? "A").toUpperCase();
  const dotIndigo: DotColor = "indigo";
  const dotGreen: DotColor = "green";
  const dotAmber: DotColor = "amber";
  const dotSlate: DotColor = "slate";

  return (
    <>
      {/* Hero / identity */}
      <div className="editor__hero">
        <div className="hero__avatar">{heroAvatar}</div>
        <div className="hero__fields">
          <Field label="名称" required inline>
            <input
              className="input"
              value={state.name}
              onChange={(e) => setState("name", e.target.value)}
              placeholder="例如：QQ 助手"
            />
          </Field>
          <Field label="一句话描述" inline>
            <input
              className="input"
              value={state.description}
              onChange={(e) => setState("description", e.target.value)}
              placeholder="这个 Actor 负责做什么？"
            />
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
          {/* Agent 与模型 */}
          <LegendCard legend="Agent 与模型" dotColor={dotIndigo}>
            <div className="grid-2">
              {/* D: no independent Agent resource pool — facade_module="yb" implies
                  the 核对话助手 agent; surfaced as a read-only hint, not a select. */}
              <Field
                label="Agent 规格"
                hint="偏离：无独立 Agent 资源池，默认绑定核对话助手 (yb)。"
              >
                <input className="input" value="核对话助手 (yb)" readOnly />
              </Field>
              <Field label="LLM 角色" hint="决定使用哪个 Provider / 模型。">
                <select
                  className="input"
                  value={state.backendId}
                  onChange={(e) => {
                    const id = e.target.value;
                    const b = backends.find((bk) => bk.id === id);
                    setState("backendId", id);
                    setState("model", b?.default_model ?? b?.models?.names?.[0] ?? "");
                  }}
                >
                  <option value="">选择 Backend</option>
                  {backends.map((b) => (
                    <option key={b.id} value={b.id}>{b.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="Default 模型">
                <select
                  className="input"
                  value={state.model}
                  onChange={(e) => setState("model", e.target.value)}
                  disabled={modelOptions.length === 0}
                >
                  <option value="">选择模型</option>
                  {modelOptions.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
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
            </Field>
          </LegendCard>

          {/* 预算与节流 */}
          <LegendCard legend="预算与节流" dotColor={dotAmber}>
            <div className="grid-2">
              {/* D1: default_budget.max_tokens ← demo 单回合上限 (tokens) */}
              <Field label="单回合上限 (tokens)">
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={256}
                  value={state.maxTokens}
                  onChange={(e) => setState("maxTokens", e.target.value)}
                />
              </Field>
              {/* D: default_budget.max_steps ← demo 步数上限 (命 max_tool_calls) */}
              <Field label="步数上限">
                <input
                  className="input"
                  type="number"
                  min={0}
                  value={state.maxSteps}
                  onChange={(e) => setState("maxSteps", e.target.value)}
                />
              </Field>
              {/* D2: max_concurrent absent on ActorResource → disabled placeholder */}
              <Field label="并发会话上限" hint="待后端支持">
                <input className="input" type="number" min={1} disabled placeholder="待后端支持" />
              </Field>
              {/* D2: cooldown_ms absent on ActorResource → disabled placeholder */}
              <Field label="冷却 (ms / 会话)" hint="待后端支持">
                <input className="input" type="number" min={0} step={100} disabled placeholder="待后端支持" />
              </Field>
            </div>
          </LegendCard>

          {/* 高级 — baseline LegendCard has no <details> variant; rendered as a
              non-collapsible card with the demo legend. */}
          <LegendCard legend="高级" dotColor={dotSlate} as="div">
            {/* D: system_prompt 覆盖 → edits underlying Character.system_prompt */}
            <Field label="系统提示词覆盖（可选）" hint="留空使用 Agent 默认；保存时写入底层 Character。">
              <textarea
                className="input"
                rows={3}
                value={state.systemPrompt}
                onChange={(e) => setState("systemPrompt", e.target.value)}
                placeholder="留空使用 Agent 默认"
              />
            </Field>
          </LegendCard>
        </div>

        <aside className="editor__rail">
          {/* D-extra: status simplified to enabled/disabled (running/stopped). */}
          <RailCard title="状态">
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
          </RailCard>

          <RailCard
            title="事件来源"
            lead="Actor 不直接绑定集成。事件经 Ingress 规则路由进入，保存后到 Ingress 页配置。"
          >
            <Link to="/routes">
              <button type="button" className="btn btn--ghost">配置 Ingress</button>
            </Link>
          </RailCard>

          {/* D3: per-actor strict absent; only global BootstrapConfig.strict. */}
          <RailCard
            title="严格模式"
            hint="严格模式为全局开关（见 BootstrapConfig），不在此 editor 修改。"
          >
            <label className="switch" aria-disabled="true">
              <input type="checkbox" disabled />
              <span className="switch__label">全局开关（只读）</span>
            </label>
          </RailCard>

          {mode === "edit" && onDelete && (
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

/** Compute the model option list for a given selected backend. Pure helper. */
export function modelOptionsFor(backend?: LLMBackendResource): string[] {
  if (!backend) return [];
  return Array.from(
    new Set(
      [backend.default_model, ...(backend.models?.names ?? [])]
        .map((m) => m?.trim())
        .filter(Boolean) as string[],
    ),
  ).sort();
}

import { useState } from "react";
import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { AlertTriangle, Trash2 } from "lucide-react";
import { useResourceList, useCreateResource, useDeleteResource } from "@/hooks/use-resources";
import type { ActorResource, LLMBackendResource } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { providerBaseUrlWarning } from "@/provider-models";
import { PRESET_ACTORS, presetActorCreatePayload } from "@/lib/presets";
import {
  PageShell,
  LegendCard,
  CrudHeader,
  Empty,
  Field,
  StatusPill,
  type DotColor,
} from "@/components/baseline";

// ---------------------------------------------------------------------------
// Provider presets — maps a human-readable API type to yuuagents provider key
// + default base URL. Local to this route (single source of the preset list).
// ---------------------------------------------------------------------------

interface ProviderPreset {
  key: string;
  label: string;
  baseUrl: string;
  runtimeProviderKey: string;
  providerName: string;
  /** Demo mark glyph shown in the preset card head. */
  mark: string;
  /** Short note under the preset name. */
  note: string;
}

const providerPresets: ProviderPreset[] = [
  {
    key: "openai",
    label: "OpenAI API",
    baseUrl: "https://api.openai.com/v1",
    runtimeProviderKey: "openai",
    providerName: "openai",
    mark: "OA",
    note: "GPT-4o / GPT-4o-mini 等官方接口。",
  },
  {
    key: "anthropic",
    label: "Anthropic API",
    baseUrl: "https://api.anthropic.com/v1",
    runtimeProviderKey: "anthropic",
    providerName: "anthropic",
    mark: "AN",
    note: "Claude 3.5 / Sonnet 系列官方接口。",
  },
  {
    key: "deepseek",
    label: "DeepSeek API",
    baseUrl: "https://api.deepseek.com",
    runtimeProviderKey: "openai",
    providerName: "deepseek",
    mark: "DS",
    note: "DeepSeek-V3 / R1，OpenAI 兼容。",
  },
  {
    key: "groq",
    label: "Groq API",
    baseUrl: "https://api.groq.com/openai/v1",
    runtimeProviderKey: "openai",
    providerName: "groq",
    mark: "GQ",
    note: "超低延迟推理，Llama 系列模型。",
  },
  {
    key: "openrouter",
    label: "OpenRouter API",
    baseUrl: "https://openrouter.ai/api/v1",
    runtimeProviderKey: "openrouter",
    providerName: "openrouter",
    mark: "OR",
    note: "聚合多模型，统一计费入口。",
  },
  {
    key: "google",
    label: "Google Gemini API",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    runtimeProviderKey: "openai",
    providerName: "google",
    mark: "GG",
    note: "Gemini 2.5 系列，OpenAI 兼容端点。",
  },
  {
    key: "xai",
    label: "xAI Grok API",
    baseUrl: "https://api.x.ai/v1",
    runtimeProviderKey: "openai",
    providerName: "xai",
    mark: "XA",
    note: "Grok 系列模型，OpenAI 兼容。",
  },
  {
    key: "custom",
    label: "Custom (OpenAI-compatible)",
    baseUrl: "http://localhost:11434/v1",
    runtimeProviderKey: "openai",
    providerName: "custom",
    mark: "CU",
    note: "任意 OpenAI 兼容端点（Ollama / vLLM 等）。",
  },
];

// ---------------------------------------------------------------------------
// Preset Actors offered after the FIRST backend create for OpenAI/DeepSeek.
// The preset list + create payload live in @/lib/presets (shared with the
// Actors page "update preset Actors" action).
// ---------------------------------------------------------------------------

/** Preset keys that trigger the onboarding dialog after the first backend. */
const onboardingPresetKeys = new Set(["openai", "deepseek"]);

/** Parse a free-form USD string into a number, or undefined when blank. */
function parseOptionalUsd(value: string): number | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export const Route = createFileRoute("/providers")({
  component: ProvidersPage,
});

// demo `view--providers` styling lives in styles/baseline.css (comprehensive
// structural port — single source of truth for all demo structural CSS),
// consolidated there during the ISSUE-0007 CSS-gap direct fix.

function ProvidersPage() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const { data: backends = [], isLoading, error } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: existingActors = [] } = useResourceList<ActorResource>("actors");
  const createMutation = useCreateResource<LLMBackendResource>("llm-backends");
  const createActorMutation = useCreateResource<ActorResource>("actors");
  const deleteMutation = useDeleteResource("llm-backends");

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    baseUrl: "",
    apiKey: "",
    dailyUsd: "2.00",
    monthlyUsd: "",
  });
  const [formError, setFormError] = useState("");
  // Onboarding dialog state: holds the freshly-created backend whose
  // default_model / id the preset Actors should bind to.
  const [onboardingBackend, setOnboardingBackend] = useState<LLMBackendResource | null>(null);
  const [onboardingBusy, setOnboardingBusy] = useState(false);
  const [onboardingError, setOnboardingError] = useState("");


  const selectedPreset = selectedKey
    ? (providerPresets.find((p) => p.key === selectedKey) ?? null)
    : null;
  const baseUrlWarning = providerBaseUrlWarning(selectedKey ?? "", form.baseUrl);

  if (pathname !== "/providers") {
    return <Outlet />;
  }

  if (isLoading) {
    return (
      <PageShell title="Providers">
        <Empty title="加载中…" description="正在读取已连接的后端。" />
      </PageShell>
    );
  }
  if (error) {
    return (
      <PageShell title="Providers">
        <Empty title="读取失败" description={error.message} />
      </PageShell>
    );
  }

  const selectPreset = (key: string) => {
    const preset = providerPresets.find((p) => p.key === key);
    if (!preset) return;
    setSelectedKey(key);
    // OpenAI and DeepSeek get theseeded onboarding budget default; other
    // presets keep editable budget fields but start blank (unlimited).
    const seededBudget = onboardingPresetKeys.has(preset.key) ? "2.00" : "";
    setForm({ name: `${key}-main`, baseUrl: preset.baseUrl, apiKey: "", dailyUsd: seededBudget, monthlyUsd: "" });
    setFormError("");
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedPreset) return;
    setFormError("");
    if (baseUrlWarning) {
      setFormError(baseUrlWarning);
      return;
    }
    // First-backend detection is frontend-side: capture the count BEFORE the
    // create call mutates the cache.
    const wasFirstBackend = backends.length === 0;
    const createdBackend = await createMutation.mutateAsync({
      name: form.name,
      yuuagents_provider: selectedPreset.runtimeProviderKey,
      model_capabilities: {
        chat: true,
        vision: false,
        tool_calling: true,
        reasoning: false,
        embedding: false,
        structured_output: false,
      },
      models: { names: [] },
      pricing: { entries: [] },
      budget: {
        daily_usd: parseOptionalUsd(form.dailyUsd),
        monthly_usd: parseOptionalUsd(form.monthlyUsd),
      },
      provider_options: {
        base_url: form.baseUrl || selectedPreset.baseUrl,
        provider_name: selectedPreset.providerName,
        api_key: form.apiKey,
        timeout: 60,
        max_retries: 2,
      },
      default_model: "",
      default_stream_options: {
        max_tokens: 4096,
        temperature: 0.7,
      },
    });
    setSelectedKey(null);
    if (wasFirstBackend && onboardingPresetKeys.has(selectedPreset.key)) {
      setOnboardingError("");
      setOnboardingBackend(createdBackend);
    }
  };

  // Create the preset Actors bound to the freshly-created backend. Uses the
  // normal Actor resource hook; references the stable seeded Character /
  // CapabilitySet ids. Skips presets whose Actor name already exists.
  const handleCreatePresetActors = async () => {
    if (!onboardingBackend) return;
    setOnboardingBusy(true);
    setOnboardingError("");
    try {
      for (const preset of PRESET_ACTORS) {
        const exists = existingActors.some((a) => a.name === preset.actorName);
        if (exists) continue;
        await createActorMutation.mutateAsync(
          presetActorCreatePayload(preset, onboardingBackend),
        );
      }
      setOnboardingBackend(null);
    } catch (err) {
      // Surface the (likely FK / mutation) error and leave the already-created
      // backend intact — do NOT silently mint replacement Character /
      // CapabilitySet records from the frontend.
      setOnboardingError(err instanceof Error ? err.message : String(err));
    } finally {
      setOnboardingBusy(false);
    }
  };

  const handleSkipOnboarding = () => {
    setOnboardingBackend(null);
    setOnboardingError("");
  };

  const handleDelete = (id: string) => {
    if (confirm("Delete this backend?")) deleteMutation.mutate(id);
  };

  return (
    <PageShell
      title="Providers"
      sub="选一个预设，填入 API key，系统会自动准备一个能聊的 Actor（默认 Character / CapabilitySet / Actor 串联就绪）。"
    >
      <div className="view">
        {/* Presets grid */}
        <div className="prov-presets">
          {providerPresets.map((preset) => {
            const connected = backends.find((b) => backendProviderKey(b) === preset.key);
            return (
              <PresetCard
                key={preset.key}
                preset={preset}
                connected={!!connected}
                connectedModel={connected?.default_model}
                onUse={() => selectPreset(preset.key)}
              />
            );
          })}
        </div>

        {/* Inline API key form (slot; not a floating popover — D-extra) */}
        {selectedPreset && (
          <div className="prov-form-slot">
            <LegendCard dotColor="indigo" legend={`${selectedPreset.label} 接入`} as="div">
              <form onSubmit={handleCreate} className="space-y-4">
                <Field label="显示名称">
                  <Input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    required
                  />
                </Field>
                <Field label="Base URL">
                  <Input
                    value={form.baseUrl}
                    onChange={(e) => setForm({ ...form, baseUrl: e.target.value })}
                  />
                </Field>
                <Field label="API Key">
                  <Input
                    type="password"
                    value={form.apiKey}
                    onChange={(e) => setForm({ ...form, apiKey: e.target.value })}
                    placeholder="sk-..."
                  />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="每日预算 (USD)">
                    <Input
                      inputMode="decimal"
                      value={form.dailyUsd}
                      onChange={(e) => setForm({ ...form, dailyUsd: e.target.value })}
                      placeholder="留空 = 不限"
                    />
                  </Field>
                  <Field label="每月预算 (USD)">
                    <Input
                      inputMode="decimal"
                      value={form.monthlyUsd}
                      onChange={(e) => setForm({ ...form, monthlyUsd: e.target.value })}
                      placeholder="留空 = 不限"
                    />
                  </Field>
                </div>
                {baseUrlWarning && (
                  <p className="flex items-center gap-1.5 text-xs text-destructive">
                    <AlertTriangle className="size-3.5" /> {baseUrlWarning}
                  </p>
                )}
                {formError && <p className="text-xs text-destructive">{formError}</p>}
                {createMutation.error && (
                  <p className="text-xs text-destructive">{createMutation.error.message}</p>
                )}
                <div className="flex gap-2">
                  <Button type="submit" disabled={createMutation.isPending}>
                    {createMutation.isPending ? "连接中…" : "连接"}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setSelectedKey(null)}
                  >
                    取消
                  </Button>
                </div>
              </form>
            </LegendCard>
          </div>
        )}

        {/* Connected backends list */}
        {backends.length > 0 && (
          <div className="prov-connected">
            <CrudHeader title="已连接的后端" count={backends.length} />
            <div className="prov-connected__list">
              {backends.map((backend) => (
                <BackendCard
                  key={backend.id}
                  backend={backend}
                  onDelete={handleDelete}
                  disabled={deleteMutation.isPending}
                />
              ))}
            </div>
          </div>
        )}

        {/* Onboarding dialog — offered after the FIRST OpenAI/DeepSeek backend
            create. Lets the user create ready-to-chat preset Actors bound to
            that backend in a single click. */}
        <Dialog open={onboardingBackend !== null} onOpenChange={(open) => { if (!open) handleSkipOnboarding(); }}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>创建预设 Actor？</DialogTitle>
              <DialogDescription>
                是否使用当前 LLM backend 创建预设 Actor?（安装后即可使用）
              </DialogDescription>
            </DialogHeader>
            {onboardingError && (
              <p className="text-xs text-destructive">{onboardingError}</p>
            )}
            <DialogFooter>
              <Button variant="ghost" onClick={handleSkipOnboarding} disabled={onboardingBusy}>
                跳过
              </Button>
              <Button onClick={handleCreatePresetActors} disabled={onboardingBusy}>
                {onboardingBusy ? "创建中…" : "创建"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </PageShell>
  );
}

// ---------------------------------------------------------------------------
// PresetCard — a preset tile (demo .preset). "使用/管理" CTA selects it.
// ---------------------------------------------------------------------------

function PresetCard({
  preset,
  connected,
  connectedModel,
  onUse,
}: {
  preset: ProviderPreset;
  connected: boolean;
  connectedModel?: string;
  onUse: () => void;
}) {
  return (
    <div className={`preset${connected ? " is-connected" : ""}`}>
      <div className="preset__head">
        <div className="preset__mark">{preset.mark}</div>
        <div className="preset__meta">
          <div className="preset__name">{preset.label}</div>
          <div className="preset__badge">
            {connected ? `已连接 / ${connectedModel || "—"}` : "预设"}
          </div>
        </div>
      </div>
      <p className="preset__note">{preset.note}</p>
      <div className="preset__foot">
        <button type="button" className="btn btn--ghost preset__cta" onClick={onUse}>
          {connected ? "管理" : "连接"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BackendCard — a connected backend rendered in LegendCard style.
// ---------------------------------------------------------------------------

function BackendCard({
  backend,
  onDelete,
  disabled,
}: {
  backend: LLMBackendResource;
  onDelete: (id: string) => void;
  disabled: boolean;
}) {
  const preset = providerPresets.find((p) => p.key === backendProviderKey(backend));
  const providerLabel = preset?.label ?? backend.yuuagents_provider;
  const missingPricing =
    budgetRequiresPricing(backend) && !hasPricingForDefaultModel(backend);
  const warning = providerBaseUrlWarning(
    backendProviderKey(backend),
    backend.provider_options?.base_url ?? "",
  );
  const dotColor: DotColor = warning || missingPricing ? "amber" : "green";
  const variant = warning || missingPricing ? "draft" : "connected";

  return (
    <LegendCard
      as="div"
      dotColor={dotColor}
      legend={backend.name}
      lead={providerLabel}
    >
      <div className="space-y-2 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">状态</span>
          <StatusPill variant={variant}>{variant === "connected" ? "active" : "需处理"}</StatusPill>
        </div>
        {backend.provider_options?.base_url && (
          <div className="flex justify-between gap-2">
            <span className="text-muted-foreground">Base URL</span>
            <code className="font-mono text-xs truncate max-w-[220px]">
              {backend.provider_options.base_url}
            </code>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-muted-foreground">Default Model</span>
          <code className="text-xs">{backend.default_model || "unset"}</code>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Daily Budget</span>
          <span>{formatUsd(backend.budget?.daily_usd)}</span>
        </div>
        {(missingPricing || warning) && (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <span>{warning || "Set input/output pricing before using USD budgets."}</span>
          </div>
        )}
        <div className="flex justify-between pt-2">
          <Link to="/providers/$id" params={{ id: backend.id }}>
            <Button variant="outline" size="xs">Edit</Button>
          </Link>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onDelete(backend.id)}
            disabled={disabled}
          >
            <Trash2 className="size-3.5 text-destructive" />
          </Button>
        </div>
      </div>
    </LegendCard>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function budgetRequiresPricing(backend: LLMBackendResource): boolean {
  return (backend.budget?.daily_usd ?? 0) > 0 || (backend.budget?.monthly_usd ?? 0) > 0;
}

function hasPricingForDefaultModel(backend: LLMBackendResource): boolean {
  if (!backend.default_model) {
    return true;
  }
  return backend.pricing.entries.some(
    (entry) => entry.model === backend.default_model,
  );
}

function backendProviderKey(backend: LLMBackendResource): string {
  return backend.provider_options?.provider_name || backend.yuuagents_provider;
}

function formatUsd(value: number | null | undefined): string {
  if (value == null) {
    return "unlimited";
  }
  return `$${value.toFixed(2)}`;
}

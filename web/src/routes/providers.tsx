import { useState } from "react";
import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { AlertTriangle, Plus, Trash2 } from "lucide-react";
import { useResourceList, useCreateResource, useDeleteResource } from "@/hooks/use-resources";
import type { LLMBackendResource } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { providerBaseUrlWarning } from "@/provider-models";

// ---------------------------------------------------------------------------
// Provider presets — maps a human-readable API type to yuuagents provider key
// + default base URL.
// ---------------------------------------------------------------------------

interface ProviderPreset {
  key: string;
  label: string;
  baseUrl: string;
  runtimeProviderKey: string;
  providerName: string;
}

const providerPresets: ProviderPreset[] = [
  {
    key: "openai",
    label: "OpenAI API",
    baseUrl: "https://api.openai.com/v1",
    runtimeProviderKey: "openai",
    providerName: "openai",
  },
  {
    key: "anthropic",
    label: "Anthropic API",
    baseUrl: "https://api.anthropic.com/v1",
    runtimeProviderKey: "anthropic",
    providerName: "anthropic",
  },
  {
    key: "deepseek",
    label: "DeepSeek API",
    baseUrl: "https://api.deepseek.com",
    runtimeProviderKey: "openai",
    providerName: "deepseek",
  },
  {
    key: "groq",
    label: "Groq API",
    baseUrl: "https://api.groq.com/openai/v1",
    runtimeProviderKey: "openai",
    providerName: "groq",
  },
  {
    key: "openrouter",
    label: "OpenRouter API",
    baseUrl: "https://openrouter.ai/api/v1",
    runtimeProviderKey: "openrouter",
    providerName: "openrouter",
  },
  {
    key: "google",
    label: "Google Gemini API",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    runtimeProviderKey: "openai",
    providerName: "google",
  },
  {
    key: "xai",
    label: "xAI Grok API",
    baseUrl: "https://api.x.ai/v1",
    runtimeProviderKey: "openai",
    providerName: "xai",
  },
  {
    key: "custom",
    label: "Custom (OpenAI-compatible)",
    baseUrl: "http://localhost:11434/v1",
    runtimeProviderKey: "openai",
    providerName: "custom",
  },
];

export const Route = createFileRoute("/providers")({
  component: ProvidersPage,
});

interface BackendFormData {
  name: string;
  providerKey: string; // selected preset key
  baseUrl: string;
  apiKey: string;
}

const defaultForm: BackendFormData = {
  name: "",
  providerKey: "openai",
  baseUrl: "https://api.openai.com/v1",
  apiKey: "",
};

function ProvidersPage() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const { data: backends = [], isLoading, error } = useResourceList<LLMBackendResource>("llm-backends");
  const createMutation = useCreateResource<LLMBackendResource>("llm-backends");
  const deleteMutation = useDeleteResource("llm-backends");

  const [form, setForm] = useState<BackendFormData>(defaultForm);
  const [formError, setFormError] = useState("");
  const baseUrlWarning = providerBaseUrlWarning(form.providerKey, form.baseUrl);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const preset = providerPresets.find((p) => p.key === form.providerKey);
    setFormError("");
    if (baseUrlWarning) {
      setFormError(baseUrlWarning);
      return;
    }
    await createMutation.mutateAsync({
      name: form.name,
      yuuagents_provider: preset?.runtimeProviderKey ?? form.providerKey,
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
      budget: {},
      provider_options: {
        base_url: form.baseUrl || (preset?.baseUrl ?? ""),
        provider_name: preset?.providerName ?? form.providerKey,
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
    setForm(defaultForm);
  };

  const handleProviderChange = (key: string) => {
    const preset = providerPresets.find((p) => p.key === key);
    if (!preset) return;
    setForm({
      ...form,
      providerKey: key,
      baseUrl: preset.baseUrl,
      // auto-generate name if user hasn't typed one yet, or reset on preset switch
      name: form.name && form.providerKey === key ? form.name : `${key}-main`,
    });
  };

  const handleDelete = (id: string) => {
    if (confirm("Delete this backend?")) deleteMutation.mutate(id);
  };

  if (pathname !== "/providers") {
    return <Outlet />;
  }

  if (isLoading) return <PageShell>Loading backends...</PageShell>;
  if (error) return <PageShell>Error: {error.message}</PageShell>;

  return (
    <PageShell>
      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Backend cards */}
        <div className="flex-1 space-y-4">
          {backends.length === 0 ? (
            <Empty text="No LLM backends configured" />
          ) : (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {backends.map((backend) => (
                <BackendCard
                  key={backend.id}
                  backend={backend}
                  onDelete={handleDelete}
                  disabled={deleteMutation.isPending}
                />
              ))}
            </div>
          )}
        </div>

        {/* Creation form */}
        <Card className="w-full lg:w-80">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Plus className="size-4" />
              New Backend
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-medium">
                  Name<span className="ml-0.5 text-destructive">*</span>
                </label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">
                  API Type<span className="ml-0.5 text-destructive">*</span>
                </label>
                <Select
                  value={form.providerKey}
                  onValueChange={handleProviderChange}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select API type" />
                  </SelectTrigger>
                  <SelectContent>
                    {providerPresets.map((preset) => (
                      <SelectItem key={preset.key} value={preset.key}>
                        {preset.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">Base URL</label>
                <Input
                  value={form.baseUrl}
                  onChange={(e) => setForm({ ...form, baseUrl: e.target.value })}
                />
                {baseUrlWarning && (
                  <p className="text-xs text-destructive">{baseUrlWarning}</p>
                )}
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium">API Key</label>
                <Input
                  type="password"
                  value={form.apiKey}
                  onChange={(e) => setForm({ ...form, apiKey: e.target.value })}
                  placeholder="sk-..."
                />
              </div>
              {formError && (
                <p className="text-xs text-destructive">{formError}</p>
              )}
              {createMutation.error && (
                <p className="text-xs text-destructive">
                  {createMutation.error.message}
                </p>
              )}
              <Button
                type="submit"
                className="w-full"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? "Saving..." : "Create Backend"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}

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
  const baseUrlWarning = providerBaseUrlWarning(
    backendProviderKey(backend),
    backend.provider_options?.base_url ?? "",
  );
  const statusLabel = baseUrlWarning
    ? "url invalid"
    : missingPricing
      ? "pricing missing"
      : "active";

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{backend.name}</CardTitle>
          <Badge variant={baseUrlWarning || missingPricing ? "destructive" : "default"}>
            {statusLabel}
          </Badge>
        </div>
        <CardDescription>{providerLabel}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {backend.provider_options?.base_url && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">Base URL</span>
            <span className="font-mono text-xs truncate max-w-[180px]">
              {backend.provider_options.base_url}
            </span>
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
        {missingPricing && (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <span>Set input/output pricing before using USD budgets.</span>
          </div>
        )}
        {baseUrlWarning && (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <span>{baseUrlWarning}</span>
          </div>
        )}
        <div className="flex justify-between pt-2">
          <Button variant="outline" size="xs" onClick={() => {}}>
            Test Connection
          </Button>
          <Link to="/providers/$id" params={{ id: backend.id }}>
            <Button variant="ghost" size="xs">Edit</Button>
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
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="p-6">{children}</div>;
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
      <p className="text-sm">{text}</p>
    </div>
  );
}

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

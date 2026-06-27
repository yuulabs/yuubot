import { useState, useEffect } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { AlertTriangle, ArrowLeft, Plus, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import {
  useResourceList,
  useDeleteResource,
  useUpdateResource,
} from "@/hooks/use-resources";
import type { LLMBackendResource, ModelCapabilities, ModelConfig } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";
import {
  fetchProviderModels,
  mergeModelOptions,
  providerBaseUrlWarning,
  validateProvider,
  type ProviderModelOption,
  type ProviderValidationResult,
} from "@/provider-models";
import {
  defaultModelConfigsForProvider,
  hasDefaultModelConfigs,
  PROVIDER_MODEL_PRICE_SOURCE,
} from "@/lib/provider-model-configs";

export const Route = createFileRoute("/providers/$id")({
  component: ProviderDetailPage,
});

interface ModelConfigForm {
  model: string;
  input_per_million: string;
  cached_input_per_million: string;
  output_per_million: string;
  chat: boolean;
  vision: boolean;
  tool_calling: boolean;
  reasoning: boolean;
  embedding: boolean;
  structured_output: boolean;
}

function ProviderDetailPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const deleteMutation = useDeleteResource("llm-backends");
  const updateMutation = useUpdateResource<LLMBackendResource>("llm-backends");

  const backend = backends.find((b) => b.id === id);

  const [name, setName] = useState(backend?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(backend?.provider_options?.base_url ?? "");
  const [dailyBudget, setDailyBudget] = useState(
    backend?.budget?.daily_usd?.toString() ?? "",
  );
  const [modelConfigRows, setModelConfigRows] = useState<ModelConfigForm[]>(
    modelConfigsToForm(backend?.model_configs ?? {}),
  );
  const [modelApiKey, setModelApiKey] = useState("");
  const [providerModels, setProviderModels] = useState<ProviderModelOption[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [isValidatingProvider, setIsValidatingProvider] = useState(false);
  const [providerValidation, setProviderValidation] =
    useState<ProviderValidationResult | null>(null);
  const [modelFetchError, setModelFetchError] = useState("");
  const [saveError, setSaveError] = useState("");

  // Sync form when backend data loads/updates
  useEffect(() => {
    if (backend) {
      setName(backend.name);
      setBaseUrl(backend.provider_options?.base_url ?? "");
      setDailyBudget(backend.budget?.daily_usd?.toString() ?? "");
      setModelConfigRows(modelConfigsToForm(backend.model_configs ?? {}));
      setProviderModels([]);
      setProviderValidation(null);
      setModelFetchError("");
      setSaveError("");
    }
  }, [backend]);

  useEffect(() => {
    if (!backend) {
      return;
    }
    void loadProviderModels(backend, baseUrl, "", {
      setProviderModels,
      setIsLoadingModels,
      setModelFetchError,
    });
  }, [backend, baseUrl]);

  if (!backend) {
    return (
      <div className="p-6">
        <Link to="/providers" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Back to providers
        </Link>
        <p className="text-muted-foreground">Backend not found.</p>
      </div>
    );
  }

  const baseUrlWarning = providerBaseUrlWarning(
    backendProviderKey(backend),
    baseUrl,
  );

  const handleDelete = () => {
    if (confirm(`Delete backend "${backend.name}"?`)) {
      deleteMutation.mutate(backend.id, {
        onSettled: () => navigate({ to: "/providers" }),
      });
    }
  };

  const handleSave = async () => {
    const modelConfigs = formRowsToModelConfigs(modelConfigRows);
    const budget = {
      ...backend.budget,
      daily_usd: parseOptionalUsd(dailyBudget),
    };
    if (budgetRequiresPricing(budget) && Object.keys(modelConfigs).length === 0) {
      setSaveError("Configure at least one model before enabling a USD budget.");
      return;
    }
    if (baseUrlWarning) {
      setSaveError(baseUrlWarning);
      return;
    }
    setSaveError("");
    await updateMutation.mutateAsync({
      id: backend.id,
      data: {
        name,
        budget,
        model_configs: modelConfigs,
        provider_options: {
          ...backend.provider_options,
          base_url: baseUrl,
          api_key: modelApiKey || backend.provider_options?.api_key || "",
        },
      },
    });
  };

  const updateModelConfigRow = (
    index: number,
    field: keyof ModelConfigForm,
    value: string | boolean,
  ) => {
    setModelConfigRows((entries) =>
      entries.map((entry, i) =>
        i === index ? { ...entry, [field]: value } : entry,
      ),
    );
  };

  const addModelConfigRow = () => {
    const pricedModels = new Set(
      modelConfigRows.map((entry) => entry.model).filter(Boolean),
    );
    const nextModel =
      modelOptions.find((option) => !pricedModels.has(option.id))?.id ??
      modelOptions[0]?.id ??
      "";
    setModelConfigRows((entries) => [
      ...entries,
      emptyModelConfigForm(nextModel),
    ]);
  };

  const removeModelConfigRow = (index: number) => {
    setModelConfigRows((entries) => entries.filter((_, i) => i !== index));
  };

  const restoreDefaultModelConfigs = () => {
    setModelConfigRows(
      modelConfigsToForm(defaultModelConfigsForProvider(backendProviderKey(backend))),
    );
  };

  const providerDefaultModelConfigs = defaultModelConfigsForProvider(
    backendProviderKey(backend),
  );
  const hasProviderDefaults = hasDefaultModelConfigs(backendProviderKey(backend));
  const normalizedModelConfigs = formRowsToModelConfigs(modelConfigRows);
  const modelOptions = mergeModelOptions(
    providerModels,
    Object.keys(providerDefaultModelConfigs),
    Object.keys(backend.model_configs ?? {}),
    modelConfigRows.map((entry) => entry.model),
  );
  const missingPricing =
    budgetRequiresPricing({ ...backend.budget, daily_usd: parseOptionalUsd(dailyBudget) }) &&
    Object.keys(normalizedModelConfigs).length === 0;

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/providers">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="size-4" />
            </Button>
          </Link>
          <div>
            <h1 className="text-xl font-bold">{backend.name}</h1>
            <p className="text-sm text-muted-foreground">Backend ID: {backend.id}</p>
          </div>
        </div>
        <Badge variant={missingPricing ? "destructive" : "default"}>
          {missingPricing ? "models missing" : "active"}
        </Badge>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Details</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Table>
              <TableBody>
                <TableRow>
                  <TableCell className="font-medium">Name</TableCell>
                  <TableCell>{backend.name}</TableCell>
                </TableRow>
	                <TableRow>
	                  <TableCell className="font-medium">Provider</TableCell>
	                  <TableCell><code>{backend.provider_identity}</code></TableCell>
                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Base URL</TableCell>
                  <TableCell className="font-mono text-xs">
                    {backend.provider_options?.base_url ?? "default"}
                  </TableCell>
                </TableRow>
	                <TableRow>
	                  <TableCell className="font-medium">Configured Models</TableCell>
	                  <TableCell><code>{Object.keys(backend.model_configs ?? {}).length}</code></TableCell>
	                </TableRow>
                <TableRow>
                  <TableCell className="font-medium">Daily Budget</TableCell>
                  <TableCell>{formatUsd(backend.budget?.daily_usd)}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Edit</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Name</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Base URL</label>
              <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
              {baseUrlWarning && (
                <p className="text-xs text-destructive">{baseUrlWarning}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Models API Key</label>
              <Input
                type="password"
                value={modelApiKey}
                onChange={(e) => setModelApiKey(e.target.value)}
                placeholder="sk-..."
              />
            </div>
            <Button
              variant="outline"
              className="w-full"
              onClick={() =>
                void loadProviderModels(backend, baseUrl, modelApiKey, {
                  setProviderModels,
                  setIsLoadingModels,
                  setModelFetchError,
                })
              }
              disabled={isLoadingModels}
            >
              <RefreshCw className="size-4" />
              {isLoadingModels ? "Loading Models..." : "Refresh Models"}
            </Button>
            <Button
              variant="outline"
              className="w-full"
              onClick={() =>
                void validateBackend(backend, baseUrl, modelApiKey, {
                  setProviderValidation,
                  setIsValidatingProvider,
                  setModelFetchError,
                })
              }
              disabled={isValidatingProvider}
            >
              {isValidatingProvider ? "Validating..." : "Validate Backend"}
            </Button>
            {providerValidation && (
              <div className="rounded-md border p-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span>Connection</span>
                  <Badge variant={providerValidation.valid ? "default" : "destructive"}>
                    {providerValidation.valid ? "valid" : "failed"}
                  </Badge>
                </div>
	                <div className="mt-1 flex items-center justify-between gap-2">
	                  <span>Live models</span>
	                  <Badge variant="outline">{providerValidation.models.length}</Badge>
	                </div>
                {providerValidation.detail && (
                  <p className="mt-2 text-destructive">{providerValidation.detail}</p>
                )}
              </div>
            )}
            {modelFetchError && (
              <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                <span>{modelFetchError}</span>
              </div>
            )}
            {!modelFetchError && modelOptions.length === 0 && (
              <p className="text-xs text-destructive">
                No provider models loaded yet.
              </p>
            )}
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Daily Budget ($)</label>
              <Input
                type="number"
                min="0"
                step="0.01"
                value={dailyBudget}
                onChange={(e) => setDailyBudget(e.target.value)}
              />
            </div>
            {(saveError || updateMutation.error) && (
              <p className="text-xs text-destructive">
                {saveError || updateMutation.error?.message}
              </p>
            )}
            <Button
              onClick={handleSave}
              className="w-full"
              disabled={updateMutation.isPending}
            >
              {updateMutation.isPending ? "Saving..." : "Save Changes"}
            </Button>
            <Button
              variant="destructive"
              className="w-full"
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="size-4" />
              Delete Backend
            </Button>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <CardTitle>Model Configs</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  Pricing is USD per 1M tokens. Capabilities control what Actors can safely assume.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {hasProviderDefaults && (
                  <Button variant="outline" size="xs" onClick={restoreDefaultModelConfigs}>
                    <RotateCcw className="size-3.5" />
                    Restore presets
                  </Button>
                )}
                <Button variant="outline" size="xs" onClick={addModelConfigRow}>
                  <Plus className="size-3.5" />
                  Add
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {missingPricing && (
              <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                <span>
	                  Add at least one configured model while USD budget is enabled.
                </span>
              </div>
            )}
            <div className="model-config-list">
              {modelConfigRows.map((entry, index) => (
                <ModelConfigEditor
                  key={index}
                  entry={entry}
                  index={index}
                  modelOptions={modelOptions}
                  onUpdate={updateModelConfigRow}
                  onRemove={removeModelConfigRow}
                />
              ))}
              {modelConfigRows.length === 0 && (
                <div className="model-config-empty">
                  No model configs saved.
                </div>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              Models shown by refresh are candidates only. Saving writes configured models into model_configs.
              {" "}
              {hasProviderDefaults ? PROVIDER_MODEL_PRICE_SOURCE : ""}
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

const MODEL_CAPABILITY_FIELDS: Array<{
  key: Exclude<keyof ModelConfigForm, "model" | `${string}_per_million`>;
  label: string;
}> = [
  { key: "chat", label: "chat" },
  { key: "vision", label: "vision" },
  { key: "tool_calling", label: "tools" },
  { key: "reasoning", label: "reasoning" },
  { key: "embedding", label: "embed" },
  { key: "structured_output", label: "schema" },
];

function ModelConfigEditor({
  entry,
  index,
  modelOptions,
  onUpdate,
  onRemove,
}: {
  entry: ModelConfigForm;
  index: number;
  modelOptions: ProviderModelOption[];
  onUpdate: (
    index: number,
    field: keyof ModelConfigForm,
    value: string | boolean,
  ) => void;
  onRemove: (index: number) => void;
}) {
  return (
    <div className="model-config-card">
      <div className="model-config-card__head">
        <div className="min-w-0 flex-1">
          <label className="model-config-card__label">Model</label>
          <ModelSelect
            value={entry.model}
            models={modelOptions}
            placeholder="model name"
            onValueChange={(value) => onUpdate(index, "model", value)}
          />
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => onRemove(index)}
          aria-label={`Remove ${entry.model || "model config"}`}
        >
          <Trash2 className="size-3.5 text-destructive" />
        </Button>
      </div>

      <div className="model-config-card__pricing">
        {MODEL_PRICE_FIELDS.map((field) => (
          <label key={field.key} className="model-price-field">
            <span>{field.label}</span>
            <Input
              type="number"
              min="0"
              step="0.000001"
              value={entry[field.key] ?? ""}
              onChange={(event) => onUpdate(index, field.key, event.target.value)}
              placeholder={field.placeholder}
            />
          </label>
        ))}
      </div>

      <div className="model-config-card__caps" aria-label="Model capabilities">
        {MODEL_CAPABILITY_FIELDS.map((field) => (
          <label
            key={field.key}
            className={`model-cap-chip ${entry[field.key] ? "is-on" : ""}`}
          >
            <input
              type="checkbox"
              checked={entry[field.key]}
              onChange={(event) =>
                onUpdate(index, field.key, event.target.checked)
              }
            />
            {field.label}
          </label>
        ))}
      </div>
    </div>
  );
}

const MODEL_PRICE_FIELDS: Array<{
  key: Extract<keyof ModelConfigForm, `${string}_per_million`>;
  label: string;
  placeholder: string;
}> = [
  { key: "input_per_million", label: "Input", placeholder: "input $/1M" },
  {
    key: "cached_input_per_million",
    label: "Cached input",
    placeholder: "cached $/1M",
  },
  { key: "output_per_million", label: "Output", placeholder: "output $/1M" },
];

function modelConfigsToForm(
  modelConfigs: Record<string, ModelConfig>,
): ModelConfigForm[] {
  return Object.entries(modelConfigs)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([model, config]) => {
      const pricing = config.pricing ?? {};
      const capabilities = { ...defaultCapabilities(), ...config.capabilities };
      return {
        model,
        input_per_million: String(pricing.input_per_million ?? ""),
        cached_input_per_million: String(pricing.cached_input_per_million ?? ""),
        output_per_million: String(pricing.output_per_million ?? ""),
        chat: capabilities.chat ?? true,
        vision: capabilities.vision ?? false,
        tool_calling: capabilities.tool_calling ?? false,
        reasoning: capabilities.reasoning ?? false,
        embedding: capabilities.embedding ?? false,
        structured_output: capabilities.structured_output ?? false,
      };
    });
}

function formRowsToModelConfigs(
  entries: ModelConfigForm[],
): Record<string, ModelConfig> {
  return Object.fromEntries(
    entries
      .filter((entry) => entry.model.trim())
      .map((entry) => [
        entry.model.trim(),
        {
          pricing: {
            input_per_million: Number(entry.input_per_million) || 0,
            cached_input_per_million:
              Number(entry.cached_input_per_million) || 0,
            output_per_million: Number(entry.output_per_million) || 0,
          },
          capabilities: {
            chat: entry.chat,
            vision: entry.vision,
            tool_calling: entry.tool_calling,
            reasoning: entry.reasoning,
            embedding: entry.embedding,
            structured_output: entry.structured_output,
          },
        },
      ]),
  );
}

function emptyModelConfigForm(model: string): ModelConfigForm {
  const capabilities = defaultCapabilities();
  return {
    model,
    input_per_million: "",
    cached_input_per_million: "",
    output_per_million: "",
    chat: capabilities.chat ?? true,
    vision: capabilities.vision ?? false,
    tool_calling: capabilities.tool_calling ?? false,
    reasoning: capabilities.reasoning ?? false,
    embedding: capabilities.embedding ?? false,
    structured_output: capabilities.structured_output ?? false,
  };
}

function defaultCapabilities(): ModelCapabilities {
  return {
    chat: true,
    vision: false,
    tool_calling: false,
    reasoning: false,
    embedding: false,
    structured_output: false,
  };
}

function ModelSelect({
  value,
  models,
  placeholder,
  onValueChange,
}: {
  value: string;
  models: ProviderModelOption[];
  placeholder: string;
  onValueChange: (value: string) => void;
}) {
  return (
    <Select
      value={value}
      onValueChange={onValueChange}
      disabled={models.length === 0}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {models.map((model) => (
          <SelectItem key={model.id} value={model.id}>
            {model.displayName ? `${model.displayName} (${model.id})` : model.id}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

async function loadProviderModels(
  backend: LLMBackendResource,
  baseUrl: string,
  apiKey: string,
  state: {
    setProviderModels: (models: ProviderModelOption[]) => void;
    setIsLoadingModels: (isLoading: boolean) => void;
    setModelFetchError: (error: string) => void;
  },
) {
  state.setIsLoadingModels(true);
  state.setModelFetchError("");
  try {
    const models = await fetchProviderModels({
      backendId: backend.id,
      providerKey: backendProviderKey(backend),
      baseUrl,
      apiKey,
    });
    state.setProviderModels(models);
  } catch (error) {
    state.setModelFetchError(error instanceof Error ? error.message : String(error));
  } finally {
    state.setIsLoadingModels(false);
  }
}

async function validateBackend(
  backend: LLMBackendResource,
  baseUrl: string,
  apiKey: string,
  state: {
    setProviderValidation: (result: ProviderValidationResult | null) => void;
    setIsValidatingProvider: (isValidating: boolean) => void;
    setModelFetchError: (error: string) => void;
  },
) {
  state.setIsValidatingProvider(true);
  state.setModelFetchError("");
  try {
    const result = await validateProvider({
      backendId: backend.id,
      providerKey: backendProviderKey(backend),
      baseUrl,
      apiKey,
    });
    state.setProviderValidation(result);
  } catch (error) {
    state.setProviderValidation(null);
    state.setModelFetchError(error instanceof Error ? error.message : String(error));
  } finally {
    state.setIsValidatingProvider(false);
  }
}

function parseOptionalUsd(value: string): number | null {
  if (!value.trim()) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function budgetRequiresPricing(budget: { daily_usd?: number | null; monthly_usd?: number | null }): boolean {
  return (budget.daily_usd ?? 0) > 0 || (budget.monthly_usd ?? 0) > 0;
}

function backendProviderKey(backend: LLMBackendResource): string {
  return backend.provider_identity;
}

function formatUsd(value: number | null | undefined): string {
  if (value == null) {
    return "unlimited";
  }
  return `$${value.toFixed(2)}`;
}

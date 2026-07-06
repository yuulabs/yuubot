import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import {
  getProvider,
  getProviderBalance,
  listProviderProtocols,
  putActor,
  putProvider,
  putProviderModelCard,
  refreshProviderCatalog,
  validateProvider,
} from "@/shared/lib/api";
import { describeApiError } from "@/shared/lib/api-errors";
import type { ActorRecord, ModelCard, ProviderInput } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import { DenseMeta, DenseSection, ErrorState, LoadingState, Page, ResourceList, ResourceListPrimary, Status } from "@/shared/components";
import { useBootstrap, useRefreshBootstrap } from "@/shared/hooks";

const PRESETS = {
  openai: {
    label: "OpenAI",
    providerId: "openai",
    name: "OpenAI",
    baseUrl: "",
    model: "gpt-4.1-mini",
    apiKey: "",
  },
  deepseek: {
    label: "DeepSeek",
    providerId: "deepseek",
    name: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-chat",
    apiKey: "",
  },
  custom: {
    label: "Custom compatible",
    providerId: "openai-compatible",
    name: "OpenAI-compatible",
    baseUrl: "",
    model: "model-name",
    apiKey: "",
  },
} as const;

type PresetKey = keyof typeof PRESETS;

export function ProviderDetailPage({ id }: { id: string }) {
  const navigate = useNavigate();
  const refreshBootstrap = useRefreshBootstrap();
  const { data: bootstrap } = useBootstrap();
  const protocols = useQuery({ queryKey: ["provider-protocols"], queryFn: listProviderProtocols });
  const detail = useQuery({
    queryKey: ["provider", id],
    queryFn: () => getProvider(id),
    enabled: id !== "new",
  });
  const defaultPreset = PRESETS.deepseek;
  const [providerId, setProviderId] = useState<string>(id === "new" ? defaultPreset.providerId : id);
  const [name, setName] = useState<string>(id === "new" ? defaultPreset.name : "");
  const [protocol, setProtocol] = useState("openai-compatible");
  const [baseUrl, setBaseUrl] = useState<string>(id === "new" ? defaultPreset.baseUrl : "");
  const [apiKey, setApiKey] = useState<string>(id === "new" ? defaultPreset.apiKey : "***");
  const [optionsText, setOptionsText] = useState("{}");
  const [card, setCard] = useState<ModelCard>({
    selector: id === "new" ? defaultPreset.model : "",
    toolcall: true,
    json: true,
    vision: false,
    input_price_per_million: 0,
    cached_input_price_per_million: 0,
    output_price_per_million: 0,
  });
  const [modelQuery, setModelQuery] = useState("");
  const [createDefaultActor, setCreateDefaultActor] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!detail.data) return;
    setProviderId(detail.data.id);
    setName(detail.data.name);
    setProtocol(detail.data.protocol);
    setBaseUrl(String(detail.data.config.endpoint ?? ""));
    setApiKey(String(detail.data.config.api_key ?? "***"));
    setOptionsText(JSON.stringify(detail.data.config.options ?? {}, null, 2));
    setCard(detail.data.model_cards[0] ?? {
      selector: "",
      toolcall: true,
      json: true,
      vision: false,
      input_price_per_million: 0,
      cached_input_price_per_million: 0,
      output_price_per_million: 0,
    });
  }, [detail.data]);

  const firstProvider = bootstrap ? bootstrap.providers.length === 0 : false;
  const supportedProtocols = useMemo(() => protocols.data?.map((item) => item.protocol) ?? ["openai-compatible"], [protocols.data]);
  const modelCards = detail.data?.model_cards ?? [];
  const filteredModelCards = useMemo(() => {
    const query = modelQuery.trim().toLowerCase();
    if (!query) return modelCards;
    return modelCards.filter((item) => item.selector.toLowerCase().includes(query));
  }, [modelCards, modelQuery]);
  const isNew = id === "new";
  const persistProvider = async () => {
    setError("");
    const urlWarning = endpointWarning(baseUrl);
    if (urlWarning) {
      throw new Error(urlWarning);
    }
    const options = parseOptions(optionsText);
    const input: ProviderInput = {
      name,
      protocol,
      config: {
        endpoint: normalizedEndpoint(baseUrl),
        api_key: apiKey,
        options,
      },
    };
    await putProvider(providerId, input);
    let savedCard: ModelCard | null = null;
    if (card.selector.trim()) {
      savedCard = await putProviderModelCard(providerId, { ...card, selector: card.selector.trim() });
    }
    if (firstProvider && createDefaultActor && savedCard?.configured) {
      const actor = defaultActor(providerId, name, savedCard);
      if (!bootstrap?.actors.some((item) => item.id === actor.id)) {
        await putActor(actor);
      }
    }
  };
  const afterPersist = async () => {
    refreshBootstrap();
    if (isNew) {
      await navigate({ to: "/providers/$id", params: { id: providerId }, replace: true });
      return;
    }
    void detail.refetch();
  };
  const save = useMutation({
    mutationFn: persistProvider,
    onSuccess: async () => {
      setMessage("Provider saved.");
      await afterPersist();
    },
    onError: (err) => setError(describeApiError(err)),
  });
  const validate = useMutation({
    mutationFn: async () => {
      await persistProvider();
      return validateProvider(providerId);
    },
    onSuccess: async (result) => {
      setMessage(`${result.ok ? "OK" : "Failed"}${result.message ? `: ${result.message}` : ""}`);
      await afterPersist();
    },
    onError: (err) => setError(describeApiError(err)),
  });
  const refreshCatalog = useMutation({
    mutationFn: () => refreshProviderCatalog(providerId),
    onSuccess: (result) => {
      setMessage(`Catalog refreshed: ${result.model_cards.length} model cards`);
      void detail.refetch();
      refreshBootstrap();
    },
    onError: (err) => setError(describeApiError(err)),
  });
  const balance = useMutation({
    mutationFn: () => getProviderBalance(providerId),
    onSuccess: (result) => {
      if (result.available === false) {
        setMessage("Balance is not available for this provider");
        return;
      }
      setMessage(`Balance: ${result.balance ?? "unknown"} ${result.currency ?? ""}`.trim());
    },
    onError: (err) => setError(describeApiError(err)),
  });

  if (protocols.isLoading || detail.isLoading) return <LoadingState />;
  if (protocols.error) return <ErrorState error={protocols.error} />;
  if (detail.error) return <ErrorState error={detail.error} />;

  return (
    <Page title={id === "new" ? "New Provider" : `Provider ${providerId}`} sub="Configure an OpenAI-compatible provider and its model card catalog.">
      <div className="dense-stack">
        <DenseSection
          title="Provider config"
          description={id === "new" ? "Create credentials and endpoint settings." : "Edit credentials and endpoint settings."}
          actions={id !== "new" && detail.data ? <Status enabled={detail.data.configured} label={detail.data.configured ? "configured" : "needs config"} /> : undefined}
        >
          <div className="dense-stack">
            {id === "new" && (
              <div className="flex flex-wrap gap-2">
                {(Object.keys(PRESETS) as PresetKey[]).map((key) => (
                  <Button key={key} type="button" variant="outline" size="sm" onClick={() => applyPreset(key)}>
                    {PRESETS[key].label}
                  </Button>
                ))}
              </div>
            )}
            {firstProvider && id === "new" && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={createDefaultActor} onChange={(event) => setCreateDefaultActor(event.target.checked)} />
                Create a default actor bound to this provider and model
              </label>
            )}
            <div className="dense-form-grid">
              <label className="grid gap-1">
                <span className="text-sm font-medium">Provider id</span>
                <input className="input" value={providerId} disabled={id !== "new"} onChange={(event) => setProviderId(event.target.value)} />
              </label>
              <label className="grid gap-1">
                <span className="text-sm font-medium">Display name</span>
                <input className="input" value={name} onChange={(event) => setName(event.target.value)} />
              </label>
              <label className="grid gap-1">
                <span className="text-sm font-medium">Protocol</span>
                <select className="input" value={protocol} onChange={(event) => setProtocol(event.target.value)}>
                  {supportedProtocols.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
              <label className="grid gap-1">
                <span className="text-sm font-medium">API key</span>
                <input className="input" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
              </label>
              <label className="grid gap-1 dense-field-span">
                <span className="text-sm font-medium">Base URL</span>
                <input className="input" value={baseUrl} placeholder="blank uses provider default" onChange={(event) => setBaseUrl(event.target.value)} />
                {baseUrl.trim() && <span className="page-sub">Normalized: {normalizedEndpoint(baseUrl)}</span>}
              </label>
              <label className="grid gap-1 dense-field-span">
                <span className="text-sm font-medium">Advanced options</span>
                <textarea className="textarea font-mono" rows={5} value={optionsText} onChange={(event) => setOptionsText(event.target.value)} />
              </label>
            </div>
          </div>
        </DenseSection>

        <div className="model-catalog-layout">
          <DenseSection
            title="Model catalog"
            description="Scan available models by selector, capability, and price. Select a row to edit it."
          >
            <div className="model-catalog-toolbar">
              <input
                className="input"
                value={modelQuery}
                placeholder="Search models"
                onChange={(event) => setModelQuery(event.target.value)}
              />
              <span className="model-catalog-count">{filteredModelCards.length}/{modelCards.length} models</span>
            </div>
            <ResourceList
              rows={filteredModelCards}
              getRowId={(item) => item.selector}
              emptyLabel={modelCards.length ? "No models match the search." : "No model cards yet."}
              columns={[
                {
                  key: "selector",
                  label: "Selector",
                  render: (item) => (
                    <ResourceListPrimary
                      title={
                        <button
                          className={`model-selector-button${item.selector === card.selector ? " is-selected" : ""}`}
                          type="button"
                          onClick={() => setCard(item)}
                        >
                          {item.selector}
                        </button>
                      }
                      subtitle={
                        !item.configured
                          ? "pricing required"
                          : item.selector === card.selector
                            ? "selected"
                            : undefined
                      }
                    />
                  ),
                },
                {
                  key: "caps",
                  label: "Caps",
                  render: (item) => (
                    <div className="dense-chip-row">
                      <span className={`dense-chip${item.toolcall ? " dense-chip--ok" : " dense-chip--muted"}`}>tools</span>
                      <span className={`dense-chip${item.json ? " dense-chip--ok" : " dense-chip--muted"}`}>json</span>
                      <span className={`dense-chip${item.vision ? " dense-chip--ok" : " dense-chip--muted"}`}>vision</span>
                    </div>
                  ),
                },
                {
                  key: "pricing",
                  label: "Pricing",
                  render: (item) => (
                    <DenseMeta
                      items={[
                        { label: "In", value: priceValue(item.input_price_per_million) },
                        { label: "Cached", value: priceValue(item.cached_input_price_per_million) },
                        { label: "Out", value: priceValue(item.output_price_per_million) },
                      ]}
                    />
                  ),
                },
              ]}
            />
          </DenseSection>

          <DenseSection
            title="Selected model"
            description="Edit the selector, capabilities, and per-million token prices for the active model."
          >
            <div className="dense-stack">
              <DenseMeta
                items={[
                  { label: "Selector", value: card.selector || "missing", tone: card.selector ? "default" : "warning" },
                  { label: "Input $/M", value: priceValue(card.input_price_per_million) },
                  { label: "Cached $/M", value: priceValue(card.cached_input_price_per_million) },
                  { label: "Output $/M", value: priceValue(card.output_price_per_million) },
                ]}
              />
              <label className="grid gap-1">
                <span className="text-sm font-medium">Selector</span>
                <input className="input" value={card.selector} onChange={(event) => setCard({ ...card, selector: event.target.value })} />
              </label>
              <div className="dense-chip-row">
                <label className={`model-cap-chip${card.toolcall ? " is-on" : ""}`}>
                  <input type="checkbox" checked={Boolean(card.toolcall)} onChange={(event) => setCard({ ...card, toolcall: event.target.checked })} />
                  Tool calls
                </label>
                <label className={`model-cap-chip${card.json ? " is-on" : ""}`}>
                  <input type="checkbox" checked={Boolean(card.json)} onChange={(event) => setCard({ ...card, json: event.target.checked })} />
                  JSON
                </label>
                <label className={`model-cap-chip${card.vision ? " is-on" : ""}`}>
                  <input type="checkbox" checked={Boolean(card.vision)} onChange={(event) => setCard({ ...card, vision: event.target.checked })} />
                  Vision
                </label>
              </div>
              <div className="dense-form-grid dense-form-grid--compact">
                <PriceInput label="Input $/M" value={card.input_price_per_million} onChange={(value) => setCard({ ...card, input_price_per_million: value })} />
                <PriceInput label="Cached input $/M" value={card.cached_input_price_per_million} onChange={(value) => setCard({ ...card, cached_input_price_per_million: value })} />
                <PriceInput label="Output $/M" value={card.output_price_per_million} onChange={(value) => setCard({ ...card, output_price_per_million: value })} />
              </div>
            </div>
          </DenseSection>
        </div>

        <div className="dense-actions-bar">
          <div className="dense-actions-bar__status">
            {message || error || "Save changes or run provider-side checks."}
          </div>
          <div className="dense-actions-bar__buttons">
            <Button type="button" disabled={save.isPending || validate.isPending} onClick={() => save.mutate()}>Save Provider</Button>
            <Button type="button" variant="outline" disabled={save.isPending || validate.isPending} onClick={() => validate.mutate()}>Validate</Button>
            {id !== "new" && <Button type="button" variant="outline" disabled={balance.isPending} onClick={() => balance.mutate()}>Balance</Button>}
            {id !== "new" && <Button type="button" variant="outline" disabled={refreshCatalog.isPending} onClick={() => refreshCatalog.mutate()}>Refresh Catalog</Button>}
          </div>
        </div>
      </div>
    </Page>
  );

  function applyPreset(key: PresetKey) {
    const preset = PRESETS[key];
    setProviderId(preset.providerId);
    setName(preset.name);
    setProtocol("openai-compatible");
    setBaseUrl(preset.baseUrl);
    setApiKey(preset.apiKey);
    setCard({ ...card, selector: preset.model });
  }
}

function PriceInput({ label, value, onChange }: { label: string; value: number | null | undefined; onChange: (value: number) => void }) {
  return (
    <label className="grid gap-1">
      <span className="text-sm font-medium">{label}</span>
      <input className="input" type="number" min="0" step="0.01" value={value ?? 0} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function priceValue(value: number | null | undefined): string {
  if (value === null || value === undefined) return "not set";
  return `$${value.toFixed(2)}`;
}

function parseOptions(text: string): Record<string, unknown> {
  const parsed = JSON.parse(text || "{}") as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("options must be a JSON object");
  }
  return parsed as Record<string, unknown>;
}

function normalizedEndpoint(value: string): string {
  return value.trim().replace(/\/chat\/completions\/?$/, "").replace(/\/+$/, "");
}

function endpointWarning(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  try {
    const parsed = new URL(trimmed);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return "Base URL must use http or https";
    }
    return "";
  } catch {
    return "Base URL is not a valid URL";
  }
}

function defaultActor(providerId: string, providerName: string, model: ModelCard): ActorRecord {
  return {
    id: `${providerId}-assistant`,
    name: `${providerName || providerId} Assistant`,
    description: "Default actor created during first provider setup.",
    persona: "You are a concise, practical assistant.",
    provider: providerId,
    model,
  };
}

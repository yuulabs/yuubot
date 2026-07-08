import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { getProvider } from "@/shared/lib/api";
import { describeApiError } from "@/shared/lib/api-errors";
import type { ActorInput, ActorRecord, BootstrapSnapshot, ModelCard } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import { DenseSection } from "@/shared/components";

const DEFAULT_CONTEXT_COMPRESSION_TOKENS = 262144;

export function ActorForm({
  initial,
  bootstrap,
  saveLabel,
  onSave,
}: {
  initial: ActorRecord;
  bootstrap: BootstrapSnapshot;
  saveLabel: string;
  onSave: (actorId: string, input: ActorInput) => Promise<unknown>;
}) {
  const [id, setId] = useState(initial.id);
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description ?? "");
  const [workspace, setWorkspace] = useState(initial.workspace ?? "");
  const [persona, setPersona] = useState(initial.persona ?? "");
  const [provider, setProvider] = useState(initial.provider);
  const [model, setModel] = useState<ModelCard>(initial.model);
  const [contextCompressionTokens, setContextCompressionTokens] = useState(
    initial.context_compression_tokens ?? DEFAULT_CONTEXT_COMPRESSION_TOKENS,
  );
  const [message, setMessage] = useState("");
  const [saveError, setSaveError] = useState("");
  const providerDetail = useQuery({
    queryKey: ["provider", provider],
    queryFn: () => getProvider(provider),
    enabled: Boolean(provider),
  });
  const allCards = useMemo(() => providerDetail.data?.model_cards ?? [], [providerDetail.data?.model_cards]);
  const cards = useMemo(
    () => allCards.filter((card) => card.configured),
    [allCards],
  );

  useEffect(() => {
    if (!cards.length) return;
    if (!cards.some((card) => card.selector === model.selector)) {
      setModel((current) => ({ ...cards[0], reasoning_effort: current.reasoning_effort ?? "" }));
    }
  }, [cards, model.selector]);

  const pricingHint = provider && allCards.length > 0 && cards.length === 0
    ? "No models have pricing yet. Open the provider page, pick a model, set per-million prices (0 is allowed), and save before creating an actor."
    : provider && allCards.length === 0
      ? "This provider has no model cards yet. Refresh the catalog on the provider page first."
      : "";

  return (
    <div className="dense-stack">
      <DenseSection title="Identity" description="Name the actor and bind it to a workspace.">
        <div className="dense-form-grid">
          <TextField label="Actor id" value={id} onChange={setId} />
          <TextField label="Name" value={name} onChange={setName} />
          <TextField label="Description" value={description} onChange={setDescription} />
          <label className="grid gap-1">
            <span className="text-sm font-medium">Workspace</span>
            <input
              className="input"
              value={workspace}
              placeholder={id || "actor-id"}
              onChange={(event) => setWorkspace(event.target.value)}
            />
            <span className="text-xs text-muted-foreground">
              Relative to {bootstrap.workspace_dir}/ unless absolute.
            </span>
          </label>
        </div>
      </DenseSection>
      <DenseSection title="Model binding" description="Choose a provider model that already has pricing configured.">
        <div className="dense-form-grid">
          <label className="grid gap-1">
            <span className="text-sm font-medium">Provider</span>
            <select className="input" value={provider} onChange={(event) => setProvider(event.target.value)}>
              <option value="">Provider</option>
              {bootstrap.providers.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
            </select>
          </label>
          <label className="grid gap-1">
            <span className="text-sm font-medium">Model</span>
            <select
              className="input"
              value={model.selector}
              disabled={!cards.length}
              onChange={(event) => {
                const selected = cards.find((card) => card.selector === event.target.value);
                setModel((current) => selected ? { ...selected, reasoning_effort: current.reasoning_effort ?? "" } : { ...current, selector: event.target.value });
              }}
            >
              <option value="">{cards.length ? "Select model" : "No priced models"}</option>
              {cards.map((card) => <option key={card.selector} value={card.selector}>{card.selector}</option>)}
            </select>
          </label>
          <label className="grid gap-1">
            <span className="text-sm font-medium">Reasoning effort</span>
            <input
              className="input"
              value={model.reasoning_effort ?? ""}
              placeholder="provider raw value, e.g. low or high"
              onChange={(event) => setModel({ ...model, reasoning_effort: event.target.value })}
            />
          </label>
          <label className="grid gap-1">
            <span className="text-sm font-medium">Compression threshold</span>
            <input
              className="input"
              type="number"
              min="1"
              step="1"
              value={contextCompressionTokens}
              onChange={(event) => setContextCompressionTokens(Number(event.target.value))}
            />
          </label>
        </div>
        {pricingHint && <p className="text-sm text-muted-foreground">{pricingHint}</p>}
        {providerDetail.error && <p className="text-sm text-destructive">{describeApiError(providerDetail.error)}</p>}
      </DenseSection>
      <DenseSection title="Persona" description="Long-form behavior instructions for the actor.">
        <label className="grid gap-1">
          <span className="text-sm font-medium">Persona</span>
          <textarea className="textarea" rows={8} value={persona} onChange={(event) => setPersona(event.target.value)} />
        </label>
      </DenseSection>
      <div className="dense-actions-bar">
        <div className={`dense-actions-bar__status${saveError ? " text-destructive" : ""}`}>
          {saveError || message || "Save actor identity, model binding, and persona."}
        </div>
        <div className="dense-actions-bar__buttons">
          <Button
            disabled={!provider || !cards.length || !model.selector}
            onClick={async () => {
              try {
                setMessage("");
                setSaveError("");
                await onSave(id, {
                  name,
                  description,
                  workspace,
                  persona,
                  provider,
                  model,
                  context_compression_tokens: contextCompressionTokens,
                });
              } catch (err) {
                setSaveError(describeApiError(err));
              }
            }}
          >
            {saveLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="grid gap-1">
      <span className="text-sm font-medium">{label}</span>
      <input className="input" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

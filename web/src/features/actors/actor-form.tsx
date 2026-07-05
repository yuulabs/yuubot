import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { getProvider } from "@/shared/lib/api";
import type { ActorRecord, BootstrapSnapshot, ModelCard } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import { DenseSection } from "@/shared/components";

export function ActorForm({
  initial,
  bootstrap,
  saveLabel,
  onSave,
}: {
  initial: ActorRecord;
  bootstrap: BootstrapSnapshot;
  saveLabel: string;
  onSave: (record: ActorRecord) => Promise<unknown>;
}) {
  const [id, setId] = useState(initial.id);
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description ?? "");
  const [workspace, setWorkspace] = useState(initial.workspace ?? "");
  const [persona, setPersona] = useState(initial.persona ?? "");
  const [provider, setProvider] = useState(initial.provider);
  const [model, setModel] = useState<ModelCard>(initial.model);
  const [message, setMessage] = useState("");
  const providerDetail = useQuery({
    queryKey: ["provider", provider],
    queryFn: () => getProvider(provider),
    enabled: Boolean(provider),
  });
  const cards = useMemo(() => providerDetail.data?.model_cards ?? [], [providerDetail.data?.model_cards]);

  useEffect(() => {
    if (!cards.length) return;
    if (!cards.some((card) => card.selector === model.selector)) {
      setModel((current) => ({ ...cards[0], reasoning_effort: current.reasoning_effort ?? "" }));
    }
  }, [cards, model.selector]);

  return (
    <div className="dense-stack">
      <DenseSection title="Identity" description="Name the actor and bind it to a workspace.">
        <div className="dense-form-grid">
          <TextField label="Actor id" value={id} onChange={setId} />
          <TextField label="Name" value={name} onChange={setName} />
          <TextField label="Description" value={description} onChange={setDescription} />
          <TextField label="Workspace" value={workspace} onChange={setWorkspace} />
        </div>
      </DenseSection>
      <DenseSection title="Model binding" description="Choose the provider and model this actor will use.">
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
              onChange={(event) => {
                const selected = cards.find((card) => card.selector === event.target.value);
                setModel((current) => selected ? { ...selected, reasoning_effort: current.reasoning_effort ?? "" } : { ...current, selector: event.target.value });
              }}
            >
              <option value={model.selector}>{model.selector || "Model"}</option>
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
        </div>
        {providerDetail.error && <p className="text-sm text-destructive">{providerDetail.error instanceof Error ? providerDetail.error.message : String(providerDetail.error)}</p>}
      </DenseSection>
      <DenseSection title="Persona" description="Long-form behavior instructions for the actor.">
        <label className="grid gap-1">
          <span className="text-sm font-medium">Persona</span>
          <textarea className="textarea" rows={8} value={persona} onChange={(event) => setPersona(event.target.value)} />
        </label>
      </DenseSection>
      <div className="dense-actions-bar">
        <div className="dense-actions-bar__status">{message || "Save actor identity, model binding, and persona."}</div>
        <div className="dense-actions-bar__buttons">
          <Button
            onClick={async () => {
              try {
                setMessage("");
                await onSave({
                  id,
                  name,
                  description,
                  workspace,
                  persona,
                  provider,
                  model,
                });
              } catch (err) {
                setMessage(err instanceof Error ? err.message : String(err));
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

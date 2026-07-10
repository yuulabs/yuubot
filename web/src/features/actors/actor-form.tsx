import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { DenseSection } from "@/shared/components";
import { describeApiError } from "@/shared/lib/api-errors";
import { getGateway } from "@/shared/lib/api/gateway";
import type { ActorInput, ActorRecord, BootstrapSnapshot, ModelSelector } from "@/shared/types/api";

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
  const [model, setModel] = useState<ModelSelector>(initial.model);
  const [contextCompressionTokens, setContextCompressionTokens] = useState(initial.context_compression_tokens ?? DEFAULT_CONTEXT_COMPRESSION_TOKENS);
  const [message, setMessage] = useState("");
  const [saveError, setSaveError] = useState("");
  const gateway = useQuery({ queryKey: ["gateway"], queryFn: getGateway });
  const endpoint = model.type === "exact" ? gateway.data?.endpoints.find((item) => item.id === model.endpoint_id) : undefined;
  const validModel = model.type === "alias"
    ? Boolean(model.alias)
    : Boolean(model.endpoint_id && model.model.trim());

  return (
    <div className="dense-stack">
      <DenseSection title="Identity" description="Name and workspace.">
        <div className="dense-form-grid">
          <TextField label="Actor id" value={id} onChange={setId} />
          <TextField label="Name" value={name} onChange={setName} />
          <TextField label="Description" value={description} onChange={setDescription} />
          <label className="grid gap-1"><span className="text-sm font-medium">Workspace</span><input className="input" value={workspace} placeholder={id || "actor-id"} onChange={(event) => setWorkspace(event.target.value)} /><span className="text-xs text-muted-foreground">{bootstrap.workspace_dir}/</span></label>
        </div>
      </DenseSection>

      <DenseSection title="Model binding" description="Alias routing or one exact target.">
        <div className="mb-3 inline-flex rounded-md border p-0.5" role="group" aria-label="Model selection mode">
          <Button type="button" size="sm" variant={model.type === "alias" ? "default" : "ghost"} onClick={() => setModel({ type: "alias", alias: gateway.data?.aliases[0]?.id ?? "" })}>Alias</Button>
          <Button type="button" size="sm" variant={model.type === "exact" ? "default" : "ghost"} onClick={() => setModel({ type: "exact", endpoint_id: gateway.data?.endpoints[0]?.id ?? "", model: "" })}>Exact model</Button>
        </div>
        {model.type === "alias" ? (
          <label className="grid max-w-xl gap-1"><span className="text-sm font-medium">Alias</span><select className="input" value={model.alias} onChange={(event) => setModel({ type: "alias", alias: event.target.value })}><option value="">Select alias</option>{gateway.data?.aliases.map((alias) => <option key={alias.id} value={alias.id}>{alias.id} ({alias.modalities.join(", ")})</option>)}</select></label>
        ) : (
          <div className="dense-form-grid">
            <label className="grid gap-1"><span className="text-sm font-medium">Endpoint</span><select className="input" value={model.endpoint_id} onChange={(event) => setModel({ ...model, endpoint_id: event.target.value })}><option value="">Select endpoint</option>{gateway.data?.endpoints.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}</select></label>
            <label className="grid gap-1"><span className="text-sm font-medium">Model</span><input className="input" list="actor-exact-models" value={model.model} placeholder="model-id" onChange={(event) => setModel({ ...model, model: event.target.value })} /><datalist id="actor-exact-models">{endpoint?.models.map((item) => <option key={item} value={item} />)}</datalist>{model.model && endpoint?.models.length && !endpoint.models.includes(model.model) ? <span className="text-xs text-amber-700">Not currently discovered; exact selection is still allowed.</span> : null}</label>
          </div>
        )}
        <label className="mt-3 grid max-w-xs gap-1"><span className="text-sm font-medium">Compression threshold</span><input className="input" type="number" min="1" step="1" value={contextCompressionTokens} onChange={(event) => setContextCompressionTokens(Number(event.target.value))} /></label>
        {gateway.error && <p className="text-sm text-destructive">{describeApiError(gateway.error)}</p>}
      </DenseSection>

      <DenseSection title="Persona" description="Actor instructions.">
        <textarea className="textarea" rows={8} value={persona} onChange={(event) => setPersona(event.target.value)} />
      </DenseSection>
      <div className="dense-actions-bar">
        <div className={`dense-actions-bar__status${saveError ? " text-destructive" : ""}`}>{saveError || message}</div>
        <Button disabled={!validModel || !id.trim() || !name.trim()} onClick={async () => {
          try {
            setMessage(""); setSaveError("");
            await onSave(id, { name, description, workspace, persona, model, context_compression_tokens: contextCompressionTokens });
            setMessage("Saved");
          } catch (error) { setSaveError(describeApiError(error)); }
        }}>{saveLabel}</Button>
      </div>
    </div>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="grid gap-1"><span className="text-sm font-medium">{label}</span><input className="input" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

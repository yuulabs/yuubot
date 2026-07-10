import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { describeApiError } from "@/shared/lib/api-errors";
import {
  deleteAlias,
  deleteEndpoint,
  getGateway,
  putAlias,
  putEndpoint,
  refreshEndpoint,
  type AliasInput,
  type AliasTarget,
  type EndpointInput,
  type EndpointStatus,
  type GatewayAlias,
  type InputModality,
} from "@/shared/lib/api/gateway";
import { ErrorState, LoadingState, Page, ResourceCard, Status } from "@/shared/components";

const EMPTY_ENDPOINT: EndpointInput = {
  name: "",
  base_url: "",
  api_key: "",
  clear_api_key: false,
  connect_timeout_s: 10,
  request_timeout_s: 300,
  refresh_models: true,
};

const EMPTY_ALIAS: AliasInput = { modalities: ["text"], targets: [] };
const MODALITIES: InputModality[] = ["text", "image", "audio", "video"];

export function GatewayPage() {
  const queryClient = useQueryClient();
  const gateway = useQuery({ queryKey: ["gateway"], queryFn: getGateway, refetchInterval: 30_000 });
  const [endpointDialogOpen, setEndpointDialogOpen] = useState(false);
  const [aliasDialogOpen, setAliasDialogOpen] = useState(false);

  if (gateway.isLoading) return <LoadingState />;
  if (gateway.error) return <ErrorState error={gateway.error} />;

  const data = gateway.data;
  if (!data) return null;
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["gateway"] });

  return (
    <Page title="Gateway" sub="OpenAI-compatible endpoints and ordered aliases.">
      <section className="dense-stack">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">Endpoints</h2>
            <p className="text-sm text-muted-foreground">{data.endpoints.length} configured</p>
          </div>
          <Button size="sm" onClick={() => setEndpointDialogOpen(true)}><Plus /> Add Endpoint</Button>
        </div>

        {data.endpoints.map((endpoint) => (
          <EndpointEditor key={endpoint.id} endpoint={endpoint} onChanged={invalidate} />
        ))}
      </section>

      <section className="dense-stack mt-6 border-t pt-6">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">Aliases</h2>
            <p className="text-sm text-muted-foreground">{data.aliases.length} configured</p>
          </div>
          <Button
            size="sm"
            disabled={!data.endpoints.length}
            title={data.endpoints.length ? "Add alias" : "Add an endpoint first"}
            onClick={() => setAliasDialogOpen(true)}
          >
            <Plus /> Add Alias
          </Button>
        </div>

        {data.aliases.map((alias) => (
          <AliasEditor key={alias.id} alias={alias} endpoints={data.endpoints} onChanged={invalidate} />
        ))}
      </section>

      {endpointDialogOpen && (
        <EndpointCreateDialog
          existingIds={data.endpoints.map((item) => item.id)}
          onOpenChange={setEndpointDialogOpen}
          onCreated={() => { setEndpointDialogOpen(false); void invalidate(); }}
        />
      )}
      {aliasDialogOpen && (
        <AliasCreateDialog
          endpoints={data.endpoints}
          existingIds={data.aliases.map((item) => item.id)}
          onOpenChange={setAliasDialogOpen}
          onCreated={() => { setAliasDialogOpen(false); void invalidate(); }}
        />
      )}
    </Page>
  );
}

function EndpointCreateDialog({
  existingIds,
  onOpenChange,
  onCreated,
}: {
  existingIds: string[];
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [endpointId, setEndpointId] = useState("");
  const [draft, setDraft] = useState<EndpointInput>(EMPTY_ENDPOINT);
  const create = useMutation({
    mutationFn: () => putEndpoint(endpointId.trim(), draft),
    onSuccess: onCreated,
  });
  const duplicate = existingIds.includes(endpointId.trim());
  const error = duplicate ? `Endpoint ID "${endpointId.trim()}" already exists.` : create.error ? describeApiError(create.error) : "";

  return (
    <Dialog open onOpenChange={(next) => !create.isPending && onOpenChange(next)}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add Endpoint</DialogTitle>
          <DialogDescription>Connect one OpenAI-compatible API.</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!duplicate) create.mutate();
          }}
        >
          <div className="dense-form-grid">
            <Field label="Endpoint ID" value={endpointId} placeholder="openai" onChange={setEndpointId} autoFocus />
            <Field label="Name" value={draft.name} placeholder="OpenAI" onChange={(name) => setDraft({ ...draft, name })} />
            <Field label="Base URL" value={draft.base_url} placeholder="https://api.openai.com/v1" onChange={(base_url) => setDraft({ ...draft, base_url })} />
            <Field label="API key" value={draft.api_key} type="password" placeholder="Optional" onChange={(api_key) => setDraft({ ...draft, api_key })} />
            <label className="grid gap-1 text-sm">
              <span className="font-medium">Connect timeout</span>
              <input className="input" type="number" min="0.1" step="0.1" value={draft.connect_timeout_s} onChange={(event) => setDraft({ ...draft, connect_timeout_s: Number(event.target.value) })} />
            </label>
            <label className="grid gap-1 text-sm">
              <span className="font-medium">Request timeout</span>
              <input className="input" type="number" min="1" step="1" value={draft.request_timeout_s} onChange={(event) => setDraft({ ...draft, request_timeout_s: Number(event.target.value) })} />
            </label>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.refresh_models} onChange={(event) => setDraft({ ...draft, refresh_models: event.target.checked })} />
            Refresh models after saving
          </label>
          {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
          <DialogFooter>
            <DialogClose asChild><Button type="button" variant="outline">Cancel</Button></DialogClose>
            <Button type="submit" disabled={!endpointId.trim() || !draft.name.trim() || !draft.base_url.trim() || duplicate || create.isPending}>
              <Save /> {create.isPending ? "Saving" : "Save Endpoint"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AliasCreateDialog({
  endpoints,
  existingIds,
  onOpenChange,
  onCreated,
}: {
  endpoints: EndpointStatus[];
  existingIds: string[];
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [aliasId, setAliasId] = useState("");
  const [draft, setDraft] = useState<AliasInput>({
    ...EMPTY_ALIAS,
    targets: [{ endpoint_id: endpoints[0]?.id ?? "", model: "" }],
  });
  const create = useMutation({
    mutationFn: () => putAlias(aliasId.trim(), draft),
    onSuccess: onCreated,
  });
  const duplicate = existingIds.includes(aliasId.trim());
  const incomplete = !draft.targets.length || draft.targets.some((target) => !target.endpoint_id || !target.model.trim());
  const error = duplicate ? `Alias "${aliasId.trim()}" already exists.` : create.error ? describeApiError(create.error) : "";
  const updateTarget = (index: number, value: AliasTarget) => setDraft({
    ...draft,
    targets: draft.targets.map((item, itemIndex) => itemIndex === index ? value : item),
  });
  const move = (index: number, offset: number) => {
    const destination = index + offset;
    if (destination < 0 || destination >= draft.targets.length) return;
    const targets = [...draft.targets];
    const [target] = targets.splice(index, 1);
    if (!target) return;
    targets.splice(destination, 0, target);
    setDraft({ ...draft, targets });
  };

  return (
    <Dialog open onOpenChange={(next) => !create.isPending && onOpenChange(next)}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add Alias</DialogTitle>
          <DialogDescription>Declare accepted input modalities and an ordered fallback chain.</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!duplicate && !incomplete) create.mutate();
          }}
        >
          <Field label="Alias" value={aliasId} placeholder="fast" onChange={setAliasId} autoFocus />
          <fieldset className="grid gap-2">
            <legend className="text-sm font-medium">Input modalities</legend>
            <div className="flex flex-wrap gap-4">
              {MODALITIES.map((modality) => (
                <label key={modality} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    disabled={modality === "text"}
                    checked={draft.modalities.includes(modality)}
                    onChange={(event) => setDraft({
                      ...draft,
                      modalities: event.target.checked
                        ? [...draft.modalities, modality]
                        : draft.modalities.filter((item) => item !== modality),
                    })}
                  />
                  {modality}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-2">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium">Targets</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setDraft({ ...draft, targets: [...draft.targets, { endpoint_id: endpoints[0]?.id ?? "", model: "" }] })}
              >
                <Plus /> Add Target
              </Button>
            </div>
            {draft.targets.map((target, index) => {
              const endpoint = endpoints.find((item) => item.id === target.endpoint_id);
              return (
                <div key={index} className="grid gap-2 rounded-md border p-3 sm:grid-cols-[minmax(120px,0.7fr)_minmax(180px,1fr)_auto]">
                  <label className="grid gap-1 text-sm">
                    <span className="font-medium">Endpoint</span>
                    <select className="input" value={target.endpoint_id} onChange={(event) => updateTarget(index, { ...target, endpoint_id: event.target.value })}>
                      {endpoints.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
                    </select>
                  </label>
                  <label className="grid gap-1 text-sm">
                    <span className="font-medium">Model</span>
                    <input className="input" list={`new-alias-models-${index}`} value={target.model} placeholder="model-id" onChange={(event) => updateTarget(index, { ...target, model: event.target.value })} />
                    <datalist id={`new-alias-models-${index}`}>{endpoint?.models.map((model) => <option key={model} value={model} />)}</datalist>
                  </label>
                  <div className="flex items-end gap-1">
                    <Button type="button" variant="ghost" size="icon-sm" title="Move up" disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp /></Button>
                    <Button type="button" variant="ghost" size="icon-sm" title="Move down" disabled={index === draft.targets.length - 1} onClick={() => move(index, 1)}><ArrowDown /></Button>
                    <Button type="button" variant="ghost" size="icon-sm" title="Remove target" disabled={draft.targets.length === 1} onClick={() => setDraft({ ...draft, targets: draft.targets.filter((_, itemIndex) => itemIndex !== index) })}><Trash2 /></Button>
                  </div>
                </div>
              );
            })}
          </div>
          {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
          <DialogFooter>
            <DialogClose asChild><Button type="button" variant="outline">Cancel</Button></DialogClose>
            <Button type="submit" disabled={!aliasId.trim() || duplicate || incomplete || create.isPending}>
              <Save /> {create.isPending ? "Saving" : "Save Alias"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EndpointEditor({ endpoint, onChanged }: { endpoint: EndpointStatus; onChanged: () => void }) {
  const [draft, setDraft] = useState<EndpointInput>({
    name: endpoint.name,
    base_url: endpoint.base_url,
    api_key: "",
    clear_api_key: false,
    connect_timeout_s: endpoint.connect_timeout_s,
    request_timeout_s: endpoint.request_timeout_s,
    refresh_models: true,
  });
  const [error, setError] = useState("");
  const save = useMutation({ mutationFn: () => putEndpoint(endpoint.id, draft), onSuccess: onChanged, onError: (value) => setError(describeApiError(value)) });
  const refresh = useMutation({ mutationFn: () => refreshEndpoint(endpoint.id), onSuccess: onChanged, onError: (value) => setError(describeApiError(value)) });
  const remove = useMutation({ mutationFn: () => deleteEndpoint(endpoint.id), onSuccess: onChanged, onError: (value) => setError(describeApiError(value)) });

  return (
    <ResourceCard
      variant="neutral"
      title={endpoint.name || endpoint.id}
      subtitle={endpoint.id}
      status={<Status enabled={endpoint.connected} label={endpoint.connected ? "connected" : "disconnected"} />}
    >
      <div className="dense-form-grid">
        <Field label="Name" value={draft.name} onChange={(name) => setDraft({ ...draft, name })} />
        <Field label="Base URL" value={draft.base_url} placeholder="http://127.0.0.1:11434/v1" onChange={(base_url) => setDraft({ ...draft, base_url })} />
        <Field label="API key" value={draft.api_key} type="password" placeholder={endpoint.has_api_key ? "Stored" : "Optional"} onChange={(api_key) => setDraft({ ...draft, api_key, clear_api_key: false })} />
        <label className="grid gap-1 text-sm"><span className="font-medium">Connect timeout</span><input className="input" type="number" min="0.1" value={draft.connect_timeout_s} onChange={(event) => setDraft({ ...draft, connect_timeout_s: Number(event.target.value) })} /></label>
        <label className="grid gap-1 text-sm"><span className="font-medium">Request timeout</span><input className="input" type="number" min="1" value={draft.request_timeout_s} onChange={(event) => setDraft({ ...draft, request_timeout_s: Number(event.target.value) })} /></label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-2"><input type="checkbox" checked={draft.refresh_models} onChange={(event) => setDraft({ ...draft, refresh_models: event.target.checked })} />Refresh models on save</label>
        {endpoint.has_api_key && <label className="flex items-center gap-2"><input type="checkbox" checked={draft.clear_api_key} onChange={(event) => setDraft({ ...draft, clear_api_key: event.target.checked, api_key: "" })} />Clear stored key</label>}
      </div>
      <div className="mt-4 flex items-center justify-between gap-3 border-t pt-3">
        <div className="min-w-0 text-xs text-muted-foreground">
          {endpoint.models.length ? `${endpoint.models.length} discovered models` : "No discovered models"}
          {endpoint.last_error ? <span className="ml-2 text-destructive">{endpoint.last_error}</span> : null}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="icon-sm" title="Refresh models" disabled={refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw className={refresh.isPending ? "animate-spin" : ""} /></Button>
          <Button variant="outline" size="icon-sm" title="Delete endpoint" disabled={remove.isPending} onClick={() => remove.mutate()}><Trash2 /></Button>
          <Button size="sm" disabled={!draft.name.trim() || !draft.base_url.trim() || save.isPending} onClick={() => save.mutate()}><Save /> Save</Button>
        </div>
      </div>
      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
    </ResourceCard>
  );
}

function AliasEditor({ alias, endpoints, onChanged }: { alias: GatewayAlias; endpoints: EndpointStatus[]; onChanged: () => void }) {
  const [draft, setDraft] = useState<AliasInput>({ modalities: alias.modalities, targets: alias.targets });
  const [error, setError] = useState("");
  const save = useMutation({ mutationFn: () => putAlias(alias.id, draft), onSuccess: onChanged, onError: (value) => setError(describeApiError(value)) });
  const remove = useMutation({ mutationFn: () => deleteAlias(alias.id), onSuccess: onChanged, onError: (value) => setError(describeApiError(value)) });

  const updateTarget = (index: number, value: AliasTarget) => setDraft({ ...draft, targets: draft.targets.map((item, itemIndex) => itemIndex === index ? value : item) });
  const move = (index: number, offset: number) => {
    const next = [...draft.targets];
    const target = next[index];
    const destination = index + offset;
    if (!target || destination < 0 || destination >= next.length) return;
    next.splice(index, 1);
    next.splice(destination, 0, target);
    setDraft({ ...draft, targets: next });
  };

  return (
    <ResourceCard variant="neutral" title={alias.id} subtitle={`${draft.targets.length} target(s)`}>
      <div className="flex flex-wrap gap-4">
        {MODALITIES.map((modality) => (
          <label key={modality} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              disabled={modality === "text"}
              checked={draft.modalities.includes(modality)}
              onChange={(event) => setDraft({ ...draft, modalities: event.target.checked ? [...draft.modalities, modality] : draft.modalities.filter((item) => item !== modality) })}
            />
            {modality}
          </label>
        ))}
      </div>
      <div className="mt-3 grid gap-2">
        {draft.targets.map((target, index) => {
          const endpoint = endpoints.find((item) => item.id === target.endpoint_id);
          const missing = Boolean(target.model && endpoint?.models.length && !endpoint.models.includes(target.model));
          return (
            <div key={`${index}-${target.endpoint_id}`} className="grid gap-2 sm:grid-cols-[minmax(120px,0.7fr)_minmax(180px,1fr)_auto]">
              <select className="input" value={target.endpoint_id} onChange={(event) => updateTarget(index, { ...target, endpoint_id: event.target.value })}>
                <option value="">Endpoint</option>
                {endpoints.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
              </select>
              <div>
                <input className="input w-full" list={`models-${alias.id}-${index}`} value={target.model} placeholder="model-id" onChange={(event) => updateTarget(index, { ...target, model: event.target.value })} />
                <datalist id={`models-${alias.id}-${index}`}>{endpoint?.models.map((model) => <option key={model} value={model} />)}</datalist>
                {missing && <span className="text-xs text-amber-700">Not currently discovered</span>}
              </div>
              <div className="flex gap-1">
                <Button variant="ghost" size="icon-sm" title="Move up" disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp /></Button>
                <Button variant="ghost" size="icon-sm" title="Move down" disabled={index === draft.targets.length - 1} onClick={() => move(index, 1)}><ArrowDown /></Button>
                <Button variant="ghost" size="icon-sm" title="Remove target" onClick={() => setDraft({ ...draft, targets: draft.targets.filter((_, itemIndex) => itemIndex !== index) })}><Trash2 /></Button>
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex items-center justify-between border-t pt-3">
        <Button variant="outline" size="sm" onClick={() => setDraft({ ...draft, targets: [...draft.targets, { endpoint_id: endpoints[0]?.id ?? "", model: "" }] })}><Plus /> Target</Button>
        <div className="flex gap-2">
          <Button variant="outline" size="icon-sm" title="Delete alias" disabled={remove.isPending} onClick={() => remove.mutate()}><Trash2 /></Button>
          <Button size="sm" disabled={!draft.targets.length || draft.targets.some((item) => !item.endpoint_id || !item.model.trim()) || save.isPending} onClick={() => save.mutate()}><Save /> Save</Button>
        </div>
      </div>
      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
    </ResourceCard>
  );
}

function Field({ label, value, onChange, placeholder = "", type = "text", autoFocus = false }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string; type?: string; autoFocus?: boolean }) {
  return <label className="grid gap-1 text-sm"><span className="font-medium">{label}</span><input className="input" type={type} value={value} placeholder={placeholder} autoFocus={autoFocus} onChange={(event) => onChange(event.target.value)} /></label>;
}

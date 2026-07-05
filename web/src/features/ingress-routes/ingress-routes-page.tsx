import { useMemo, useState } from "react";

import { createRoute, deleteRoute, updateRoute } from "@/shared/lib/api";
import type { RouteRecord } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import {
  DeleteButton,
  EmptyState,
  ErrorState,
  LoadingState,
  Page,
  Panel,
  ResourceCard,
  ResourceCardGrid,
  ResourceMeta,
  Status,
} from "@/shared/components";
import { useApiMutation, useBootstrap } from "@/shared/hooks";

const emptyRoute: RouteRecord = { id: "", integration_type: "", pattern: "", actor_id: "", enabled: true };

export function IngressRoutesPage() {
  const { data, error, isLoading } = useBootstrap();
  const create = useApiMutation((record: RouteRecord) => createRoute(record));
  const update = useApiMutation((record: RouteRecord) => updateRoute(record));
  const remove = useApiMutation((id: string) => deleteRoute(id));
  const [draft, setDraft] = useState<RouteRecord>(emptyRoute);
  const [edits, setEdits] = useState<Record<string, RouteRecord>>({});
  const [actorFilter, setActorFilter] = useState("");
  const [integrationFilter, setIntegrationFilter] = useState("");
  const routes = useMemo(() => {
    return (data?.routes ?? []).filter((route) => {
      return (!actorFilter || route.actor_id === actorFilter) && (!integrationFilter || route.integration_type === integrationFilter);
    });
  }, [actorFilter, data?.routes, integrationFilter]);
  const integrationTypes = Array.from(new Set((data?.routes ?? []).map((route) => route.integration_type).filter(Boolean)));

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;

  return (
    <Page title="Routes" sub="Inbound route patterns mapped to actors.">
      <div className="grid gap-3">
        <Panel>
          <div className="grid gap-2 md:grid-cols-[1fr_1fr_1.4fr_1fr_auto_auto]">
            <input className="input" placeholder="id" value={draft.id} onChange={(event) => setDraft({ ...draft, id: event.target.value })} />
            <input className="input" placeholder="integration type" value={draft.integration_type} onChange={(event) => setDraft({ ...draft, integration_type: event.target.value })} />
            <input className="input" placeholder="pattern" value={draft.pattern} onChange={(event) => setDraft({ ...draft, pattern: event.target.value })} />
            <ActorSelect value={draft.actor_id} actors={data?.actors ?? []} onChange={(actorId) => setDraft({ ...draft, actor_id: actorId })} />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /> Enabled</label>
            <Button onClick={() => create.mutate({ ...draft, id: draft.id || draft.pattern })}>Create</Button>
          </div>
        </Panel>

        <Panel>
          <div className="grid gap-2 md:grid-cols-2">
            <ActorSelect value={actorFilter} actors={data?.actors ?? []} placeholder="All actors" onChange={setActorFilter} />
            <select className="input" value={integrationFilter} onChange={(event) => setIntegrationFilter(event.target.value)}>
              <option value="">All integrations</option>
              {integrationTypes.map((type) => <option key={type} value={type}>{type}</option>)}
            </select>
          </div>
        </Panel>

        {!routes.length ? <EmptyState>No routes configured.</EmptyState> : (
          <ResourceCardGrid>
            {routes.map((route) => {
              const edit = edits[route.id] ?? route;
              const actor = data?.actors.find((item) => item.id === edit.actor_id);
              return (
                <ResourceCard
                  key={route.id}
                  variant="route"
                  label={edit.integration_type || "any integration"}
                  title={edit.id}
                  subtitle={`${edit.pattern || "empty pattern"} -> ${actor?.name || edit.actor_id || "no actor"}`}
                  status={<Status enabled={edit.enabled} label={edit.enabled ? "enabled" : "disabled"} />}
                  actions={
                    <>
                      <Button variant="outline" size="sm" onClick={() => update.mutate({ ...edit, enabled: !edit.enabled })}>{edit.enabled ? "Disable" : "Enable"}</Button>
                      <Button variant="outline" size="sm" onClick={() => update.mutate(edit)}>Save</Button>
                      <DeleteButton onDelete={() => remove.mutate(route.id)} />
                    </>
                  }
                >
                  <div className="grid gap-2 md:grid-cols-[1fr_1fr_1.4fr_1fr]">
                    <input className="input" value={edit.id} disabled />
                    <input className="input" value={edit.integration_type} onChange={(event) => setEdit(route.id, { ...edit, integration_type: event.target.value })} />
                    <input className="input" value={edit.pattern} onChange={(event) => setEdit(route.id, { ...edit, pattern: event.target.value })} />
                    <ActorSelect value={edit.actor_id} actors={data?.actors ?? []} onChange={(actorId) => setEdit(route.id, { ...edit, actor_id: actorId })} />
                  </div>
                  <div className="resource-flow">
                    <span className="resource-flow__node">{edit.integration_type || "any"}</span>
                    <span className="resource-flow__arrow">-&gt;</span>
                    <span className="resource-flow__node">{edit.pattern || "empty pattern"}</span>
                    <span className="resource-flow__arrow">-&gt;</span>
                    <span className="resource-flow__node">{actor?.name || edit.actor_id || "no actor"}</span>
                  </div>
                  <ResourceMeta
                    items={[
                      { label: "Route id", value: edit.id },
                      { label: "Actor id", value: edit.actor_id || "unbound", tone: edit.actor_id ? "default" : "warning" },
                      { label: "Integration", value: edit.integration_type || "any" },
                      { label: "State", value: edit.enabled ? "enabled" : "disabled", tone: edit.enabled ? "ok" : "muted" },
                    ]}
                  />
                </ResourceCard>
              );
            })}
          </ResourceCardGrid>
        )}
      </div>
    </Page>
  );

  function setEdit(routeId: string, record: RouteRecord) {
    setEdits((current) => ({ ...current, [routeId]: record }));
  }
}

function ActorSelect({
  value,
  actors,
  onChange,
  placeholder = "Actor",
}: {
  value: string;
  actors: Array<{ id: string; name?: string }>;
  onChange: (actorId: string) => void;
  placeholder?: string;
}) {
  return (
    <select className="input" value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {actors.map((actor) => <option key={actor.id} value={actor.id}>{actor.name || actor.id}</option>)}
    </select>
  );
}

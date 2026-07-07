import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { SquareTerminal } from "lucide-react";

import { configureIntegration, disableIntegration, enableIntegration } from "@/shared/lib/api";
import type { IntegrationRecord } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import { DenseMeta, DenseSection, ErrorState, LoadingState, Page, ResourceList, ResourceListPrimary, Status } from "@/shared/components";
import { useApiMutation, useBootstrap } from "@/shared/hooks";

export function IntegrationDetailPage({ id }: { id: string }) {
  const navigate = useNavigate();
  const { data, error, isLoading } = useBootstrap();
  const save = useApiMutation((record: IntegrationRecord) => configureIntegration(record));
  const enable = useApiMutation((type: string) => enableIntegration(type));
  const disable = useApiMutation((type: string) => disableIntegration(type));
  const existing = data?.integrations.find((integration) => integration.type === id);
  const relatedRoutes = data?.routes.filter((route) => route.integration_type === id) ?? [];
  const fields = useMemo(() => schemaFields(existing?.config_schema), [existing?.config_schema]);
  const [name, setName] = useState(id);
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [advancedText, setAdvancedText] = useState("{}");
  const [message, setMessage] = useState("");

  useEffect(() => {
    setName(existing?.name ?? id);
    const existingConfig = existing?.config ?? {};
    const initial = Object.fromEntries(fields.map((field) => [field.name, existingConfig[field.name] ?? ""]));
    setConfig(initial);
    setAdvancedText(JSON.stringify(initial, null, 2));
  }, [existing?.config, existing?.name, fields, id]);

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  if (!existing) return <ErrorState error={`integration ${id} was not found`} />;

  return (
    <Page title={`Configure ${id}`} sub="Integration config is saved through the backend schema contract.">
      <div className="dense-stack">
        <DenseSection
          title="Integration status"
          description={existing.health_reason || "Runtime health is checked by the integration when available."}
          actions={
            existing.action_hint?.kind === "open_pty" ? (
              <Button variant="outline" asChild>
                <Link to="/terminal" search={{}}>
                  <SquareTerminal size={14} />
                  <span>Terminal</span>
                </Link>
              </Button>
            ) : undefined
          }
        >
          <DenseMeta items={[
            { label: "Enabled", value: <Status enabled={existing.enabled} label={existing.enabled ? "enabled" : "disabled"} /> },
            { label: "Health", value: existing.health_status || (existing.last_error ? "error" : "ready"), tone: existing.health_status === "ready" || (!existing.health_status && !existing.last_error) ? "ok" : "warning" },
            ...(existing.health_details?.binary_path ? [{ label: "Binary", value: String(existing.health_details.binary_path), tone: "ok" as const }] : []),
            { label: "Package", value: existing.package_path || "none" },
          ]} />
        </DenseSection>
        <DenseSection title="Integration config" description="Edit schema-backed fields for this integration.">
          <div className="dense-form-grid">
            <label className="grid gap-1">
              <span className="text-sm font-medium">Name</span>
              <input name="name" className="input" value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            {fields.map((field) => (
              <label className="grid gap-1" key={field.name}>
                <span className="text-sm font-medium">{field.name}{field.required ? " *" : ""}</span>
                <input
                  className="input"
                  name={field.name}
                  type={field.secret ? "password" : "text"}
                  value={String(config[field.name] ?? "")}
                  onChange={(event) => {
                    const next = { ...config, [field.name]: coerceValue(event.target.value, field.type) };
                    setConfig(next);
                    setAdvancedText(JSON.stringify(next, null, 2));
                  }}
                />
              </label>
            ))}
          </div>
        </DenseSection>
        <DenseSection title="Advanced JSON" description="Raw config payload saved to the backend.">
          <label className="grid gap-1">
            <span className="text-sm font-medium">Advanced config JSON</span>
            <textarea name="advanced-config-json" className="textarea font-mono" rows={7} value={advancedText} onChange={(event) => setAdvancedText(event.target.value)} />
          </label>
        </DenseSection>
        <div className="dense-actions-bar">
          <div className="dense-actions-bar__status">
            {message || (save.error ? save.error instanceof Error ? save.error.message : String(save.error) : "Save config or change runtime state.")}
          </div>
          <div className="dense-actions-bar__buttons">
            <Button
              disabled={save.isPending}
              onClick={async () => {
                try {
                  const parsed = parseObject(advancedText);
                  await save.mutateAsync({ id, type: id, name, config: parsed });
                  await navigate({ to: "/integrations" });
                } catch (err) {
                  setMessage(err instanceof Error ? err.message : String(err));
                }
              }}
            >
              Save Integration
            </Button>
            <Button variant="outline" disabled={enable.isPending || existing?.enabled} onClick={() => enable.mutate(id)}>Enable</Button>
            <Button variant="outline" disabled={disable.isPending || !existing?.enabled} onClick={() => disable.mutate(id)}>Disable</Button>
          </div>
        </div>
        <DenseSection title="Routes" description={`${relatedRoutes.length} route bindings use this integration.`}>
          <ResourceList
            rows={relatedRoutes}
            getRowId={(route) => route.id}
            emptyLabel="No routes for this integration."
            columns={[
              {
                key: "pattern",
                label: "Pattern",
                render: (route) => <ResourceListPrimary title={<span className="dense-code">{route.pattern}</span>} subtitle={route.id} />,
              },
              {
                key: "actor",
                label: "Actor",
                render: (route) => <span className="dense-code">{route.actor_id}</span>,
              },
              {
                key: "status",
                label: "Status",
                className: "is-tight",
                render: (route) => <span className={`dense-chip${route.enabled ? " dense-chip--ok" : " dense-chip--muted"}`}>{route.enabled ? "enabled" : "disabled"}</span>,
              },
            ]}
          />
        </DenseSection>
      </div>
    </Page>
  );
}

interface SchemaField {
  name: string;
  type: string;
  required: boolean;
  secret: boolean;
}

function schemaFields(schema: Record<string, unknown> | undefined): SchemaField[] {
  const resolved = resolveSchema(schema);
  const properties = resolved?.properties;
  const required = new Set(Array.isArray(resolved?.required) ? resolved.required.filter((item): item is string => typeof item === "string") : []);
  if (!properties || typeof properties !== "object" || Array.isArray(properties)) {
    return [];
  }
  return Object.entries(properties).map(([name, raw]) => {
    const item = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
    return {
      name,
      type: typeof item.type === "string" ? item.type : "string",
      required: required.has(name),
      secret: /token|secret|key|password/i.test(name),
    };
  });
}

function resolveSchema(schema: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
  if (!schema) return undefined;
  const ref = schema.$ref;
  const defs = schema.$defs;
  if (typeof ref === "string" && ref.startsWith("#/$defs/") && defs && typeof defs === "object" && !Array.isArray(defs)) {
    const resolved = (defs as Record<string, unknown>)[ref.slice("#/$defs/".length)];
    if (resolved && typeof resolved === "object" && !Array.isArray(resolved)) {
      return resolved as Record<string, unknown>;
    }
  }
  return schema;
}

function coerceValue(value: string, type: string): unknown {
  if (type === "boolean") return value === "true";
  if (type === "integer" || type === "number") return value ? Number(value) : 0;
  return value;
}

function parseObject(text: string): Record<string, unknown> {
  const parsed = JSON.parse(text || "{}") as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("config must be a JSON object");
  }
  return parsed as Record<string, unknown>;
}

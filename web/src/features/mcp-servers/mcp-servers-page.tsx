import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Plus, RefreshCw, Save, Trash2 } from "lucide-react";

import {
  deleteMcpServer,
  disableMcpServer,
  enableMcpServer,
  listMcpServers,
  putMcpServer,
  refreshMcpServer,
  startMcpOAuth,
} from "@/shared/lib/api";
import type { AuthAttempt, McpAuthMode, McpServerBody, McpServerSnapshot } from "@/shared/types/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DenseMeta,
  DenseSection,
  ErrorState,
  LoadingState,
  Page,
  ResourceActions,
  ResourceList,
  ResourceListPrimary,
  Status,
} from "@/shared/components";

const queryKey = ["mcp-servers"] as const;

const defaultForm: McpServerBody & { id: string } = {
  id: "",
  name: "",
  endpoint_url: "",
  transport: "http",
  auth_mode: "none",
  enabled: true,
  api_key: "",
  api_key_header: "Authorization",
  api_key_prefix: "Bearer ",
  oauth_issuer: "",
  oauth_authorization_endpoint: "",
  oauth_token_endpoint: "",
  oauth_client_id: "",
  oauth_client_secret: "",
  oauth_scope: "",
};

export function McpServersPage() {
  const query = useQuery({ queryKey, queryFn: listMcpServers });
  const client = useQueryClient();
  const [form, setForm] = useState(defaultForm);
  const save = useMutation({
    mutationFn: (body: typeof form) => putMcpServer(body.id, body),
    onSuccess: () => {
      setForm(defaultForm);
      client.invalidateQueries({ queryKey });
    },
  });
  const refresh = useServerMutation(refreshMcpServer);
  const enable = useServerMutation(enableMcpServer);
  const disable = useServerMutation(disableMcpServer);
  const remove = useServerMutation(deleteMcpServer);
  const authorize = useServerMutation(startMcpOAuth, openAuthAttemptUrl);
  const servers = query.data ?? [];
  const canSave = Boolean(form.id.trim() && form.name.trim() && form.endpoint_url.trim());
  const error = query.error ?? save.error ?? refresh.error ?? enable.error ?? disable.error ?? remove.error ?? authorize.error;

  if (query.isLoading) return <LoadingState />;

  return (
    <Page title="MCP Servers" sub="Remote MCP data source endpoints, daemon-managed credentials, and capability discovery.">
      <div className="dense-stack">
        {error && <ErrorState error={error} />}
        <DenseSection
          title="Servers"
          description={`${servers.length} MCP endpoints configured.`}
          actions={
            <Button variant="outline" onClick={() => setForm(defaultForm)}>
              <Plus size={14} />
              <span>New Server</span>
            </Button>
          }
        >
          <div className="crud-split crud-split--wide-form">
            <ResourceList
              rows={servers}
              getRowId={(server) => server.id}
              emptyLabel="No MCP servers configured."
              columns={[
                {
                  key: "server",
                  label: "Server",
                  render: (server) => (
                    <ResourceListPrimary
                      title={server.name || server.id}
                      subtitle={server.endpoint_url}
                      meta={<DenseMeta items={[
                        { label: "ID", value: server.id },
                        { label: "Auth", value: server.credential_configured ? `${server.auth_mode} ready` : server.auth_mode },
                        { label: "Transport", value: server.transport },
                      ]} />}
                    />
                  ),
                },
                {
                  key: "status",
                  label: "Status",
                  className: "is-tight",
                  render: (server) => <Status enabled={server.status === "ready"} label={server.status} />,
                },
                {
                  key: "capabilities",
                  label: "Capabilities",
                  render: (server) => (
                    <DenseMeta items={[
                      { label: "Tools", value: server.tools_count },
                      { label: "Resources", value: server.resources_count },
                      { label: "Prompts", value: server.prompts_count },
                    ]} />
                  ),
                },
                {
                  key: "actions",
                  label: "",
                  className: "is-actions",
                  render: (server) => (
                    <McpServerActions
                      server={server}
                      onEdit={() => setForm(draftFromServer(server))}
                      onRefresh={refresh.mutate}
                      onAuthorize={authorize.mutate}
                      onToggle={(id) => (server.enabled ? disable.mutate(id) : enable.mutate(id))}
                      onDelete={remove.mutate}
                    />
                  ),
                },
              ]}
            />
            <div className="crud-form">
              <div className="crud-form__head">
                <div className="crud-form__icon">
                  <KeyRound />
                </div>
                <div>
                  <div className="crud-form__title">{form.id ? `Edit ${form.id}` : "New MCP server"}</div>
                  <div className="crud-form__sub">Credentials stay daemon-managed.</div>
                </div>
              </div>
              <div className="crud-form__body">
                <LabeledInput label="Server ID" value={form.id} onChange={(value) => setForm({ ...form, id: value })} />
                <LabeledInput label="Display name" value={form.name} onChange={(value) => setForm({ ...form, name: value })} />
                <LabeledInput label="Endpoint URL" value={form.endpoint_url} onChange={(value) => setForm({ ...form, endpoint_url: value })} />
                <div className="dense-form-grid dense-form-grid--compact">
                  <label className="grid gap-1">
                    <span className="text-sm font-medium">Transport</span>
                    <select name="transport" className="input" value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value as typeof form.transport })}>
                      <option value="http">HTTP</option>
                      <option value="stdio">stdio</option>
                    </select>
                  </label>
                  <label className="grid gap-1">
                    <span className="text-sm font-medium">Auth mode</span>
                    <select name="auth-mode" className="input" value={form.auth_mode} onChange={(event) => setForm({ ...form, auth_mode: event.target.value as McpAuthMode })}>
                      <option value="none">No auth</option>
                      <option value="api_key">API key</option>
                      <option value="oauth_auto">OAuth auto</option>
                      <option value="oauth_manual">OAuth manual</option>
                    </select>
                  </label>
                </div>
                <label className="dense-checkbox">
                  <input name="enabled" type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />
                  <span>Enabled after save</span>
                </label>
                <LabeledInput label="API key header" value={form.api_key_header ?? ""} onChange={(value) => setForm({ ...form, api_key_header: value })} />
                {form.auth_mode === "api_key" && (
                  <>
                    <LabeledInput label="API key prefix" value={form.api_key_prefix ?? ""} onChange={(value) => setForm({ ...form, api_key_prefix: value })} />
                    <LabeledInput type="password" label="API key" value={form.api_key ?? ""} onChange={(value) => setForm({ ...form, api_key: value })} />
                  </>
                )}
                {form.auth_mode === "oauth_manual" && (
                  <>
                    <LabeledInput label="Issuer URL" value={form.oauth_issuer ?? ""} onChange={(value) => setForm({ ...form, oauth_issuer: value })} />
                    <LabeledInput label="Authorization endpoint" value={form.oauth_authorization_endpoint ?? ""} onChange={(value) => setForm({ ...form, oauth_authorization_endpoint: value })} />
                    <LabeledInput label="Token endpoint" value={form.oauth_token_endpoint ?? ""} onChange={(value) => setForm({ ...form, oauth_token_endpoint: value })} />
                    <LabeledInput label="Client ID" value={form.oauth_client_id ?? ""} onChange={(value) => setForm({ ...form, oauth_client_id: value })} />
                    <LabeledInput type="password" label="Client secret" value={form.oauth_client_secret ?? ""} onChange={(value) => setForm({ ...form, oauth_client_secret: value })} />
                    <LabeledInput label="Scope" value={form.oauth_scope ?? ""} onChange={(value) => setForm({ ...form, oauth_scope: value })} />
                  </>
                )}
                <div className="resource-actions">
                  <Button disabled={!canSave || save.isPending} onClick={() => save.mutate(form)}>
                    <Save size={14} />
                    <span>Save Server</span>
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </DenseSection>
      </div>
    </Page>
  );
}

function McpServerActions({
  server,
  onEdit,
  onRefresh,
  onAuthorize,
  onToggle,
  onDelete,
}: {
  server: McpServerSnapshot;
  onEdit: () => void;
  onRefresh: (id: string) => void;
  onAuthorize: (id: string) => void;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const healthTone = useMemo(() => {
    if (server.status === "ready") return "ok";
    if (server.status === "disabled") return "muted";
    return "warning";
  }, [server.status]);
  return (
    <ResourceActions>
      <span className={`dense-chip dense-chip--${healthTone}`}>{server.status}</span>
      <Button variant="outline" size="sm" onClick={onEdit}>
        <Pencil size={14} />
        <span>Edit</span>
      </Button>
      <Button variant="outline" size="sm" onClick={() => onRefresh(server.id)}>
        <RefreshCw size={14} />
        <span>Refresh</span>
      </Button>
      {isOAuthMode(server.auth_mode) && (
        <Button variant="outline" size="sm" onClick={() => onAuthorize(server.id)}>
          <KeyRound size={14} />
          <span>{server.credential_configured ? "Reauth" : "Authorize"}</span>
        </Button>
      )}
      <Button variant="outline" size="sm" onClick={() => onToggle(server.id)}>
        {server.enabled ? "Disable" : "Enable"}
      </Button>
      <Button variant="outline" size="sm" onClick={() => onDelete(server.id)}>
        <Trash2 size={14} />
      </Button>
    </ResourceActions>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  className,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
  type?: string;
}) {
  return (
    <label className={["grid gap-1", className].filter(Boolean).join(" ")}>
      <span className="text-sm font-medium">{label}</span>
      <Input name={fieldName(label)} type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function useServerMutation<T>(fn: (serverId: string) => Promise<T>, onSuccess?: (result: T) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      onSuccess?.(result);
      client.invalidateQueries({ queryKey });
    },
  });
}

function isOAuthMode(mode: McpAuthMode): boolean {
  return mode === "oauth_auto" || mode === "oauth_manual" || mode === "auto" || mode === "oauth";
}

function openAuthAttemptUrl(attempt: AuthAttempt): void {
  const url = attempt.action.url;
  if (typeof url === "string" && url) {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

function draftFromServer(server: McpServerSnapshot): typeof defaultForm {
  return {
    id: server.id,
    name: server.name,
    endpoint_url: server.endpoint_url,
    transport: server.transport,
    auth_mode: server.auth_mode,
    enabled: server.enabled,
    api_key: "",
    api_key_header: "Authorization",
    api_key_prefix: "Bearer ",
    oauth_issuer: server.oauth_issuer ?? "",
    oauth_authorization_endpoint: server.oauth_authorization_endpoint ?? "",
    oauth_token_endpoint: server.oauth_token_endpoint ?? "",
    oauth_client_id: server.oauth_client_id ?? "",
    oauth_client_secret: "",
    oauth_scope: server.oauth_scope ?? "",
  };
}

function fieldName(label: string): string {
  return label.toLowerCase().replace(/\s+/g, "-");
}

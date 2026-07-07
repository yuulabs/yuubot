import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { SquareTerminal } from "lucide-react";

import { disableIntegration, enableIntegration } from "@/shared/lib/api";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState, Page, ResourceCard, ResourceCardGrid, ResourceMeta, Status } from "@/shared/components";
import { useApiMutation, useBootstrap } from "@/shared/hooks";

export function IntegrationsListPage() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname !== "/integrations") {
    return <Outlet />;
  }
  const { data, error, isLoading } = useBootstrap();
  const enable = useApiMutation((type: string) => enableIntegration(type));
  const disable = useApiMutation((type: string) => disableIntegration(type));
  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  const integrations = data?.integrations ?? [];
  return (
    <Page title="Integrations" sub="Configured integration types and runtime enablement.">
      <div className="dense-stack">
        {!integrations.length ? <EmptyState>No integration types registered.</EmptyState> : (
          <ResourceCardGrid>
            {integrations.map((integration) => (
              <ResourceCard
                key={integration.type}
                variant="integration"
                label={integration.type}
                title={<Link className="font-medium underline-offset-4 hover:underline" to="/integrations/$id" params={{ id: integration.type }}>{integration.type}</Link>}
                subtitle={integration.name || integration.type}
                status={<Status enabled={integration.enabled && (integration.health_status ?? "ready") === "ready"} label={integration.enabled ? integration.health_status ?? "enabled" : "disabled"} />}
                actions={
                  <>
                    {integration.action_hint?.kind === "open_pty" && (
                      <Button variant="outline" size="sm" asChild>
                        <Link to="/terminal" search={{}}>
                          <SquareTerminal size={14} />
                          <span>Terminal</span>
                        </Link>
                      </Button>
                    )}
                    <Button variant="outline" size="sm" onClick={() => (integration.enabled ? disable.mutate(integration.type) : enable.mutate(integration.type))}>
                      {integration.enabled ? "Disable" : "Enable"}
                    </Button>
                    <Button variant="outline" size="sm" asChild>
                      <Link to="/integrations/$id" params={{ id: integration.type }}>Configure</Link>
                    </Button>
                  </>
                }
              >
                <ResourceMeta
                  items={[
                    { label: "Config", value: integration.configured ? "ready" : "missing", tone: integration.configured ? "ok" : "warning" },
                    { label: "Schema fields", value: Object.keys(integration.config_schema ?? {}).length },
                    { label: "Health", value: integration.health_status || (integration.last_error ? "error" : "ready"), tone: integration.health_status === "ready" || (!integration.health_status && !integration.last_error) ? "ok" : "warning" },
                    ...(integration.health_details?.binary_path ? [{ label: "Binary", value: String(integration.health_details.binary_path), tone: "ok" as const }] : []),
                    { label: "Package", value: integration.package_path || "none", tone: integration.package_path ? "default" : "muted" },
                  ]}
                />
                {(integration.health_reason || integration.last_error) && <pre className="resource-preview">{integration.health_reason || errorMessage(integration.last_error)}</pre>}
              </ResourceCard>
            ))}
          </ResourceCardGrid>
        )}
      </div>
    </Page>
  );
}

function errorMessage(error: Record<string, unknown> | null): string {
  const message = error?.message;
  return typeof message === "string" ? message : String(error);
}

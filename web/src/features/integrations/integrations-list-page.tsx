import { Link, Outlet, useRouterState } from "@tanstack/react-router";

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
      {!integrations.length ? <EmptyState>No integration types registered.</EmptyState> : (
        <ResourceCardGrid>
          {integrations.map((integration) => (
            <ResourceCard
              key={integration.type}
              variant="integration"
              label={integration.type}
              title={<Link className="font-medium underline-offset-4 hover:underline" to="/integrations/$id" params={{ id: integration.type }}>{integration.type}</Link>}
              subtitle={integration.name || integration.type}
              status={<Status enabled={integration.enabled} label={integration.enabled ? "enabled" : "disabled"} />}
              actions={
                <>
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
                  { label: "Health", value: integration.last_error ? "error" : "ready", tone: integration.last_error ? "warning" : "ok" },
                  { label: "Package", value: integration.package_path || "none", tone: integration.package_path ? "default" : "muted" },
                ]}
              />
              {integration.last_error && <pre className="resource-preview">{String(integration.last_error)}</pre>}
            </ResourceCard>
          ))}
        </ResourceCardGrid>
      )}
    </Page>
  );
}

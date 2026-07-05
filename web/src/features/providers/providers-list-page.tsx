import { Link, Outlet, useRouterState } from "@tanstack/react-router";

import { deleteProvider } from "@/shared/lib/api";
import { Button } from "@/components/ui/button";
import {
  DeleteButton,
  EmptyState,
  ErrorState,
  LoadingState,
  Page,
  ResourceCard,
  ResourceCardGrid,
  ResourceMeta,
  Status,
} from "@/shared/components";
import { useApiMutation, useBootstrap } from "@/shared/hooks";

export function ProvidersListPage() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname !== "/providers") {
    return <Outlet />;
  }
  const { data, error, isLoading } = useBootstrap();
  const remove = useApiMutation((id: string) => deleteProvider(id));
  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  const providers = data?.providers ?? [];
  return (
    <Page title="Providers" sub="Provider configs, credential status, and model card catalogs." actions={<Button asChild><Link to="/providers/$id" params={{ id: "new" }}>New Provider</Link></Button>}>
      {!providers.length ? <EmptyState>No providers configured.</EmptyState> : (
        <ResourceCardGrid>
          {providers.map((provider) => (
            <ResourceCard
              key={provider.id}
              variant="provider"
              label={provider.id}
              title={<Link className="font-medium underline-offset-4 hover:underline" to="/providers/$id" params={{ id: provider.id }}>{provider.name || provider.id}</Link>}
              subtitle={provider.protocol}
              status={<Status enabled={provider.configured} label={provider.configured ? "configured" : "needs config"} />}
              actions={
                <>
                  <Button variant="outline" size="sm" asChild>
                    <Link to="/providers/$id" params={{ id: provider.id }}>Configure</Link>
                  </Button>
                  <DeleteButton onDelete={() => remove.mutate(provider.id)} />
                </>
              }
            >
              <ResourceMeta
                items={[
                  { label: "Configured", value: `${provider.configured_model_count}/${provider.model_count}`, tone: provider.configured_model_count ? "ok" : "muted" },
                  { label: "Models", value: provider.model_count || "none", tone: provider.model_count ? "ok" : "muted" },
                  { label: "Protocol", value: provider.protocol },
                  { label: "Health", value: provider.last_error ? "error" : "ready", tone: provider.last_error ? "warning" : "ok" },
                ]}
              />
              {provider.last_error && <pre className="resource-preview">{String(provider.last_error)}</pre>}
            </ResourceCard>
          ))}
        </ResourceCardGrid>
      )}
    </Page>
  );
}

import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import {
  useCreateResource,
  useIntegrationKinds,
  useResourceList,
} from "@/hooks/use-resources";
import type { IntegrationKind, IntegrationResource } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const Route = createFileRoute("/integrations")({
  component: IntegrationsPage,
});

function IntegrationsPage() {
  const navigate = useNavigate();
  const { data: kinds = [], isLoading: kindsLoading } = useIntegrationKinds();
  const { data: integrations = [], isLoading: intLoading } =
    useResourceList<IntegrationResource>("integrations");
  const createMutation = useCreateResource<IntegrationResource>("integrations");

  const isLoading = kindsLoading || intLoading;
  const createError = createMutation.error;

  if (isLoading) return <PageShell>Loading integrations...</PageShell>;

  const handleCreate = (kindName: string) => {
    createMutation.mutate(
      {
        id: suggestedId(kindName, integrations),
        name: kindName,
        enabled: false,
        config: {},
      },
      {
        onSuccess: (created) => {
          navigate({
            to: "/integrations/$id",
            params: { id: created.id },
          });
        },
      },
    );
  };

  return (
    <PageShell>
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Integrations</h2>
            <p className="text-sm text-muted-foreground">
              Configured integration records and their runtime state
            </p>
          </div>
          <AddIntegrationMenu
            kinds={kinds}
            isPending={createMutation.isPending}
            onCreate={handleCreate}
          />
        </div>

        {createError ? (
          <p className="text-sm text-destructive">
            {createError instanceof Error ? createError.message : "Create failed"}
          </p>
        ) : null}

        {integrations.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {integrations.map((integration) => (
              <IntegrationCard
                key={integration.id}
                integration={integration}
                kind={kinds.find((kind) => kind.name === integration.name)}
              />
            ))}
          </div>
        ) : (
          <Empty text="No integration records configured" />
        )}

        <div>
          <h2 className="text-lg font-semibold">Available Kinds</h2>
          <p className="text-sm text-muted-foreground">
            Supported integration templates for new records
          </p>
        </div>

        {kinds && kinds.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {kinds.map((kind) => {
              return (
                <Card key={kind.name} className="flex flex-col">
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-base">{kind.name}</CardTitle>
                      <Badge variant="secondary">
                        {kind.capabilities.length} caps
                      </Badge>
                    </div>
                    <CardDescription>{kind.description}</CardDescription>
                  </CardHeader>
                  <CardContent className="flex-1 space-y-3">
                    <div className="flex flex-wrap gap-1">
                      {kind.capabilities.map((cap) => (
                        <Badge
                          key={cap.id}
                          variant="outline"
                          className="text-xs"
                        >
                          {cap.id}
                        </Badge>
                      ))}
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      onClick={() => handleCreate(kind.name)}
                      disabled={createMutation.isPending}
                    >
                      <Plus className="size-4" />
                      Add integration
                    </Button>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        ) : (
          <Empty text="No integration kinds available" />
        )}
      </div>
    </PageShell>
  );
}

function AddIntegrationMenu({
  kinds,
  isPending,
  onCreate,
}: {
  kinds: IntegrationKind[];
  isPending: boolean;
  onCreate: (kindName: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {kinds.map((kind) => (
        <Button
          key={kind.name}
          variant="outline"
          size="sm"
          onClick={() => onCreate(kind.name)}
          disabled={isPending}
        >
          <Plus className="size-4" />
          {kind.name}
        </Button>
      ))}
    </div>
  );
}

function IntegrationCard({
  integration,
  kind,
}: {
  integration: IntegrationResource;
  kind?: IntegrationKind;
}) {
  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base">{integration.id}</CardTitle>
          <Badge variant={integration.enabled ? "default" : "secondary"}>
            {integration.enabled ? "enabled" : "disabled"}
          </Badge>
        </div>
        <CardDescription>{kind?.description ?? integration.name}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <div className="flex flex-wrap gap-1">
          <Badge variant="outline" className="text-xs">
            {integration.name}
          </Badge>
          {kind ? (
            <Badge variant="secondary" className="text-xs">
              {kind.capabilities.length} caps
            </Badge>
          ) : null}
        </div>
        <Link
          to="/integrations/$id"
          params={{ id: integration.id }}
          className="mt-auto block"
        >
          <Button variant="outline" size="sm" className="w-full">
            Configure
          </Button>
        </Link>
      </CardContent>
    </Card>
  );
}

function suggestedId(
  kindName: string,
  integrations: IntegrationResource[],
): string {
  const existingIds = new Set(integrations.map((integration) => integration.id));
  if (!existingIds.has(kindName)) return kindName;

  let suffix = 2;
  while (existingIds.has(`${kindName}-${suffix}`)) {
    suffix += 1;
  }
  return `${kindName}-${suffix}`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="p-6">{children}</div>;
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
      <p className="text-sm">{text}</p>
    </div>
  );
}

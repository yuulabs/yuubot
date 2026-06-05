import { createFileRoute, Link } from "@tanstack/react-router";
import { useResourceList, useIntegrationKinds } from "@/hooks/use-resources";
import type { IntegrationResource } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const Route = createFileRoute("/integrations")({
  component: IntegrationsPage,
});

function IntegrationsPage() {
  const { data: kinds = [], isLoading: kindsLoading } = useIntegrationKinds();
  const { data: integrations = [], isLoading: intLoading } =
    useResourceList<IntegrationResource>("integrations");

  const isLoading = kindsLoading || intLoading;

  if (isLoading) return <PageShell>Loading integrations...</PageShell>;

  return (
    <PageShell>
      <div className="space-y-6">
        <div>
          <h2 className="text-lg font-semibold">Available Kinds</h2>
          <p className="text-sm text-muted-foreground">
            Integration types available in the system
          </p>
        </div>

        {kinds && kinds.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {kinds.map((kind) => {
              const integration = integrations.find(
                (i) => i.name === kind.name,
              );
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
                    {integration ? (
                      <Link
                        to="/integrations/$id"
                        params={{ id: integration.id }}
                        className="block"
                      >
                        <Button variant="outline" size="sm" className="w-full">
                          Configure
                        </Button>
                      </Link>
                    ) : (
                      <Button variant="secondary" size="sm" className="w-full" disabled>
                        Not installed
                      </Button>
                    )}
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

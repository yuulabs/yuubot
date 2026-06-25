import {
  createFileRoute,
  Link,
  Outlet,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { Plus } from "lucide-react";
import {
  useCreateResource,
  useIntegrationKinds,
  useResourceList,
} from "@/hooks/use-resources";
import type { IntegrationKind, IntegrationResource } from "@/types/api";
import { Button } from "@/components/ui/button";
import {
  PageShell,
  LegendCard,
  CrudHeader,
  Empty,
} from "@/components/baseline";

export const Route = createFileRoute("/integrations")({
  component: IntegrationsPage,
});

function IntegrationsPage() {
  const navigate = useNavigate();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const { data: kinds = [], isLoading: kindsLoading } = useIntegrationKinds();
  const { data: integrations = [], isLoading: intLoading } =
    useResourceList<IntegrationResource>("integrations");
  const createMutation = useCreateResource<IntegrationResource>("integrations");

  const isLoading = kindsLoading || intLoading;
  const createError = createMutation.error;

  if (pathname !== "/integrations") {
    return <Outlet />;
  }

  if (isLoading) {
    return (
      <PageShell title="Integrations">
        <Empty title="加载中…" description="正在读取集成记录。" />
      </PageShell>
    );
  }

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
    <PageShell
      title="Integrations"
      sub="已配置的集成记录与运行时状态。从可用模板创建新集成以接入外部能力。"
    >
      <div className="view space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <CrudHeader title="Configured Integrations" count={integrations.length} />
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
          <Empty
            title="No integration records configured"
            description="从下方可用模板创建一个集成开始。"
          />
        )}

        <CrudHeader title="Available Kinds" count={kinds.length} />

        {kinds && kinds.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {kinds.map((kind) => {
              return (
                <LegendCard
                  key={kind.name}
                  as="div"
                  dotColor="slate"
                  legend={kind.name}
                  lead={kind.description}
                >
                  <div className="flex flex-1 flex-col gap-3">
                    <div className="flex flex-wrap gap-1">
                      {kind.capabilities.map((cap) => (
                        <span
                          key={cap.id}
                          className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium border bg-muted/40"
                        >
                          {cap.id}
                        </span>
                      ))}
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {kind.capabilities.length} capabilities
                    </span>
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
                  </div>
                </LegendCard>
              );
            })}
          </div>
        ) : (
          <Empty title="No integration kinds available" />
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
  const dotColor = integration.enabled ? "green" : "slate";
  return (
    <LegendCard
      as="div"
      dotColor={dotColor}
      legend={integration.id}
      lead={kind?.description ?? integration.name}
    >
      <div className="flex flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium border bg-muted/40">
            {integration.name}
          </span>
          {kind ? (
            <span className="text-xs text-muted-foreground">
              {kind.capabilities.length} caps
            </span>
          ) : null}
        </div>
        <Button variant="outline" size="sm" className="mt-auto w-full" asChild>
          <Link to="/integrations/$id" params={{ id: integration.id }}>
            Configure
          </Link>
        </Button>
      </div>
    </LegendCard>
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

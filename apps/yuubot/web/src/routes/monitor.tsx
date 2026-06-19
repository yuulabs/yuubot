import { createFileRoute } from "@tanstack/react-router";
import { Activity, CircleDot, DollarSign, FileText } from "lucide-react";
import { useHealth, useResourceList } from "@/hooks/use-resources";
import type { ActorResource, LLMBackendResource, ActorIngressRuleResource } from "@/types/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const Route = createFileRoute("/monitor")({
  component: MonitorPage,
});

function MonitorPage() {
  const { data: health } = useHealth();
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: rules = [] } = useResourceList<ActorIngressRuleResource>("ingress-rules");

  return (
    <div className="space-y-6 p-6">
      {/* Stats */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          icon={Activity}
          label="Active Actors"
          value={actors.filter((a) => a.enabled).length}
          sub={`of ${actors.length} total`}
        />
        <StatCard
          icon={CircleDot}
          label="Backends"
          value={backends.length}
          sub="LLM providers"
        />
        <StatCard
          icon={FileText}
          label="Ingress Rules"
          value={rules.length}
          sub="routing bindings"
        />
        <StatCard
          icon={DollarSign}
          label="Health"
          value={health?.status === "ok" ? "OK" : "N/A"}
          sub="system status"
        />
      </div>

      {/* System health */}
      <Card>
        <CardHeader>
          <CardTitle>System Health</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <HealthItem label="Admin" value={health?.admin ?? "unknown"} />
            <HealthItem label="Daemon" value={health?.daemon ?? "unknown"} />
            <HealthItem label="Ingress Rules" value={String(health?.ingress_rules ?? 0)} />
            <HealthItem label="Plugins" value={String(health?.plugins ?? 0)} />
          </div>
        </CardContent>
      </Card>

      {/* Placeholders */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Traces</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Trace data will appear here when the trace service is connected.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Cost Dashboard</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Cost analytics will appear here when pricing data is available.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: React.ComponentType<{ className?: string; size?: number }>;
  label: string;
  value: string | number;
  sub: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 pt-6">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <Icon className="size-5 text-primary" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <p className="text-2xl font-bold">{value}</p>
          <p className="text-xs text-muted-foreground">{sub}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function HealthItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

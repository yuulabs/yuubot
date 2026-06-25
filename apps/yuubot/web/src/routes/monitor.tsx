import { createFileRoute } from "@tanstack/react-router";
import { Activity, CircleDot, DollarSign, FileText } from "lucide-react";
import { useHealth, useResourceList } from "@/hooks/use-resources";
import type { ActorResource, LLMBackendResource, ActorIngressRuleResource } from "@/types/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CostDashboard } from "@/components/cost-dashboard";
import { PageShell } from "@/components/baseline";

export const Route = createFileRoute("/monitor")({
  component: MonitorPage,
});

function MonitorPage() {
  const { data: health } = useHealth();
  const { data: actors = [] } = useResourceList<ActorResource>("actors");
  const { data: backends = [] } = useResourceList<LLMBackendResource>("llm-backends");
  const { data: rules = [] } = useResourceList<ActorIngressRuleResource>("ingress-rules");

  return (
    <PageShell title="Traces" sub="运行时观测：Actor / Backend / 路由计数、系统健康与成本分析。">
      <div className="view space-y-6">
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

        {/* Cost & usage analytics dashboard (replaces placeholder Cards).
            All four /monitor/trace/api/usage/* endpoints are queried by the
            CostDashboard hooks; switching the range selector re-fires every
            query. Phase breakdown shows "N/A for this range" for year/total. */}
        <CostDashboard />
      </div>
    </PageShell>
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

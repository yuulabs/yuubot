import { createFileRoute } from "@tanstack/react-router";
import type { ComponentType } from "react";
import { Activity, CircleDot, DollarSign, FileText } from "lucide-react";
import { useHealth, useResourceList } from "@/hooks/use-resources";
import type { ActorIngressRuleResource, ActorResource, LLMBackendResource } from "@/types/api";
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
      <div className="monitor-page">
        <div className="monitor-stats monitor-stats--four">
          <MonitorStat
            icon={Activity}
            label="Active Actors"
            value={actors.filter((actor) => actor.enabled).length}
            sub={`of ${actors.length} total`}
          />
          <MonitorStat
            icon={CircleDot}
            label="Backends"
            value={backends.length}
            sub="LLM providers"
          />
          <MonitorStat
            icon={FileText}
            label="Ingress Rules"
            value={rules.length}
            sub="routing bindings"
          />
          <MonitorStat
            icon={DollarSign}
            label="Health"
            value={health?.status === "ok" ? "OK" : "N/A"}
            sub="system status"
          />
        </div>

        <section className="monitor-panel">
          <div className="monitor-panel__head">
            <div>
              <h2 className="monitor-panel__title">System Health</h2>
              <p className="monitor-panel__sub">Admin process, daemon process, route bindings and plugin count.</p>
            </div>
          </div>
          <div className="monitor-health-grid">
            <HealthItem label="Admin" value={health?.admin ?? "unknown"} />
            <HealthItem label="Daemon" value={health?.daemon ?? "unknown"} />
            <HealthItem label="Ingress Rules" value={String(health?.ingress_rules ?? 0)} />
            <HealthItem label="Plugins" value={String(health?.plugins ?? 0)} />
          </div>
        </section>

        <CostDashboard />
      </div>
    </PageShell>
  );
}

function MonitorStat({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: ComponentType<{ className?: string; size?: number }>;
  label: string;
  value: string | number;
  sub: string;
}) {
  return (
    <article className="monitor-stat">
      <div className="monitor-stat__icon">
        <Icon size={18} />
      </div>
      <div className="monitor-stat__body">
        <p className="monitor-stat__label">{label}</p>
        <p className="monitor-stat__value">{value}</p>
        <p className="monitor-stat__sub">{sub}</p>
      </div>
    </article>
  );
}

function HealthItem({ label, value }: { label: string; value: string }) {
  const normalized = value.toLowerCase();
  const statusClass = normalized === "ok" ? "is-ok" : normalized === "unknown" ? "is-muted" : "is-info";

  return (
    <div className="monitor-health">
      <p className="monitor-health__label">{label}</p>
      <p className={`monitor-health__status ${statusClass}`}>{value}</p>
    </div>
  );
}

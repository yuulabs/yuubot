import { useState } from "react";
import { Activity, Clock, Hash, Layers, Route } from "lucide-react";

import {
  type UsageRange,
  usePhaseBreakdown,
  useToolCallCounts,
  useUsageLatency,
  useGatewayUsageSummary,
} from "@/hooks/use-usage-analytics";
import { formatMs, formatNumber } from "./formatters";
import { UsageTile } from "./primitives";
import { UsageRangeSelector } from "./range-selector";
import { PhaseBreakdownChart } from "./phase-breakdown-chart";
import { ToolCallsChart } from "./tool-calls-chart";

export function UsageDashboard() {
  const [range, setRange] = useState<UsageRange>("day");
  const { data: summary, isLoading: summaryLoading } = useGatewayUsageSummary(range);
  const { data: latency, isLoading: latencyLoading } = useUsageLatency(range);
  const { data: tools, isLoading: toolsLoading } = useToolCallCounts(range);
  const { data: phases, isLoading: phasesLoading } = usePhaseBreakdown(range);

  return (
    <section className="monitor-panel monitor-panel--analytics">
      <div className="monitor-panel__head">
        <div>
          <h2 className="monitor-panel__title">Usage Dashboard</h2>
          <p className="monitor-panel__sub">Token volume, routing, fallback and latency.</p>
        </div>
        <UsageRangeSelector range={range} onChange={setRange} />
      </div>

      <div className="monitor-stats monitor-stats--five">
        <UsageTile icon={Hash} label="Requests" value={summaryLoading ? "--" : formatNumber(summary?.requests)} sub="LLM calls" />
        <UsageTile icon={Activity} label="Input Tokens" value={summaryLoading ? "--" : formatNumber(summary?.input_tokens)} sub="total input" />
        <UsageTile icon={Layers} label="Cached Input" value={summaryLoading ? "--" : formatNumber(summary?.cached_input_tokens)} sub="cache reads" />
        <UsageTile icon={Layers} label="Cache Write" value={summaryLoading ? "--" : formatNumber(summary?.cache_write_tokens)} sub="cache writes" />
        <UsageTile icon={Activity} label="Output Tokens" value={summaryLoading ? "--" : formatNumber(summary?.output_tokens)} sub="generated" />
      </div>

      <div className="monitor-stats monitor-stats--four">
        <UsageTile icon={Clock} label="Gateway Latency" value={summaryLoading ? "--" : formatMs(summary?.avg_gateway_latency_ms)} sub="average request" />
        <UsageTile icon={Route} label="Fallbacks" value={summaryLoading ? "--" : formatNumber(summary?.fallback_requests)} sub="multi-target requests" />
        <UsageTile icon={Clock} label="Avg First-Token Latency" value={latencyLoading ? "--" : formatMs(latency?.avg_first_token_latency_ms)} sub="time to first token" />
        <UsageTile icon={Clock} label="Avg Turn Time" value={latencyLoading ? "--" : formatMs(latency?.avg_turn_time_ms)} sub="end-to-end turn" />
      </div>

      <div className="monitor-chart-grid">
        <Distribution title="Endpoints" items={summary?.endpoints ?? []} />
        <Distribution title="Models" items={summary?.models ?? []} />
      </div>

      <div className="monitor-chart-grid">
        <ToolCallsChart tools={tools} loading={toolsLoading} />
        <PhaseBreakdownChart phases={phases} loading={phasesLoading} />
      </div>
    </section>
  );
}

function Distribution({ title, items }: { title: string; items: Array<{ name: string; requests: number }> }) {
  return <div className="monitor-chart"><h3 className="monitor-chart__title">{title}</h3>{items.length ? <div className="grid gap-2">{items.slice(0, 8).map((item) => <div key={item.name} className="flex justify-between gap-3 text-sm"><span className="truncate font-mono text-xs">{item.name}</span><strong className="tabular-nums">{item.requests}</strong></div>)}</div> : <p className="text-sm text-muted-foreground">No requests.</p>}</div>;
}

export default UsageDashboard;

import { useState } from "react";
import { Activity, Clock, DollarSign, Hash, Layers } from "lucide-react";

import {
  type UsageRange,
  usePhaseBreakdown,
  useToolCallCounts,
  useUsageLatency,
  useUsageSummary,
} from "@/hooks/use-usage-analytics";
import { formatCost, formatMs, formatNumber } from "./formatters";
import { UsageTile } from "./primitives";
import { UsageRangeSelector } from "./range-selector";
import { PhaseBreakdownChart } from "./phase-breakdown-chart";
import { ToolCallsChart } from "./tool-calls-chart";

export function CostDashboard() {
  const [range, setRange] = useState<UsageRange>("day");
  const { data: summary, isLoading: summaryLoading } = useUsageSummary(range);
  const { data: latency, isLoading: latencyLoading } = useUsageLatency(range);
  const { data: tools, isLoading: toolsLoading } = useToolCallCounts(range);
  const { data: phases, isLoading: phasesLoading } = usePhaseBreakdown(range);

  return (
    <section className="monitor-panel monitor-panel--analytics">
      <div className="monitor-panel__head">
        <div>
          <h2 className="monitor-panel__title">Cost &amp; Usage Analytics</h2>
          <p className="monitor-panel__sub">Spend, token volume, latency and trace phase distribution.</p>
        </div>
        <UsageRangeSelector range={range} onChange={setRange} />
      </div>

      <div className="monitor-stats monitor-stats--five">
        <UsageTile icon={DollarSign} label="Cost" value={summaryLoading ? "--" : formatCost(summary?.cost)} sub="USD spent" />
        <UsageTile icon={Hash} label="Requests" value={summaryLoading ? "--" : formatNumber(summary?.requests)} sub="LLM calls" />
        <UsageTile icon={Activity} label="Input Tokens" value={summaryLoading ? "--" : formatNumber(summary?.input_tokens_uncached)} sub="uncached" />
        <UsageTile icon={Layers} label="Cached Input" value={summaryLoading ? "--" : formatNumber(summary?.cached_input_tokens)} sub="cache reads" />
        <UsageTile icon={Activity} label="Output Tokens" value={summaryLoading ? "--" : formatNumber(summary?.output_tokens)} sub="generated" />
      </div>

      <div className="monitor-stats monitor-stats--two">
        <UsageTile icon={Clock} label="Avg First-Token Latency" value={latencyLoading ? "--" : formatMs(latency?.avg_first_token_latency_ms)} sub="time to first token" />
        <UsageTile icon={Clock} label="Avg Turn Time" value={latencyLoading ? "--" : formatMs(latency?.avg_turn_time_ms)} sub="end-to-end turn" />
      </div>

      <div className="monitor-chart-grid">
        <ToolCallsChart tools={tools} loading={toolsLoading} />
        <PhaseBreakdownChart phases={phases} loading={phasesLoading} />
      </div>
    </section>
  );
}

export default CostDashboard;

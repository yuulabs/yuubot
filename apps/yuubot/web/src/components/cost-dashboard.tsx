/** Cost analytics dashboard.
 *
 * Renders a 5-range time selector, stat cards (cost / requests / tokens /
 * cached / output), latency stats, and two recharts pie charts:
 *
 *   - Tool-call counts by `tool_name`
 *   - Per-turn phase breakdown (thinking / text / tool_call / tool_execution)
 *
 * The phase breakdown endpoint (`/monitor/trace/api/usage/phases`) returns
 * HTTP 400 for `range=year` / `range=total` because the per-turn pairing
 * query is too expensive at those scales. The hook swallows that 400 and
 * yields `null`, and this component renders "N/A for this range" in its place.
 *
 * All four endpoints are re-fetched when the range selector changes (TanStack
 * Query derives a unique queryKey per range).
 */

import { useState } from "react";
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { Activity, Clock, DollarSign, Hash, Layers } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  type UsageRange,
  usePhaseBreakdown,
  useToolCallCounts,
  useUsageLatency,
  useUsageSummary,
} from "@/hooks/use-usage-analytics";

const RANGES: UsageRange[] = ["day", "week", "month", "year", "total"];
const RANGE_LABELS: Record<UsageRange, string> = {
  day: "Day",
  week: "Week",
  month: "Month",
  year: "Year",
  total: "Total",
};

const TOOL_COLORS = [
  "#2563eb", "#16a34a", "#d97706", "#9333ea",
  "#dc2626", "#0891b2", "#65a30d", "#db2777",
];

const PHASE_COLORS: Record<string, string> = {
  Thinking: "#9333ea",
  Text: "#2563eb",
  "Tool Call (args)": "#d97706",
  "Tool Execution": "#16a34a",
};

function formatMs(ms: number | undefined): string {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return "--";
  if (ms < 1) return "0 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function formatNumber(n: number | undefined): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "--";
  return n.toLocaleString();
}

function formatCost(usd: number | undefined): string {
  if (usd === undefined || usd === null || Number.isNaN(usd)) return "--";
  return `$${usd.toFixed(3)}`;
}

function StatTile({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: React.ComponentType<{ className?: string; size?: number }>;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 pt-6">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <Icon className="size-5 text-primary" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <p className="text-2xl font-bold tabular-nums">{value}</p>
          {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

function EmptyPieChart({ message }: { message: string }) {
  return (
    <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
      {message}
    </div>
  );
}

export function CostDashboard() {
  const [range, setRange] = useState<UsageRange>("day");
  const { data: summary, isLoading: summaryLoading } = useUsageSummary(range);
  const { data: latency, isLoading: latencyLoading } = useUsageLatency(range);
  const { data: tools, isLoading: toolsLoading } = useToolCallCounts(range);
  const { data: phases, isLoading: phasesLoading } = usePhaseBreakdown(range);

  const phasePieData = phases
    ? [
      { name: "Thinking", value: phases.thinking_time_ms },
      { name: "Text", value: phases.text_time_ms },
      { name: "Tool Call (args)", value: phases.tool_call_time_ms },
      { name: "Tool Execution", value: phases.tool_execution_time_ms },
    ].filter((slice) => slice.value > 0)
    : [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 className="text-lg font-semibold">Cost &amp; Usage Analytics</h2>
        <Tabs value={range} onValueChange={(v) => setRange(v as UsageRange)}>
          <TabsList>
            {RANGES.map((r) => (
              <TabsTrigger key={r} value={r}>
                {RANGE_LABELS[r]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {/* Primary stat row */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile
          icon={DollarSign}
          label="Cost"
          value={summaryLoading ? "--" : formatCost(summary?.cost)}
          sub="USD spent"
        />
        <StatTile
          icon={Hash}
          label="Requests"
          value={summaryLoading ? "--" : formatNumber(summary?.requests)}
          sub="LLM calls"
        />
        <StatTile
          icon={Activity}
          label="Input Tokens"
          value={summaryLoading ? "--" : formatNumber(summary?.input_tokens_uncached)}
          sub="uncached"
        />
        <StatTile
          icon={Layers}
          label="Cached Input"
          value={summaryLoading ? "--" : formatNumber(summary?.cached_input_tokens)}
          sub="cache reads"
        />
        <StatTile
          icon={Activity}
          label="Output Tokens"
          value={summaryLoading ? "--" : formatNumber(summary?.output_tokens)}
          sub="generated"
        />
      </div>

      {/* Latency row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <StatTile
          icon={Clock}
          label="Avg First-Token Latency"
          value={latencyLoading ? "--" : formatMs(latency?.avg_first_token_latency_ms)}
          sub="time to first token"
        />
        <StatTile
          icon={Clock}
          label="Avg Turn Time"
          value={latencyLoading ? "--" : formatMs(latency?.avg_turn_time_ms)}
          sub="end-to-end turn"
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Tool Call Counts</CardTitle>
          </CardHeader>
          <CardContent>
            {toolsLoading ? (
              <EmptyPieChart message="Loading…" />
            ) : !tools || tools.length === 0 ? (
              <EmptyPieChart message="No tool calls in this range." />
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie
                    data={tools}
                    dataKey="count"
                    nameKey="tool_name"
                    cx="50%"
                    cy="50%"
                    outerRadius={90}
                    label={(entry) => String(entry.name ?? "")}
                  >
                    {tools.map((entry, idx) => (
                      <Cell
                        key={entry.tool_name}
                        fill={TOOL_COLORS[idx % TOOL_COLORS.length]}
                      />
                    ))}
                  </Pie>
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Phase Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            {phasesLoading ? (
              <EmptyPieChart message="Loading…" />
            ) : phases === null ? (
              <EmptyPieChart message="N/A for this range" />
            ) : phasePieData.length === 0 ? (
              <EmptyPieChart message="No phase data in this range." />
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie
                    data={phasePieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    outerRadius={90}
                    label={(entry) => entry.name ?? ""}
                  >
                    {phasePieData.map((entry) => (
                      <Cell
                        key={entry.name}
                        fill={PHASE_COLORS[entry.name] ?? "#64748b"}
                      />
                    ))}
                  </Pie>
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

    </div>
  );
}

export default CostDashboard;

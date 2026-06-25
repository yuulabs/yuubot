/** Cost analytics dashboard.
 *
 * Renders usage range controls, cost/token stat tiles, latency stats, and
 * two Recharts pie charts backed by `/monitor/trace/api/usage/*`.
 */

import { useState, type ComponentType } from "react";
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { Activity, Clock, DollarSign, Hash, Layers } from "lucide-react";
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
  "var(--cyan)",
  "var(--green)",
  "var(--yellow-deep)",
  "var(--rose)",
  "var(--red)",
  "var(--ink-2)",
  "var(--amber)",
  "var(--slate)",
];

const PHASE_COLORS: Record<string, string> = {
  Thinking: "var(--rose)",
  Text: "var(--cyan)",
  "Tool Call (args)": "var(--yellow-deep)",
  "Tool Execution": "var(--green)",
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

function UsageTile({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: ComponentType<{ className?: string; size?: number }>;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <article className="monitor-stat">
      <div className="monitor-stat__icon">
        <Icon size={18} />
      </div>
      <div className="monitor-stat__body">
        <p className="monitor-stat__label">{label}</p>
        <p className="monitor-stat__value monitor-stat__value--mono">{value}</p>
        {sub && <p className="monitor-stat__sub">{sub}</p>}
      </div>
    </article>
  );
}

function ChartEmpty({ message }: { message: string }) {
  return (
    <div className="monitor-chart-empty">
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
    <section className="monitor-panel monitor-panel--analytics">
      <div className="monitor-panel__head">
        <div>
          <h2 className="monitor-panel__title">Cost &amp; Usage Analytics</h2>
          <p className="monitor-panel__sub">Spend, token volume, latency and trace phase distribution.</p>
        </div>
        <div className="seg monitor-range" role="tablist" aria-label="Usage range">
          {RANGES.map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={range === item}
              className={`seg__btn ${range === item ? "is-active" : ""}`}
              onClick={() => setRange(item)}
            >
              {RANGE_LABELS[item]}
            </button>
          ))}
        </div>
      </div>

      <div className="monitor-stats monitor-stats--five">
        <UsageTile
          icon={DollarSign}
          label="Cost"
          value={summaryLoading ? "--" : formatCost(summary?.cost)}
          sub="USD spent"
        />
        <UsageTile
          icon={Hash}
          label="Requests"
          value={summaryLoading ? "--" : formatNumber(summary?.requests)}
          sub="LLM calls"
        />
        <UsageTile
          icon={Activity}
          label="Input Tokens"
          value={summaryLoading ? "--" : formatNumber(summary?.input_tokens_uncached)}
          sub="uncached"
        />
        <UsageTile
          icon={Layers}
          label="Cached Input"
          value={summaryLoading ? "--" : formatNumber(summary?.cached_input_tokens)}
          sub="cache reads"
        />
        <UsageTile
          icon={Activity}
          label="Output Tokens"
          value={summaryLoading ? "--" : formatNumber(summary?.output_tokens)}
          sub="generated"
        />
      </div>

      <div className="monitor-stats monitor-stats--two">
        <UsageTile
          icon={Clock}
          label="Avg First-Token Latency"
          value={latencyLoading ? "--" : formatMs(latency?.avg_first_token_latency_ms)}
          sub="time to first token"
        />
        <UsageTile
          icon={Clock}
          label="Avg Turn Time"
          value={latencyLoading ? "--" : formatMs(latency?.avg_turn_time_ms)}
          sub="end-to-end turn"
        />
      </div>

      <div className="monitor-chart-grid">
        <section className="monitor-chart-card">
          <div className="monitor-chart-card__head">
            <h3>Tool Call Counts</h3>
          </div>
          {toolsLoading ? (
            <ChartEmpty message="Loading..." />
          ) : !tools || tools.length === 0 ? (
            <ChartEmpty message="No tool calls in this range." />
          ) : (
            <div className="monitor-chart">
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
                  <Tooltip wrapperClassName="monitor-chart-tooltip" />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>

        <section className="monitor-chart-card">
          <div className="monitor-chart-card__head">
            <h3>Phase Breakdown</h3>
          </div>
          {phasesLoading ? (
            <ChartEmpty message="Loading..." />
          ) : phases === null ? (
            <ChartEmpty message="N/A for this range" />
          ) : phasePieData.length === 0 ? (
            <ChartEmpty message="No phase data in this range." />
          ) : (
            <div className="monitor-chart">
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
                        fill={PHASE_COLORS[entry.name] ?? "var(--text-3)"}
                      />
                    ))}
                  </Pie>
                  <Tooltip wrapperClassName="monitor-chart-tooltip" />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

export default CostDashboard;

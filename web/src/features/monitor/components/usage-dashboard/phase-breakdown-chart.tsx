import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import type { PhaseBreakdown } from "@/hooks/use-usage-analytics";
import { PHASE_COLORS } from "./constants";
import { ChartEmpty } from "./primitives";

export function PhaseBreakdownChart({
  phases,
  loading,
}: {
  phases: PhaseBreakdown | null | undefined;
  loading: boolean;
}) {
  const phasePieData = phases
    ? [
      { name: "Thinking", value: phases.thinking_time_ms },
      { name: "Text", value: phases.text_time_ms },
      { name: "Tool Call (args)", value: phases.tool_call_time_ms },
      { name: "Tool Execution", value: phases.tool_execution_time_ms },
    ].filter((slice) => slice.value > 0)
    : [];

  return (
    <section className="monitor-chart-card">
      <div className="monitor-chart-card__head">
        <h3>Phase Breakdown</h3>
      </div>
      {loading ? (
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
  );
}

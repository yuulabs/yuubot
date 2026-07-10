import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import type { ToolCallCount } from "@/hooks/use-usage-analytics";
import { TOOL_COLORS } from "./constants";
import { ChartEmpty } from "./primitives";

export function ToolCallsChart({
  tools,
  loading,
}: {
  tools: ToolCallCount[] | undefined;
  loading: boolean;
}) {
  return (
    <section className="monitor-chart-card">
      <div className="monitor-chart-card__head">
        <h3>Tool Call Counts</h3>
      </div>
      {loading ? (
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
  );
}

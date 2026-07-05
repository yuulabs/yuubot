import type { UsageRange } from "@/hooks/use-usage-analytics";

export const USAGE_RANGES: UsageRange[] = ["day", "week", "month", "year", "total"];

export const RANGE_LABELS: Record<UsageRange, string> = {
  day: "Day",
  week: "Week",
  month: "Month",
  year: "Year",
  total: "Total",
};

export const TOOL_COLORS = [
  "var(--cyan)",
  "var(--green)",
  "var(--yellow-deep)",
  "var(--rose)",
  "var(--red)",
  "var(--ink-2)",
  "var(--amber)",
  "var(--slate)",
];

export const PHASE_COLORS: Record<string, string> = {
  Thinking: "var(--rose)",
  Text: "var(--cyan)",
  "Tool Call (args)": "var(--yellow-deep)",
  "Tool Execution": "var(--green)",
};

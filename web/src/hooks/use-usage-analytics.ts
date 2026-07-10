/** TanStack Query hooks for the yuutrace usage-analytics API.
 *
 * The four endpoints are mounted at `/monitor/trace/api/usage/*` by the
 * admin process (aduult app mounts yuutrace's `_build_app`). The phases
 * endpoint returns 400 for `range=year` / `range=total`; that case is
 * treated as "no phase data for this range" (`null`) rather than an error.
 */

import { useQuery } from "@tanstack/react-query";

export type UsageRange = "day" | "week" | "month" | "year" | "total";

export interface GatewayUsageSummary {
  requests: number;
  input_tokens: number;
  cached_input_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  avg_gateway_latency_ms: number;
  fallback_requests: number;
  endpoints: Array<{ name: string; requests: number }>;
  models: Array<{ name: string; requests: number }>;
}

export interface UsageLatency {
  avg_first_token_latency_ms: number;
  avg_turn_time_ms: number;
  avg_tool_execution_time_ms: number;
  tool_execution_samples: number;
}

export interface ToolCallCount {
  tool_name: string;
  count: number;
}

export interface PhaseBreakdown {
  thinking_time_ms: number;
  text_time_ms: number;
  tool_call_time_ms: number;
  tool_execution_time_ms: number;
}

const USAGE_BASE = "/monitor/trace/api/usage";

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

/** Fetch summary; phases endpoint may 400 for year/total — caller handles null. */
async function fetchJsonOrNull<T>(url: string): Promise<T | null> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    // Phase breakdown intentionally returns 400 for year/total — treat as "no data".
    if (response.status === 400) return null;
    throw new Error(`HTTP ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export const usageKeys = {
  all: (range: UsageRange) => ["usage", range] as const,
  summary: (range: UsageRange) => ["usage", "summary", range] as const,
  latency: (range: UsageRange) => ["usage", "latency", range] as const,
  tools: (range: UsageRange) => ["usage", "tools", range] as const,
  phases: (range: UsageRange) => ["usage", "phases", range] as const,
};

export function useGatewayUsageSummary(range: UsageRange) {
  return useQuery({
    queryKey: usageKeys.summary(range),
    queryFn: () => fetchJson<GatewayUsageSummary>(`/api/usage?range=${range}`),
  });
}

export function useUsageLatency(range: UsageRange) {
  return useQuery({
    queryKey: usageKeys.latency(range),
    queryFn: () => fetchJson<UsageLatency>(`${USAGE_BASE}/latency?range=${range}`),
  });
}

export function useToolCallCounts(range: UsageRange) {
  return useQuery({
    queryKey: usageKeys.tools(range),
    queryFn: () => fetchJson<ToolCallCount[]>(`${USAGE_BASE}/tools?range=${range}`),
  });
}

/** Phase breakdown is only available for day/week/month. year/total → 400.
 * Hooks swallow that 400 and yield `null` so the dashboard can render
 * "N/A for this range" rather than surfacing an error. */
export function usePhaseBreakdown(range: UsageRange) {
  return useQuery({
    queryKey: usageKeys.phases(range),
    queryFn: () => fetchJsonOrNull<PhaseBreakdown>(`${USAGE_BASE}/phases?range=${range}`),
    retry: false,
  });
}

export function formatMs(ms: number | undefined): string {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return "--";
  if (ms < 1) return "0 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatNumber(n: number | undefined): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "--";
  return n.toLocaleString();
}

export function formatCost(usd: number | undefined): string {
  if (usd === undefined || usd === null || Number.isNaN(usd)) return "--";
  return `$${usd.toFixed(3)}`;
}

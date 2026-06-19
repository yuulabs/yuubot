import type { LlmUsageEvent } from "../types";

export interface UsageSummaryProps {
  usages: LlmUsageEvent[];
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

interface ModelUsage {
  model: string;
  provider: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  requests: number;
}

/**
 * Usage summary panel — groups token usage by model.
 */
export function UsageSummary({ usages }: UsageSummaryProps) {
  if (usages.length === 0) {
    return (
      <div style={styles.container}>
        <h3 style={styles.title}>Usage</h3>
        <div style={styles.empty}>No usage data</div>
      </div>
    );
  }

  // Aggregate by model
  const byModel = new Map<string, ModelUsage>();
  for (const u of usages) {
    const key = `${u.provider}/${u.model}`;
    const existing = byModel.get(key);
    if (existing) {
      existing.inputTokens += u.inputTokens;
      existing.outputTokens += u.outputTokens;
      existing.cacheReadTokens += u.cacheReadTokens;
      existing.cacheWriteTokens += u.cacheWriteTokens;
      existing.requests += 1;
    } else {
      byModel.set(key, {
        model: u.model,
        provider: u.provider,
        inputTokens: u.inputTokens,
        outputTokens: u.outputTokens,
        cacheReadTokens: u.cacheReadTokens,
        cacheWriteTokens: u.cacheWriteTokens,
        requests: 1,
      });
    }
  }

  const totalInput = usages.reduce((s, u) => s + u.inputTokens, 0);
  const totalOutput = usages.reduce((s, u) => s + u.outputTokens, 0);

  return (
    <div style={styles.container}>
      <h3 style={styles.title}>Usage</h3>

      <div style={styles.totalRow}>
        <div style={styles.totalItem}>
          <span style={styles.totalLabel}>Input</span>
          <span style={styles.totalValue}>{formatTokens(totalInput)}</span>
        </div>
        <div style={styles.totalItem}>
          <span style={styles.totalLabel}>Output</span>
          <span style={styles.totalValue}>{formatTokens(totalOutput)}</span>
        </div>
        <div style={styles.totalItem}>
          <span style={styles.totalLabel}>Requests</span>
          <span style={styles.totalValue}>{usages.length}</span>
        </div>
      </div>

      <div style={styles.divider} />

      {Array.from(byModel.values()).map((m) => (
        <div key={`${m.provider}/${m.model}`} style={styles.modelBlock}>
          <div style={styles.modelHeader}>
            <span style={styles.modelName}>{m.model}</span>
            <span style={styles.provider}>{m.provider}</span>
          </div>
          <div style={styles.tokenRow}>
            <span style={styles.tokenLabel}>
              in: <b>{formatTokens(m.inputTokens)}</b>
            </span>
            <span style={styles.tokenLabel}>
              out: <b>{formatTokens(m.outputTokens)}</b>
            </span>
            {m.cacheReadTokens > 0 && (
              <span style={styles.tokenLabel}>
                cache↓: <b>{formatTokens(m.cacheReadTokens)}</b>
              </span>
            )}
            <span style={styles.tokenLabel}>
              ×{m.requests}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    background: "#161b22",
    border: "1px solid #2d333b",
    borderRadius: 8,
    padding: 16,
  },
  title: {
    fontSize: 14,
    fontWeight: 600,
    color: "#e1e4e8",
    marginBottom: 12,
  },
  empty: {
    fontSize: 13,
    color: "#8b949e",
  },
  totalRow: {
    display: "flex",
    gap: 16,
  },
  totalItem: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
  },
  totalLabel: {
    fontSize: 11,
    color: "#8b949e",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  totalValue: {
    fontSize: 16,
    fontWeight: 600,
    color: "#e1e4e8",
    fontFamily: "monospace",
  },
  divider: {
    height: 1,
    background: "#2d333b",
    margin: "12px 0",
  },
  modelBlock: {
    marginBottom: 10,
  },
  modelHeader: {
    display: "flex",
    gap: 8,
    alignItems: "center",
    marginBottom: 4,
  },
  modelName: {
    fontSize: 13,
    color: "#d2a8ff",
    fontFamily: "monospace",
    fontWeight: 600,
  },
  provider: {
    fontSize: 11,
    color: "#8b949e",
  },
  tokenRow: {
    display: "flex",
    gap: 10,
    flexWrap: "wrap",
  },
  tokenLabel: {
    fontSize: 12,
    color: "#8b949e",
    fontFamily: "monospace",
  },
};

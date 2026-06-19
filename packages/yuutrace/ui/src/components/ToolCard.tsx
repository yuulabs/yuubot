import type { CostEvent, Span, ToolUsageEvent } from "../types";

export interface ToolCardProps {
  span: Span;
  usage?: ToolUsageEvent;
  cost?: CostEvent;
}

function formatToolIo(raw: unknown): string | undefined {
  if (raw == null) return undefined;

  if (typeof raw === "string") {
    const trimmed = raw.trim();
    if (!trimmed) return undefined;
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      if (typeof parsed === "string") return parsed;
      return JSON.stringify(parsed, null, 2);
    } catch {
      return raw;
    }
  }

  return JSON.stringify(raw, null, 2);
}

export function ToolCard({ span, usage, cost }: ToolCardProps) {
  const durationMs =
    (span.end_time_unix_nano - span.start_time_unix_nano) / 1_000_000;
  const toolName =
    usage?.name ??
    (span.attributes["yuu.tool.name"] as string | undefined) ??
    span.name;

  // Extract tool I/O and error from span attributes
  const rawToolOutput = span.attributes["yuu.tool.output"];
  const rawToolInput = span.attributes["yuu.tool.input"];
  const rawToolError = span.attributes["yuu.tool.error"];

  const toolInput = formatToolIo(rawToolInput);
  const toolOutput = formatToolIo(rawToolOutput);
  const toolError =
    typeof rawToolError === "string" && rawToolError.trim()
      ? rawToolError.trim()
      : undefined;

  const hasError = !!toolError;

  return (
    <div style={hasError ? styles.cardError : styles.card}>
      <div style={styles.header}>
        <div style={styles.headerLeft}>
          <span style={styles.icon}>🔧</span>
          <span style={styles.name}>{toolName}</span>
        </div>
        <div style={styles.headerRight}>
          {cost && (
            <span style={styles.cost}>${cost.amount.toFixed(4)}</span>
          )}
          <span style={styles.duration}>{durationMs.toFixed(0)}ms</span>
        </div>
      </div>

      {usage && (
        <div style={styles.body}>
          <span style={styles.usageLine}>
            {usage.quantity} {usage.unit}
          </span>
        </div>
      )}

      {/* Display tool input */}
      {toolInput && (
        <div style={styles.ioSection}>
          <div style={styles.ioLabel}>Input:</div>
          <div style={styles.ioContent}>{toolInput}</div>
        </div>
      )}

      {/* Display tool output */}
      {toolOutput && (
        <div style={styles.ioSection}>
          <div style={styles.ioLabel}>Output:</div>
          <div style={styles.ioContent}>{toolOutput}</div>
        </div>
      )}

      {toolError && (
        <div style={styles.errorSection}>
          <div style={styles.errorLabel}>Error:</div>
          <div style={styles.errorContent}>{toolError}</div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    background: "#161b22",
    border: "1px solid #2d4a2d",
    borderRadius: 8,
    padding: "12px 16px",
    borderLeft: "3px solid #3fb950",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  headerLeft: {
    display: "flex",
    alignItems: "center",
    gap: 6,
  },
  headerRight: {
    display: "flex",
    alignItems: "center",
    gap: 10,
  },
  icon: {
    fontSize: 16,
  },
  name: {
    fontWeight: 600,
    fontSize: 14,
    color: "#e1e4e8",
  },
  cost: {
    fontSize: 13,
    color: "#3fb950",
    fontFamily: "monospace",
    fontWeight: 600,
  },
  duration: {
    fontSize: 12,
    color: "#8b949e",
    fontFamily: "monospace",
  },
  body: {
    marginTop: 6,
  },
  usageLine: {
    fontSize: 12,
    color: "#8b949e",
    fontFamily: "monospace",
  },
  ioSection: {
    marginTop: 8,
    paddingTop: 8,
    borderTop: "1px solid #21262d",
  },
  ioLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: "#8b949e",
    marginBottom: 4,
    textTransform: "uppercase",
  },
  ioContent: {
    fontSize: 12,
    color: "#c9d1d9",
    fontFamily: "monospace",
    background: "#0d1117",
    padding: "6px 8px",
    borderRadius: 4,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    maxHeight: "200px",
    overflowY: "auto",
  },
  cardError: {
    background: "#161b22",
    border: "1px solid #6e2d2d",
    borderRadius: 8,
    padding: "12px 16px",
    borderLeft: "3px solid #f85149",
  },
  errorSection: {
    marginTop: 8,
    paddingTop: 8,
    borderTop: "1px solid #3d1e1e",
  },
  errorLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: "#f85149",
    marginBottom: 4,
    textTransform: "uppercase",
  },
  errorContent: {
    fontSize: 12,
    color: "#ffa198",
    fontFamily: "monospace",
    background: "#1a0d0d",
    padding: "6px 8px",
    borderRadius: 4,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    maxHeight: "200px",
    overflowY: "auto",
  },
};

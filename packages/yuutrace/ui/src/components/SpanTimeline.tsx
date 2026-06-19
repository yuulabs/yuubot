import type { Span } from "../types";

export interface SpanTimelineProps {
  spans: Span[];
}

/**
 * Horizontal Gantt-chart style timeline of spans.
 */
export function SpanTimeline({ spans }: SpanTimelineProps) {
  if (spans.length === 0) return null;

  const minTime = Math.min(...spans.map((s) => s.start_time_unix_nano));
  const maxTime = Math.max(...spans.map((s) => s.end_time_unix_nano));
  const totalDuration = maxTime - minTime || 1;

  const sorted = [...spans].sort(
    (a, b) => a.start_time_unix_nano - b.start_time_unix_nano,
  );

  return (
    <div style={styles.container}>
      <h3 style={styles.title}>Timeline</h3>
      <div style={styles.chart}>
        {sorted.map((span) => {
          const left =
            ((span.start_time_unix_nano - minTime) / totalDuration) * 100;
          const width =
            ((span.end_time_unix_nano - span.start_time_unix_nano) /
              totalDuration) *
            100;
          const durationMs =
            (span.end_time_unix_nano - span.start_time_unix_nano) / 1_000_000;

          const isLlm =
            span.name === "llm_gen" || span.name.startsWith("llm");
          const isTool =
            span.name === "tools" || span.name.startsWith("tool:");
          const color = isLlm
            ? "#58a6ff"
            : isTool
              ? "#3fb950"
              : "#8b949e";

          return (
            <div key={span.span_id} style={styles.row}>
              <div style={styles.label} title={span.name}>
                {span.name}
              </div>
              <div style={styles.track}>
                <div
                  style={{
                    ...styles.bar,
                    left: `${left}%`,
                    width: `${Math.max(width, 0.5)}%`,
                    background: color,
                  }}
                  title={`${durationMs.toFixed(0)}ms`}
                />
              </div>
              <div style={styles.duration}>{durationMs.toFixed(0)}ms</div>
            </div>
          );
        })}
      </div>
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
  chart: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  row: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    height: 24,
  },
  label: {
    width: 100,
    fontSize: 12,
    color: "#8b949e",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    flexShrink: 0,
  },
  track: {
    flex: 1,
    position: "relative",
    height: 16,
    background: "#0d1117",
    borderRadius: 3,
  },
  bar: {
    position: "absolute",
    top: 0,
    height: "100%",
    borderRadius: 3,
    minWidth: 2,
    opacity: 0.85,
  },
  duration: {
    width: 60,
    fontSize: 11,
    color: "#6e7681",
    fontFamily: "monospace",
    textAlign: "right",
    flexShrink: 0,
  },
};

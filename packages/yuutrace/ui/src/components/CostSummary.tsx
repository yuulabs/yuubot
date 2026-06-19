import type { CostEvent } from "../types";

export interface CostSummaryProps {
  costs: CostEvent[];
}

/**
 * Cost summary panel — groups costs by category and shows totals.
 */
export function CostSummary({ costs }: CostSummaryProps) {
  if (costs.length === 0) {
    return (
      <div style={styles.container}>
        <h3 style={styles.title}>Cost</h3>
        <div style={styles.empty}>No cost data</div>
      </div>
    );
  }

  const total = costs.reduce((s, c) => s + c.amount, 0);
  const byCategory = new Map<string, number>();
  for (const c of costs) {
    byCategory.set(c.category, (byCategory.get(c.category) ?? 0) + c.amount);
  }

  // Group by model for LLM costs
  const byModel = new Map<string, number>();
  for (const c of costs) {
    if (c.category === "llm" && c.llmModel) {
      byModel.set(c.llmModel, (byModel.get(c.llmModel) ?? 0) + c.amount);
    }
  }

  return (
    <div style={styles.container}>
      <h3 style={styles.title}>Cost</h3>
      <div style={styles.total}>
        <span>Total</span>
        <span style={styles.amount}>${total.toFixed(4)}</span>
      </div>
      <div style={styles.divider} />

      {Array.from(byCategory.entries()).map(([cat, amount]) => (
        <div key={cat} style={styles.row}>
          <span style={styles.label}>
            {cat === "llm" ? "🤖 LLM" : "🔧 Tool"}
          </span>
          <span style={styles.value}>${amount.toFixed(4)}</span>
        </div>
      ))}

      {byModel.size > 0 && (
        <>
          <div style={styles.divider} />
          <div style={styles.sectionTitle}>By Model</div>
          {Array.from(byModel.entries()).map(([model, amount]) => (
            <div key={model} style={styles.row}>
              <span style={styles.modelLabel}>{model}</span>
              <span style={styles.value}>${amount.toFixed(4)}</span>
            </div>
          ))}
        </>
      )}
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
  total: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: 14,
    color: "#e1e4e8",
    fontWeight: 600,
  },
  amount: {
    color: "#3fb950",
    fontFamily: "monospace",
    fontSize: 16,
  },
  divider: {
    height: 1,
    background: "#2d333b",
    margin: "10px 0",
  },
  row: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "3px 0",
  },
  label: {
    fontSize: 13,
    color: "#e1e4e8",
  },
  value: {
    fontSize: 13,
    color: "#3fb950",
    fontFamily: "monospace",
  },
  sectionTitle: {
    fontSize: 12,
    color: "#8b949e",
    fontWeight: 600,
    marginBottom: 4,
  },
  modelLabel: {
    fontSize: 12,
    color: "#d2a8ff",
    fontFamily: "monospace",
  },
};

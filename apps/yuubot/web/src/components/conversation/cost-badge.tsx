/** Realtime cost badge rendered in the conversation header.
 *
 * Reads the running cumulative USD cost from a `cost_update` SSE event
 * (Phase 5-2 emits one per `llm.finished` RuntimeEvent). The daily budget
 * guard is global, not per-conversation, so no quota (`/ $limit`) is shown
 * here — only the running `$<total> spent` figure.
 *
 * When no cost frame has arrived yet (`totalCost` is `0`), the badge renders
 * a quiet placeholder so the header doesn't flash broken text.
 */

interface CostBadgeProps {
  /** Running cumulative USD spend for this conversation, from `cost_update`. */
  totalCost: number;
}

export function CostBadge({ totalCost }: CostBadgeProps) {
  return (
    <span
      className="text-sm text-muted-foreground tabular-nums"
      data-testid="conversation-cost-badge"
      title="Cumulative spend across all LLM calls in this conversation"
    >
      ${totalCost.toFixed(3)} spent
    </span>
  );
}

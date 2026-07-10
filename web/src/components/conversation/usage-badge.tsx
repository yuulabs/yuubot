export function UsageBadge({ totalTokens }: { totalTokens: number }) {
  return (
    <span className="text-sm text-muted-foreground tabular-nums" data-testid="conversation-usage-badge" title="Cumulative tokens across this conversation">
      {totalTokens.toLocaleString()} tokens
    </span>
  );
}

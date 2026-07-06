import type { ConversationPhase } from "../hooks/use-conversation-session";

export function TurnPill({ phase }: { phase: ConversationPhase }) {
  const label =
    phase === "sending" ? "Sending"
    : phase === "streaming" ? "Streaming"
    : phase === "error" ? "Error"
    : "Ready";
  const className =
    phase === "sending" ? "turn-pill turn-pill--thinking"
    : phase === "streaming" ? "turn-pill turn-pill--streaming"
    : phase === "error" ? "turn-pill turn-pill--error"
    : "turn-pill turn-pill--idle";

  return (
    <span className={className}>
      <span className="turn-pill__dot" />
      {label}
    </span>
  );
}

import { CostBadge } from "@/components/conversation/cost-badge";
import { Bug, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import type { ConversationPhase } from "../hooks/use-conversation-session";

export function ChatTopbar({
  phase,
  totalCost,
  canInterrupt,
  historyOpen,
  debugOpen,
  showDebugToggle = false,
  onToggleHistory,
  onToggleDebug,
  onInterrupt,
}: {
  phase: ConversationPhase;
  totalCost: number;
  canInterrupt: boolean;
  historyOpen: boolean;
  debugOpen: boolean;
  showDebugToggle?: boolean;
  onToggleHistory: () => void;
  onToggleDebug: () => void;
  onInterrupt: () => void;
}) {
  return (
    <div className="chat__topbar-actions">
      <TurnPill phase={phase} />
      <CostBadge totalCost={totalCost} />
      <button
        type="button"
        className="btn conv-stop-btn"
        disabled={!canInterrupt || phase === "idle"}
        onClick={onInterrupt}
      >
        Stop
      </button>
      <button
        type="button"
        className="chat__tool-btn"
        aria-pressed={historyOpen}
        onClick={onToggleHistory}
        title={historyOpen ? "Hide history" : "Show history"}
      >
        {historyOpen ? <PanelLeftClose size={15} /> : <PanelLeftOpen size={15} />}
        <span>History</span>
      </button>
      {showDebugToggle && (
        <button
          type="button"
          className="chat__tool-btn"
          aria-pressed={debugOpen}
          onClick={onToggleDebug}
          title={debugOpen ? "Hide debug" : "Show debug"}
        >
          <Bug size={15} />
          <span>Debug</span>
        </button>
      )}
    </div>
  );
}

function TurnPill({ phase }: { phase: ConversationPhase }) {
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

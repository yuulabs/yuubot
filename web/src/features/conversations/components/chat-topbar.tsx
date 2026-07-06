import { Bug, FolderOpen, PanelLeftClose, PanelLeftOpen } from "lucide-react";

export function ChatTopbar({
  actorId,
  historyOpen,
  debugOpen,
  showDebugToggle = false,
  onToggleHistory,
  onToggleDebug,
}: {
  actorId: string;
  historyOpen: boolean;
  debugOpen: boolean;
  showDebugToggle?: boolean;
  onToggleHistory: () => void;
  onToggleDebug: () => void;
}) {
  const workspaceHref = actorId
    ? `/workspace?actor=${encodeURIComponent(actorId)}`
    : "/workspace";

  return (
    <div className="chat__topbar-actions">
      <a
        href={workspaceHref}
        className="chat__tool-btn"
        target="_blank"
        rel="noreferrer"
        title="Open actor workspace"
      >
        <FolderOpen size={15} />
        <span>Workspace</span>
      </a>
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

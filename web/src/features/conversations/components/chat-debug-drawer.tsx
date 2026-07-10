export function ChatDebugDrawer({
  events,
  usageJson,
}: {
  events: string[];
  usageJson: string;
}) {
  return (
    <aside className="chat-debug-drawer" aria-label="Debug">
      <div className="chat-bind__head">
        Debug
      </div>
      <div className="chat-bind__body">
        <div className="chat-bind__line">
          <span>WebSocket events</span>
        </div>
        {!events.length ? (
          <p className="conv-item__preview">No live events.</p>
        ) : (
          events.slice(-20).map((event, index) => (
            <pre key={`${index}-${event.length}`} className="msg__code" style={{ marginBottom: "var(--sp-2)" }}>
              {event}
            </pre>
          ))
        )}
        <div className="chat-bind__line">
          <span>Usage</span>
        </div>
        <pre className="msg__code">{usageJson || "No usage records."}</pre>
      </div>
    </aside>
  );
}

import { EmptyState, Panel } from "@/shared/components";

export function WsEventsPanel({ events }: { events: string[] }) {
  return (
    <Panel>
      <h2 className="text-lg font-semibold">WebSocket Events</h2>
      {!events.length ? <EmptyState>No live events.</EmptyState> : events.map((event, index) => (
        <pre key={`${index}-${event.length}`} className="overflow-auto rounded border p-3 text-xs">{event}</pre>
      ))}
    </Panel>
  );
}

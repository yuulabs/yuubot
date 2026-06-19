import { useEffect, useRef, useState } from "react";
import type { ConversationSummary } from "../types";

export interface ConversationListProps {
  conversations: ConversationSummary[];
  selectedId?: string;
  onSelect?: (id: string) => void;
  hasMore?: boolean;
  onLoadMore?: () => void;
}

function formatTime(nanos: number): string {
  return new Date(nanos / 1_000_000).toLocaleString();
}

function formatCost(amount: number): string {
  return `$${amount.toFixed(4)}`;
}

export function ConversationList({
  conversations,
  selectedId,
  onSelect,
  hasMore,
  onLoadMore,
}: ConversationListProps) {
  const [search, setSearch] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = sentinelRef.current;
    const root = listRef.current;
    if (!el || !root || !onLoadMore) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore) onLoadMore();
      },
      { root, threshold: 0 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore, onLoadMore]);

  const filtered = conversations.filter((c) => {
    const q = search.toLowerCase();
    return (
      (c.agent ?? "").toLowerCase().includes(q) ||
      (c.id ?? "").toLowerCase().includes(q) ||
      (c.model ?? "").toLowerCase().includes(q)
    );
  });

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h2 style={styles.title}>Conversations</h2>
        <input
          type="text"
          placeholder="Search..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={styles.search}
        />
      </div>
      <div ref={listRef} style={styles.list}>
        {filtered.length === 0 && (
          <div style={styles.empty}>No conversations found</div>
        )}
        {filtered.map((c) => (
          <div
            key={c.id}
            onClick={() => onSelect?.(c.id)}
            style={{
              ...styles.item,
              ...(c.id === selectedId ? styles.itemSelected : {}),
            }}
          >
            <div style={styles.itemHeader}>
              <span style={styles.agent}>{c.agent || c.id.slice(0, 8) + "…"}</span>
              <span style={styles.cost}>{formatCost(c.total_cost)}</span>
            </div>
            <div style={styles.itemMeta}>
              {c.model && <span style={styles.model}>{c.model}</span>}
              <span style={styles.spanCount}>{c.span_count} spans</span>
            </div>
            <div style={styles.time}>{formatTime(c.start_time)}</div>
          </div>
        ))}
        {hasMore && (
          <div ref={sentinelRef} style={styles.loadMore}>
            Loading more...
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    borderRight: "1px solid #2d333b",
    background: "#161b22",
    minWidth: 280,
  },
  header: {
    padding: "16px",
    borderBottom: "1px solid #2d333b",
  },
  title: {
    fontSize: 16,
    fontWeight: 600,
    marginBottom: 8,
    color: "#e1e4e8",
  },
  search: {
    width: "100%",
    padding: "6px 10px",
    borderRadius: 6,
    border: "1px solid #3d444d",
    background: "#0d1117",
    color: "#e1e4e8",
    fontSize: 13,
    outline: "none",
  },
  list: {
    flex: 1,
    overflowY: "auto",
  },
  empty: {
    padding: 16,
    color: "#8b949e",
    textAlign: "center",
    fontSize: 13,
  },
  item: {
    padding: "10px 16px",
    borderBottom: "1px solid #21262d",
    cursor: "pointer",
    transition: "background 0.15s",
  },
  itemSelected: {
    background: "#1f2937",
    borderLeft: "3px solid #58a6ff",
  },
  itemHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 4,
  },
  agent: {
    fontWeight: 600,
    fontSize: 14,
    color: "#e1e4e8",
  },
  cost: {
    fontSize: 12,
    color: "#3fb950",
    fontFamily: "monospace",
  },
  itemMeta: {
    display: "flex",
    gap: 8,
    marginBottom: 2,
  },
  model: {
    fontSize: 12,
    color: "#8b949e",
    background: "#21262d",
    padding: "1px 6px",
    borderRadius: 4,
  },
  spanCount: {
    fontSize: 12,
    color: "#8b949e",
  },
  time: {
    fontSize: 11,
    color: "#6e7681",
  },
  loadMore: {
    padding: 12,
    color: "#8b949e",
    textAlign: "center",
    fontSize: 12,
  },
};

import { ConversationFlow } from "../components/ConversationFlow";
import { ConversationList } from "../components/ConversationList";
import { CostSummary } from "../components/CostSummary";
import { SpanTimeline } from "../components/SpanTimeline";
import { UsageSummary } from "../components/UsageSummary";
import { useTraceData } from "../hooks/useTraceData";
import { parseConversation } from "../utils/parse";

/**
 * Full trace visualization page.
 *
 * Layout: Left sidebar (ConversationList) | Center (Flow + Timeline) | Right (Cost + Usage)
 */
export function TracePage() {
  const {
    conversations,
    selectedConversation,
    loading,
    error,
    hasMore,
    selectConversation,
    loadMore,
  } = useTraceData();

  const parsed = selectedConversation
    ? parseConversation(selectedConversation.spans)
    : null;

  return (
    <div style={styles.layout}>
      {/* Left sidebar */}
      <div style={styles.sidebar}>
        <ConversationList
          conversations={conversations}
          selectedId={selectedConversation?.id}
          onSelect={selectConversation}
          hasMore={hasMore}
          onLoadMore={loadMore}
        />
      </div>

      {/* Center */}
      <div style={styles.center}>
        {loading && <div style={styles.status}>Loading...</div>}
        {error && <div style={styles.error}>Error: {error}</div>}

        {!selectedConversation && !loading && (
          <div style={styles.placeholder}>
            Select a conversation to view traces
          </div>
        )}

        {selectedConversation && (
          <>
            <div style={styles.header}>
              <h2 style={styles.title}>
                {selectedConversation.agent}
              </h2>
              {selectedConversation.model && (
                <span style={styles.model}>
                  {selectedConversation.model}
                </span>
              )}
            </div>
            <SpanTimeline spans={selectedConversation.spans} />
            <ConversationFlow spans={selectedConversation.spans} />
          </>
        )}
      </div>

      {/* Right sidebar */}
      <div style={styles.rightSidebar}>
        {selectedConversation && (
          <div style={styles.convId}>
            <div style={styles.convIdLabel}>Conversation ID</div>
            <div style={styles.convIdValue}>{selectedConversation.id}</div>
          </div>
        )}
        {parsed && (
          <>
            <div style={{ height: selectedConversation ? 12 : 0 }} />
            <CostSummary costs={parsed.costs} />
            <div style={{ height: 12 }} />
            <UsageSummary usages={parsed.usages} />
          </>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  layout: {
    display: "flex",
    height: "100vh",
    overflow: "hidden",
  },
  sidebar: {
    width: 300,
    flexShrink: 0,
    overflow: "hidden",
  },
  center: {
    flex: 1,
    overflowY: "auto",
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  rightSidebar: {
    width: 280,
    flexShrink: 0,
    overflowY: "auto",
    padding: 16,
    borderLeft: "1px solid #2d333b",
  },
  convId: {
    background: "#161b22",
    border: "1px solid #2d333b",
    borderRadius: 6,
    padding: "8px 10px",
  },
  convIdLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: "#8b949e",
    textTransform: "uppercase" as const,
    letterSpacing: "0.5px",
    marginBottom: 4,
  },
  convIdValue: {
    fontSize: 11,
    fontFamily: "monospace",
    color: "#d2a8ff",
    wordBreak: "break-all" as const,
  },
  status: {
    color: "#8b949e",
    textAlign: "center",
    padding: 40,
  },
  error: {
    color: "#f85149",
    textAlign: "center",
    padding: 40,
  },
  placeholder: {
    color: "#6e7681",
    textAlign: "center",
    padding: 80,
    fontSize: 15,
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    paddingBottom: 8,
    borderBottom: "1px solid #2d333b",
  },
  title: {
    fontSize: 18,
    fontWeight: 600,
    color: "#e1e4e8",
  },
  model: {
    fontSize: 13,
    color: "#d2a8ff",
    background: "#21262d",
    padding: "2px 8px",
    borderRadius: 4,
    fontFamily: "monospace",
  },
};

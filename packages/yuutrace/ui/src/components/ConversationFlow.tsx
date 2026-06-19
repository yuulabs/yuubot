import type { ConversationItem, CostEvent, Span, TurnEvent } from "../types";
import {
    extractCostEvents,
    extractLlmUsageEvents,
    extractToolUsageEvents,
    extractTurnEvents,
    extractTurnSpans,
    formatToolArguments,
} from "../utils/parse";
import { ImageBlock } from "./ImageBlock";
import { LlmCard } from "./LlmCard";
import { ToolCard } from "./ToolCard";

export interface ConversationFlowProps {
  spans: Span[];
}

/**
 * Waterfall-style conversation flow.
 *
 * Supports three formats:
 * - **Span-based**: "turn" child spans (real-time, current)
 * - **Event-based**: "yuu.turn" events on conversation spans (pre-refactor)
 * - **Legacy**: "user" events (content string) + "llm_gen" child spans
 *
 * Detection priority: turn spans → yuu.turn events → legacy.
 */
type TimelineEntry =
  | { kind: "span"; span: Span; time: number }
  | { kind: "user"; content: string; time: number; key: string }
  | { kind: "turn"; turn: TurnEvent; time: number; key: string };

export function ConversationFlow({ spans }: ConversationFlowProps) {
  // Filter out the "tools" wrapper span -- it contains no I/O data;
  // individual "tool:*" child spans carry all the useful information.
  const filtered = spans.filter((s) => s.name !== "tools");

  const convSpans = filtered.filter((s) => s.name === "conversation");

  // Detect format: turn spans (new) → yuu.turn events (middle) → legacy
  const hasTurnSpans = filtered.some((s) => s.name === "turn");
  const hasTurnEvents = !hasTurnSpans && convSpans.some((s) =>
    s.events.some((ev) => ev.name === "yuu.turn"),
  );
  const hasModernTurns = hasTurnSpans || hasTurnEvents;

  // Build timeline entries for turns/user messages
  let messageEntries: TimelineEntry[];
  if (hasTurnSpans) {
    // New format: extract from turn child spans
    const turnEvents = extractTurnSpans(filtered);
    messageEntries = turnEvents.map((turn, i) => ({
      kind: "turn" as const,
      turn,
      time: turn.startTimeNs,
      key: `turn-span-${i}`,
    }));
  } else if (hasTurnEvents) {
    // Middle format: extract turn events from conversation spans
    messageEntries = convSpans.flatMap((s) =>
      extractTurnEvents(s).map((turn, i) => ({
        kind: "turn" as const,
        turn,
        time: turn.timeNs,
        key: `${s.span_id}-turn-${i}`,
      })),
    );
  } else {
    // Legacy format: extract "user" events with content string
    messageEntries = convSpans.flatMap((s) =>
      s.events
        .filter((ev) => ev.name === "user")
        .map((ev, i) => ({
          kind: "user" as const,
          content: (ev.attributes["content"] as string) ?? "",
          time: ev.time_unix_nano,
          key: `${s.span_id}-user-${i}`,
        })),
    );
  }

  // Filter out turn spans and llm_gen spans from span entries (rendered as TurnCards)
  const spanEntries: TimelineEntry[] = filtered
    .filter((s) => s.name !== "turn")
    .filter((s) => !(hasModernTurns && s.name === "llm_gen"))
    .map((s) => ({
      kind: "span",
      span: s,
      time: s.start_time_unix_nano,
    }));

  const timeline = [...spanEntries, ...messageEntries].sort(
    (a, b) => a.time - b.time,
  );

  // Find the earliest "conversation" span so we can suppress repeated system
  // prompts on continuation spans.
  const firstConvNano = convSpans.reduce(
    (min, s) => (s.start_time_unix_nano < min ? s.start_time_unix_nano : min),
    Infinity,
  );

  return (
    <div style={styles.container}>
      {timeline.map((entry) => {
        if (entry.kind === "user") {
          return <LegacyUserCard key={entry.key} content={entry.content} />;
        }

        if (entry.kind === "turn") {
          return (
            <TurnCard key={entry.key} turn={entry.turn} />
          );
        }

        const { span } = entry;
        const costs = extractCostEvents(span);
        const isLlm = span.name === "llm_gen" || span.name.startsWith("llm");
        const isTool = span.name.startsWith("tool:");

        if (isLlm) {
          const usages = extractLlmUsageEvents(span);
          const llmCost = costs.find((c) => c.category === "llm");
          return (
            <LlmCard
              key={span.span_id}
              span={span}
              usage={usages[0]}
              cost={llmCost}
            />
          );
        }

        if (isTool) {
          const toolUsages = extractToolUsageEvents(span);
          const toolCost = costs.find((c) => c.category === "tool");
          return (
            <ToolCard
              key={span.span_id}
              span={span}
              usage={toolUsages[0]}
              cost={toolCost}
            />
          );
        }

        // Generic span (e.g. conversation root).
        const isContinuation =
          span.name === "conversation" &&
          span.start_time_unix_nano !== firstConvNano;
        return (
          <GenericCard
            key={span.span_id}
            span={span}
            costs={costs}
            hideSystem={isContinuation}
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Turn card (new format)
// ---------------------------------------------------------------------------

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function TurnCard({ turn }: { turn: TurnEvent }) {
  const isUser = turn.role === "user";
  const isSystem = turn.role === "system";
  const cardStyle = isSystem ? styles.systemCard : (isUser ? styles.userCard : styles.assistantCard);
  const labelStyle = isSystem ? styles.systemLabel : (isUser ? styles.userLabel : styles.assistantLabel);
  const label = turn.role[0].toUpperCase() + turn.role.slice(1);

  // Compute duration for assistant turns
  let durationMs: number | undefined;
  if (!isUser && turn.startTimeNs !== turn.timeNs) {
    durationMs = (turn.timeNs - turn.startTimeNs) / 1_000_000;
  }

  const { usage, cost } = turn;

  return (
    <div style={cardStyle}>
      <div style={styles.turnHeader}>
        <div style={labelStyle}>{label}</div>
        <div style={styles.turnHeaderRight}>
          {cost && (
            <span style={styles.costBadge}>${cost.amount.toFixed(4)}</span>
          )}
          {durationMs != null && (
            <span style={styles.duration}>{durationMs.toFixed(0)}ms</span>
          )}
        </div>
      </div>

      {usage && (
        <div style={styles.usageRow}>
          <span style={styles.modelBadge}>{usage.model}</span>
          <span style={styles.tokenBadge}>
            <span style={styles.tokenLabel}>in</span>{" "}
            <span style={styles.tokenValueBlue}>{formatTokens(usage.inputTokens)}</span>
          </span>
          <span style={styles.tokenBadge}>
            <span style={styles.tokenLabel}>out</span>{" "}
            <span style={styles.tokenValuePurple}>{formatTokens(usage.outputTokens)}</span>
          </span>
          {usage.cacheReadTokens > 0 && (
            <span style={styles.tokenBadge}>
              <span style={styles.tokenLabel}>cache↓</span>{" "}
              <span style={styles.tokenValueGreen}>{formatTokens(usage.cacheReadTokens)}</span>
            </span>
          )}
          {usage.cacheWriteTokens > 0 && (
            <span style={styles.tokenBadge}>
              <span style={styles.tokenLabel}>cache↑</span>{" "}
              <span style={styles.tokenValueOrange}>{formatTokens(usage.cacheWriteTokens)}</span>
            </span>
          )}
        </div>
      )}

      <ItemsRenderer items={turn.items} />
      {isSystem && turn.tools && (
        <div style={{ marginTop: 8 }}>
          <div style={styles.contextLabel}>Tools</div>
          <pre style={styles.contextPre}>{(() => {
            try {
              return JSON.stringify(JSON.parse(turn.tools), null, 2);
            } catch {
              return turn.tools;
            }
          })()}</pre>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared items renderer (renders yuullm.Item arrays)
// ---------------------------------------------------------------------------

/** Decode HTML entities (e.g. &lt; → <) that arrive from XML-formatted messages. */
function decodeHtmlEntities(text: string): string {
  if (!text.includes("&")) return text;
  const textarea = document.createElement("textarea");
  textarea.innerHTML = text;
  return textarea.value;
}

function mergeTextItems(items: ConversationItem[]): ConversationItem[] {
  const result: ConversationItem[] = [];
  for (const item of items) {
    const last = result[result.length - 1];
    if (item.type === "text" && last?.type === "text") {
      result[result.length - 1] = { ...last, text: (last.text ?? "") + (item.text ?? "") };
    } else {
      result.push(item);
    }
  }
  return result;
}

function ItemsRenderer({ items }: { items: ConversationItem[] }) {
  if (!items || items.length === 0) return null;
  const merged = mergeTextItems(items);

  return (
    <div>
      {merged.map((item, idx) => (
        <div key={idx} style={styles.contentItem}>
          {item.type === "text" && (
            <div style={styles.textContent}>{decodeHtmlEntities(item.text ?? "")}</div>
          )}
          {item.type === "thinking" && (
            <details style={styles.thinkingDetails}>
              <summary style={styles.thinkingSummary}>Thinking</summary>
              <div style={styles.thinkingContent}>{item.thinking ?? ""}</div>
            </details>
          )}
          {item.type === "tool_calls" && (
            <div style={styles.toolCalls}>
              <div style={styles.toolCallsHeader}>Tool Calls:</div>
              {item.tool_calls?.map((tc, tcIdx) => (
                <div key={tcIdx} style={styles.toolCall}>
                  <span style={styles.toolCallName}>{tc.function}</span>
                  <pre style={styles.toolCallArgs}>
                    {formatToolArguments(tc.arguments)}
                  </pre>
                </div>
              ))}
            </div>
          )}
          {item.type === "tool_call" && (
            <div style={styles.toolCalls}>
              <div style={styles.toolCallsHeader}>Tool Call:</div>
              <div style={styles.toolCall}>
                <span style={styles.toolCallName}>{item.name ?? item.id}</span>
                <pre style={styles.toolCallArgs}>
                  {formatToolArguments(item.arguments)}
                </pre>
              </div>
            </div>
          )}
          {item.type === "tool_result" && (
            <div style={styles.toolCalls}>
              <div style={styles.toolCallsHeader}>Tool Result: {item.tool_call_id}</div>
              <pre style={styles.toolCallArgs}>{formatToolArguments(item.content)}</pre>
            </div>
          )}
          {item.type === "image" && <ImageBlock source={item.source} />}
          {item.type === "image_url" && <ImageBlock source={item.image_url} />}
          {item.type === "document" && (
            <div style={styles.mediaBadge}>
              <span style={styles.mediaIcon}>doc</span>
              <span style={styles.mediaLabel}>
                {item.source?.media_type ?? "document"}
              </span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Legacy cards (backward compat)
// ---------------------------------------------------------------------------

function GenericCard({
  span,
  costs,
  hideSystem = false,
}: {
  span: Span;
  costs: CostEvent[];
  hideSystem?: boolean;
}) {
  const totalCost = costs.reduce((s, c) => s + c.amount, 0);
  const durationMs =
    (span.end_time_unix_nano - span.start_time_unix_nano) / 1_000_000;

  const agentName = hideSystem
    ? undefined
    : (span.attributes["yuu.agent"] as string | undefined);
  const modelName = hideSystem
    ? undefined
    : (span.attributes["yuu.conversation.model"] as string | undefined);

  const hasContext = agentName || modelName;

  return (
    <div style={styles.card}>
      <div style={styles.cardHeader}>
        <span style={styles.spanName}>{span.name}</span>
        <span style={styles.duration}>{durationMs.toFixed(0)}ms</span>
      </div>

      {hasContext && (
        <div style={styles.contextSection}>
          {(agentName || modelName) && (
            <div style={styles.contextBlock}>
              <div style={styles.contextLabel}>Agent</div>
              <div style={styles.contextContent}>
                {agentName && <span style={styles.agentBadge}>{agentName}</span>}
                {modelName && <span style={styles.modelBadge}>{modelName}</span>}
              </div>
            </div>
          )}

        </div>
      )}

      {totalCost > 0 && (
        <div style={styles.costLine}>Total: ${totalCost.toFixed(4)}</div>
      )}
    </div>
  );
}

function LegacyUserCard({ content }: { content: string }) {
  let display: string;
  try {
    const parsed = JSON.parse(content);
    display = typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2);
  } catch {
    display = content;
  }
  return (
    <div style={styles.userCard}>
      <div style={styles.userLabel}>User</div>
      <div style={styles.textContent}>{display}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    padding: 16,
  },
  card: {
    background: "#161b22",
    border: "1px solid #2d333b",
    borderRadius: 8,
    padding: "12px 16px",
  },
  userCard: {
    background: "#0d1117",
    border: "1px solid #1f6feb",
    borderRadius: 8,
    padding: "12px 16px",
  },
  systemCard: {
    background: "#0d1117",
    border: "1px solid #3d444d",
    borderRadius: 8,
    padding: "12px 16px",
    borderLeft: "3px solid #8b949e",
  },
  assistantCard: {
    background: "#161b22",
    border: "1px solid #1f3a5f",
    borderRadius: 8,
    padding: "12px 16px",
    borderLeft: "3px solid #58a6ff",
  },
  turnHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 6,
  },
  turnHeaderRight: {
    display: "flex",
    alignItems: "center",
    gap: 10,
  },
  costBadge: {
    fontSize: 13,
    color: "#3fb950",
    fontFamily: "monospace",
    fontWeight: 600,
  },
  usageRow: {
    display: "flex",
    gap: 6,
    flexWrap: "wrap" as const,
    alignItems: "center",
    marginBottom: 8,
  },
  modelBadge: {
    fontSize: 12,
    color: "#d2a8ff",
    background: "#21262d",
    padding: "1px 6px",
    borderRadius: 4,
    fontFamily: "monospace",
  },
  tokenBadge: {
    display: "inline-flex",
    gap: 4,
    alignItems: "center",
    fontSize: 12,
    fontFamily: "monospace",
    padding: "2px 8px",
    borderRadius: 4,
    background: "#0d1117",
    border: "1px solid #2d333b",
  },
  tokenLabel: {
    color: "#8b949e",
    fontSize: 10,
  },
  tokenValueBlue: {
    color: "#58a6ff",
    fontWeight: 600,
  },
  tokenValuePurple: {
    color: "#d2a8ff",
    fontWeight: 600,
  },
  tokenValueGreen: {
    color: "#3fb950",
    fontWeight: 600,
  },
  tokenValueOrange: {
    color: "#f0883e",
    fontWeight: 600,
  },
  userLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: "#58a6ff",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  systemLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: "#8b949e",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  assistantLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: "#d2a8ff",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  textContent: {
    fontSize: 13,
    color: "#c9d1d9",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
    lineHeight: 1.5,
  },
  contentItem: {
    marginBottom: 6,
  },
  toolCalls: {
    fontSize: 12,
    color: "#8b949e",
  },
  toolCallsHeader: {
    fontWeight: 600,
    marginBottom: 4,
    color: "#3fb950",
  },
  toolCall: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
    marginLeft: 12,
    marginBottom: 4,
    padding: "4px 8px",
    background: "#0d1117",
    borderRadius: 4,
    borderLeft: "2px solid #3fb950",
  },
  toolCallName: {
    fontWeight: 600,
    color: "#58a6ff",
    fontFamily: "monospace",
  },
  toolCallArgs: {
    fontSize: 11,
    color: "#8b949e",
    fontFamily: "monospace",
    wordBreak: "break-all",
    whiteSpace: "pre-wrap",
    margin: 0,
  },
  mediaBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 10px",
    background: "#21262d",
    border: "1px solid #2d333b",
    borderRadius: 4,
    fontSize: 12,
    fontFamily: "monospace",
    color: "#8b949e",
  },
  mediaIcon: {
    fontSize: 14,
  },
  mediaLabel: {
    color: "#6e7681",
  },
  cardHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  spanName: {
    fontWeight: 600,
    fontSize: 14,
    color: "#e1e4e8",
  },
  duration: {
    fontSize: 12,
    color: "#8b949e",
    fontFamily: "monospace",
  },
  costLine: {
    marginTop: 4,
    fontSize: 12,
    color: "#3fb950",
    fontFamily: "monospace",
  },
  contextSection: {
    marginTop: 12,
    paddingTop: 12,
    borderTop: "1px solid #2d333b",
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  contextBlock: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  contextLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: "#8b949e",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  contextContent: {
    fontSize: 13,
    color: "#c9d1d9",
    lineHeight: 1.5,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  agentBadge: {
    display: "inline-block",
    fontSize: 12,
    color: "#d2a8ff",
    background: "#21262d",
    padding: "1px 6px",
    borderRadius: 4,
    fontFamily: "monospace",
    marginRight: 6,
  },
  thinkingDetails: {
    marginBottom: 4,
  },
  thinkingSummary: {
    fontSize: 11,
    color: "#8b949e",
    cursor: "pointer",
    userSelect: "none" as const,
    fontFamily: "monospace",
  },
  thinkingContent: {
    fontSize: 12,
    color: "#6e7681",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
    lineHeight: 1.5,
    marginTop: 4,
    paddingLeft: 12,
    borderLeft: "2px solid #2d333b",
  },
  contextPre: {
    fontSize: 11,
    color: "#8b949e",
    background: "#0d1117",
    padding: 8,
    borderRadius: 4,
    overflow: "auto",
    maxHeight: 400,
    margin: 0,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
};

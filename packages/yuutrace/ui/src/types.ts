// ---------------------------------------------------------------------------
// Core types — aligned with ytrace_spec.md and Python SDK otel.py
// ---------------------------------------------------------------------------

/** Summary of a conversation for list views. */
export interface ConversationSummary {
  id: string;
  agent: string;
  model?: string;
  span_count: number;
  total_cost: number;
  start_time: number;
  end_time: number;
}

/** Full conversation with all spans. */
export interface Conversation {
  id: string;
  agent: string;
  model?: string;
  tags?: string[];
  spans: Span[];
  total_cost?: number;
  start_time: number;
  end_time: number;
}

/** A single OTEL span. */
export interface Span {
  trace_id: string;
  span_id: string;
  parent_span_id?: string | null;
  name: string;
  start_time_unix_nano: number;
  end_time_unix_nano: number;
  status_code: number;
  attributes: Record<string, unknown>;
  events: SpanEvent[];
}

/** A single OTEL span event. */
export interface SpanEvent {
  id: number;
  name: string;
  time_unix_nano: number;
  attributes: Record<string, unknown>;
}

/** Parsed cost event from yuu.cost event attributes. */
export interface CostEvent {
  category: "llm" | "tool";
  currency: string;
  amount: number;
  source?: string;
  pricingId?: string;
  llmProvider?: string;
  llmModel?: string;
  llmRequestId?: string;
  toolName?: string;
  toolCallId?: string;
}

/** Parsed LLM usage event from yuu.llm.usage event attributes. */
export interface LlmUsageEvent {
  provider: string;
  model: string;
  requestId?: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  totalTokens?: number;
}

/** Parsed tool usage event from yuu.tool.usage event attributes. */
export interface ToolUsageEvent {
  name: string;
  callId?: string;
  unit: string;
  quantity: number;
}

/** Tool call item emitted by providers that support tool invocation. */
export interface ToolCall {
  function: string;
  arguments: unknown;
}

/** Image/document payload source attached to a conversation item. */
export interface MediaSource {
  type?: string;
  url?: string;
  data?: string;
  media_type?: string;
}

/** Provider message item rendered in turn and llm_gen views. */
export interface ConversationItem {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  arguments?: unknown;
  tool_call_id?: string;
  content?: unknown;
  tool_calls?: ToolCall[];
  source?: MediaSource;
  image_url?: MediaSource;
  thinking?: string;
}

/** Parsed turn event from yuu.turn event attributes. */
export interface TurnEvent {
  role: "system" | "user" | "assistant" | "tool";
  items: ConversationItem[];
  startTimeNs: number;
  timeNs: number;
  /** Per-turn usage (embedded in yuu.turn event attributes). */
  usage?: LlmUsageEvent;
  /** Per-turn cost (embedded in yuu.turn event attributes). */
  cost?: CostEvent;
  /** JSON string of tool specs (system turns only, from yuu.context.system.tools). */
  tools?: string;
}

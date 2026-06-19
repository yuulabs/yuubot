/**
 * Attribute key extraction utilities.
 *
 * This is the ONLY place in the frontend that references yuu.* magic strings.
 * All keys correspond to Python-side otel.py constants.
 */

import type {
  ConversationItem,
  CostEvent,
  LlmUsageEvent,
  Span,
  SpanEvent,
  ToolUsageEvent,
  TurnEvent,
} from "../types";

type TurnRole = TurnEvent["role"];

// ---------------------------------------------------------------------------
// Single-event extractors
// ---------------------------------------------------------------------------

function isConversationItem(value: unknown): value is ConversationItem {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    typeof value.type === "string"
  );
}

function isTurnRole(value: unknown): value is TurnRole {
  return (
    value === "system" ||
    value === "user" ||
    value === "assistant" ||
    value === "tool"
  );
}

export function parseConversationItems(raw: unknown): ConversationItem[] {
  if (typeof raw !== "string") return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isConversationItem);
  } catch {
    return [];
  }
}

export function formatToolArguments(args: unknown): string {
  if (typeof args === "string") {
    try {
      return JSON.stringify(JSON.parse(args), null, 2);
    } catch {
      return args;
    }
  }
  const formatted = JSON.stringify(args, null, 2);
  return formatted ?? String(args);
}

function parseCostEvent(ev: SpanEvent): CostEvent | null {
  const a = ev.attributes;
  const amount = a["yuu.cost.amount"];
  if (amount == null) return null;
  return {
    category: (a["yuu.cost.category"] as "llm" | "tool") ?? "llm",
    currency: (a["yuu.cost.currency"] as string) ?? "USD",
    amount: Number(amount),
    source: a["yuu.cost.source"] as string | undefined,
    pricingId: a["yuu.cost.pricing_id"] as string | undefined,
    llmProvider: a["yuu.llm.provider"] as string | undefined,
    llmModel: a["yuu.llm.model"] as string | undefined,
    llmRequestId: a["yuu.llm.request_id"] as string | undefined,
    toolName: a["yuu.tool.name"] as string | undefined,
    toolCallId: a["yuu.tool.call_id"] as string | undefined,
  };
}

function parseLlmUsageEvent(ev: SpanEvent): LlmUsageEvent | null {
  const a = ev.attributes;
  const provider = a["yuu.llm.provider"];
  if (provider == null) return null;
  return {
    provider: String(provider),
    model: String(a["yuu.llm.model"] ?? ""),
    requestId: a["yuu.llm.request_id"] as string | undefined,
    inputTokens: Number(a["yuu.llm.usage.input_tokens"] ?? 0),
    outputTokens: Number(a["yuu.llm.usage.output_tokens"] ?? 0),
    cacheReadTokens: Number(a["yuu.llm.usage.cache_read_tokens"] ?? 0),
    cacheWriteTokens: Number(a["yuu.llm.usage.cache_write_tokens"] ?? 0),
    totalTokens:
      a["yuu.llm.usage.total_tokens"] != null
        ? Number(a["yuu.llm.usage.total_tokens"])
        : undefined,
  };
}

function parseToolUsageEvent(ev: SpanEvent): ToolUsageEvent | null {
  const a = ev.attributes;
  const name = a["yuu.tool.name"];
  if (name == null) return null;
  return {
    name: String(name),
    callId: a["yuu.tool.call_id"] as string | undefined,
    unit: String(a["yuu.tool.usage.unit"] ?? ""),
    quantity: Number(a["yuu.tool.usage.quantity"] ?? 0),
  };
}

// ---------------------------------------------------------------------------
// Span-level extractors
// ---------------------------------------------------------------------------

/** Extract all cost events from a span's events. */
export function extractCostEvents(span: Span): CostEvent[] {
  return span.events
    .filter((e) => e.name === "yuu.cost")
    .map(parseCostEvent)
    .filter((e): e is CostEvent => e !== null);
}

/** Extract all LLM usage events from a span's events. */
export function extractLlmUsageEvents(span: Span): LlmUsageEvent[] {
  return span.events
    .filter((e) => e.name === "yuu.llm.usage")
    .map(parseLlmUsageEvent)
    .filter((e): e is LlmUsageEvent => e !== null);
}

/** Extract all tool usage events from a span's events. */
export function extractToolUsageEvents(span: Span): ToolUsageEvent[] {
  return span.events
    .filter((e) => e.name === "yuu.tool.usage")
    .map(parseToolUsageEvent)
    .filter((e): e is ToolUsageEvent => e !== null);
}

/** Extract turn events from a span's events (legacy/middle format). */
export function extractTurnEvents(span: Span): TurnEvent[] {
  return span.events
    .filter((e) => e.name === "yuu.turn")
    .map((ev): TurnEvent | null => {
      const a = ev.attributes;
      const role = a["yuu.turn.role"];
      if (!isTurnRole(role)) return null;
      const items = parseConversationItems(a["yuu.turn.items"]);

      const provider = a["yuu.llm.provider"] as string | undefined;
      let usage: LlmUsageEvent | undefined;
      if (provider) {
        usage = {
          provider,
          model: (a["yuu.llm.model"] as string) ?? "",
          requestId: a["yuu.llm.request_id"] as string | undefined,
          inputTokens: Number(a["yuu.llm.usage.input_tokens"] ?? 0),
          outputTokens: Number(a["yuu.llm.usage.output_tokens"] ?? 0),
          cacheReadTokens: Number(a["yuu.llm.usage.cache_read_tokens"] ?? 0),
          cacheWriteTokens: Number(a["yuu.llm.usage.cache_write_tokens"] ?? 0),
          totalTokens:
            a["yuu.llm.usage.total_tokens"] != null
              ? Number(a["yuu.llm.usage.total_tokens"])
              : undefined,
        };
      }

      const costAmount = a["yuu.cost.amount"];
      let cost: CostEvent | undefined;
      if (costAmount != null) {
        cost = {
          category: (a["yuu.cost.category"] as "llm" | "tool") ?? "llm",
          currency: (a["yuu.cost.currency"] as string) ?? "USD",
          amount: Number(costAmount),
          source: a["yuu.cost.source"] as string | undefined,
          pricingId: a["yuu.cost.pricing_id"] as string | undefined,
          llmProvider: a["yuu.llm.provider"] as string | undefined,
          llmModel: a["yuu.llm.model"] as string | undefined,
          llmRequestId: a["yuu.llm.request_id"] as string | undefined,
        };
      }

      return {
        role,
        items,
        startTimeNs: Number(a["yuu.turn.start_time"] ?? ev.time_unix_nano),
        timeNs: ev.time_unix_nano,
        usage,
        cost,
      };
    })
    .filter((e): e is TurnEvent => e !== null);
}

/** Extract turns from child spans named "turn" (new format — real-time). */
export function extractTurnSpans(spans: Span[]): TurnEvent[] {
  return spans
    .filter((s) => s.name === "turn")
    .map((s): TurnEvent | null => {
      const a = s.attributes;
      const role = a["yuu.turn.role"];
      if (!isTurnRole(role)) return null;

      const items = parseConversationItems(a["yuu.turn.items"]);

      // Usage/cost from span attributes (set directly by TurnContext.usage())
      const provider = a["yuu.llm.provider"] as string | undefined;
      let usage: LlmUsageEvent | undefined;
      if (provider) {
        usage = {
          provider,
          model: (a["yuu.llm.model"] as string) ?? "",
          requestId: a["yuu.llm.request_id"] as string | undefined,
          inputTokens: Number(a["yuu.llm.usage.input_tokens"] ?? 0),
          outputTokens: Number(a["yuu.llm.usage.output_tokens"] ?? 0),
          cacheReadTokens: Number(a["yuu.llm.usage.cache_read_tokens"] ?? 0),
          cacheWriteTokens: Number(a["yuu.llm.usage.cache_write_tokens"] ?? 0),
          totalTokens:
            a["yuu.llm.usage.total_tokens"] != null
              ? Number(a["yuu.llm.usage.total_tokens"])
              : undefined,
        };
      }

      const costAmount = a["yuu.cost.amount"];
      let cost: CostEvent | undefined;
      if (costAmount != null) {
        cost = {
          category: (a["yuu.cost.category"] as "llm" | "tool") ?? "llm",
          currency: (a["yuu.cost.currency"] as string) ?? "USD",
          amount: Number(costAmount),
          source: a["yuu.cost.source"] as string | undefined,
          pricingId: a["yuu.cost.pricing_id"] as string | undefined,
          llmProvider: a["yuu.llm.provider"] as string | undefined,
          llmModel: a["yuu.llm.model"] as string | undefined,
          llmRequestId: a["yuu.llm.request_id"] as string | undefined,
        };
      }

      return {
        role,
        items,
        startTimeNs: Number(a["yuu.turn.start_time"] ?? s.start_time_unix_nano),
        timeNs: s.end_time_unix_nano,
        usage,
        cost,
        tools: role === "system" ? (a["yuu.context.system.tools"] as string | undefined) : undefined,
      };
    })
    .filter((e): e is TurnEvent => e !== null);
}

// ---------------------------------------------------------------------------
// Conversation-level aggregation
// ---------------------------------------------------------------------------

/** Parse all typed events from a list of spans. */
export function parseConversation(spans: Span[]): {
  costs: CostEvent[];
  usages: LlmUsageEvent[];
  toolUsages: ToolUsageEvent[];
} {
  const costs: CostEvent[] = [];
  const usages: LlmUsageEvent[] = [];
  const toolUsages: ToolUsageEvent[] = [];

  for (const span of spans) {
    costs.push(...extractCostEvents(span));
    usages.push(...extractLlmUsageEvents(span));
    toolUsages.push(...extractToolUsageEvents(span));
  }

  return { costs, usages, toolUsages };
}

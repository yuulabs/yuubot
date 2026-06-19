# yuutrace

LLM-oriented observability SDK built on OpenTelemetry. Provides structured tracing for LLM agent workloads with first-class cost and token usage tracking.

## What's in the box

| Deliverable | Registry | Description |
|---|---|---|
| `yuutrace` | PyPI | Python SDK for instrumentation + CLI (`ytrace server` / `ytrace ui`) |
| `@yuutrace/ui` | npm | React component library for trace visualization |

```
your-agent (Python)
  │  import yuutrace
  │
  ▼
ytrace server ──OTLP/HTTP JSON──▶ SQLite
  │
  ▼
ytrace ui ──REST API──▶ Browser (@yuutrace/ui)
```

## Installation

```bash
# Python SDK (includes CLI tools)
pip install yuutrace

# React components (for embedding in your own dashboard)
npm install @yuutrace/ui
```

## Quick Start

### 1. Start the Trace Collector

```bash
ytrace server --db ./traces.db --port 4318
```

### 2. Configure Tracing

```python
import yuutrace as ytrace

ytrace.init(service_name="my-agent")
```

If you already configure OpenTelemetry elsewhere, yuutrace reuses the existing `TracerProvider` and `init()` becomes a no-op.
If you do not configure tracing, yuutrace operations are no-ops and emit one warning on first use. To intentionally keep tracing off without a warning, call `ytrace.disable()`.

### 3. Instrument Your Agent

Below is a minimal but complete example covering the core workflow: conversation → turns → usage/cost → tool execution.

```python
import yuutrace as ytrace
from uuid import uuid4

ytrace.init(service_name="my-agent")

async def agent_turn(user_msg: str):
    with ytrace.conversation(
        id=uuid4(),            # UUID – unique conversation identifier
        agent="my-agent",      # str  – agent name
        model="gpt-4o",        # str  – primary model
        tags=["prod"],         # list[str] | None – filtering tags
    ) as chat:

        # Record context
        chat.system(persona="You are helpful.", tools=tool_specs)
        chat.user(user_msg)

        # ── LLM generation ──────────────────────────────────────
        with chat.turn("assistant") as turn:
            response = await llm.call(messages)
            turn.add({"type": "text", "text": response.text})
            turn.usage(response.usage, cost=response.cost)
            # Alternative: instead of `turn.usage(...)`, call the wrappers
            # inside the active turn span:
            #
            # ytrace.record_llm_usage(response.usage, cost=response.cost)
            # ytrace.record_llm_usage(
            #     provider="openai",
            #     model="gpt-4o",
            #     input_tokens=150,
            #     output_tokens=42,
            #     cache_read_tokens=80,
            # )

        # ── Tool execution ──────────────────────────────────────
        with chat.tool_batch() as tools:
            with tools.tool(name="search", call_id="call_1", input={"q": "BTC"}) as tool:
                try:
                    tool.ok(await search_fn(q="BTC"))
                except Exception as exc:
                    tool.fail(str(exc))
                    raise
```

### 4. View Traces

```bash
ytrace ui --db ./traces.db --port 8080
# Open http://localhost:8080
```

## Key Concepts

### Span Hierarchy

Every instrumented conversation produces a tree of OpenTelemetry spans:

```
conversation (root)
  ├── turn             # one user/assistant turn
  ├── tools            # a batch of tool calls
  │     ├── tool:search
  │     └── tool:calc
  ├── turn
  └── ...
```

The root `conversation` span carries metadata (`conversation.id`, `agent`, `model`, `tags`). Child spans are created automatically by the context managers.

### Delta Semantics

All cost and usage data is recorded as **increments** (deltas). A single span can emit multiple cost/usage events. Aggregation happens at query time, not write time. This keeps the write path simple and concurrent-safe.

### Event Types

| Event Name | Purpose | Key Attributes |
|---|---|---|
| `yuu.cost` | Cost increment | `category`, `currency`, `amount`, `llm.model`, `tool.name` |
| `yuu.llm.usage` | Token usage | `provider`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens` |
| `yuu.tool.usage` | Tool usage (optional) | `name`, `unit`, `quantity` |

Business code never writes these event names or attribute keys directly — the SDK wraps them in type-safe functions.

### No-op by Default

If tracing is unconfigured, yuutrace operations become no-ops and emit one warning on first use. If you explicitly call `ytrace.disable()`, operations stay no-op without warning. Once tracing is configured, `current_span()` still raises `NoActiveSpanError` when you record outside an active span.

## Python SDK API Reference

### Initialization

```python
ytrace.init(
    *,
    endpoint: str = "http://localhost:4318/v1/traces",
    service_name: str = "yuutrace",
    service_version: str | None = None,
    timeout_seconds: float = 10.0,
) -> None
```

No-op if OpenTelemetry is already configured. Registers `atexit` shutdown hook.

```python
ytrace.disable() -> None
ytrace.is_initialized() -> bool
ytrace.is_enabled() -> bool
ytrace.is_disabled() -> bool
```

### Context Managers

#### `conversation()`

```python
ytrace.conversation(
    *,
    id: UUID,                            # unique conversation ID
    agent: str,                          # agent name
    model: str,                          # primary LLM model
    tags: list[str] | None = None,       # filtering/grouping tags
) -> Iterator[ConversationContext]
```

Root span. If tracing is not configured, this returns a no-op context.

#### `ConversationContext`

| Method | Signature | Description |
|---|---|---|
| `system` | `(persona: str, tools: list[Any] \| None = None) -> None` | Record system prompt and tool specs |
| `user` | `(*items: Any) -> None` | Record a single user turn |
| `turn` | `(role: str) -> Iterator[TurnContext]` | Open a child span for a turn |
| `start_turn` | `(role: str) -> TurnContext` | Start a turn manually |
| `tool_batch` | `() -> Iterator[ToolsContext]` | Preferred tool batch context manager |
| `start_tool_batch` | `() -> ToolsContext` | Start a tool batch manually |
| `tools` | `() -> Iterator[ToolsContext]` | Compatibility alias for `tool_batch()` |
| `start_tools` | `() -> ToolsContext` | Compatibility alias for `start_tool_batch()` |

#### `TurnContext`

| Method | Signature | Description |
|---|---|---|
| `add` | `(*items: Any) -> None` | Append response/content items |
| `usage` | `(usage: object, cost: object \| None = None) -> None` | Record usage and optional cost on the turn |
| `end` | `(error: Exception \| None = None) -> None` | End the turn span |

#### `ToolsContext`

| Method | Signature | Description |
|---|---|---|
| `tool` | `(*, name: str, call_id: str, input: Any) -> Iterator[ToolSpan]` | Open a tool invocation span |
| `start_tool` | `(*, name: str, call_id: str, input: Any) -> ToolSpan` | Start a tool invocation manually |
| `end` | `() -> None` | End the tools batch span |

### Recording Functions

#### `record_llm_usage()`

Accepts a request-level usage object such as `yuullm.Usage`, a pre-built
`LlmUsageDelta`, or keyword arguments. If you also have a matching
`yuullm.Cost`, pass it as `cost=` so callers do not need to construct
`CostDelta` manually:

```python
# Directly record yuullm's request-level objects
ytrace.record_llm_usage(response.usage, cost=response.cost)

# Keyword args (most common)
ytrace.record_llm_usage(
    provider: str,                       # e.g. "openai", "anthropic"
    model: str,                          # e.g. "gpt-4o", "claude-sonnet-4-20250514"
    request_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    total_tokens: int | None = None,     # auto-computed if None
)

# Or pass a struct
ytrace.record_llm_usage(LlmUsageDelta(...))
```

#### `record_cost()` / `record_cost_delta()`

```python
ytrace.record_cost(
    category: str,       # "llm" | "tool"
    currency: str,       # "USD"
    amount: float,       # incremental cost
    # LLM-specific (when category="llm")
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_request_id: str | None = None,
    # Tool-specific (when category="tool")
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    # General
    source: str | None = None,
    pricing_id: str | None = None,
)

# Or pass a struct
ytrace.record_cost_delta(CostDelta(...))
```

Convenience helper for request-level objects:

```python
ytrace.record_llm_cost(usage, cost)
```

#### `record_tool_usage()`

```python
ytrace.record_tool_usage(
    ToolUsageDelta(
        name="get_weather",     # tool name
        unit="api_calls",       # unit of measurement
        quantity=1.0,           # amount
        call_id="call_1",       # optional correlation ID
    )
)
```

### Types

Trace event payloads are frozen `msgspec.Struct` instances. `LlmUsage` and
`LlmCost` are structural protocols that `yuullm.Usage` and `yuullm.Cost`
satisfy.

| Type | Required Fields | Optional Fields |
|---|---|---|
| `LlmUsage` | `provider`, `model` | `request_id`, token counts |
| `LlmCost` | `total_cost` | provider-specific metadata such as `source` |
| `CostDelta` | `category`, `currency`, `amount` | `source`, `pricing_id`, `llm_provider`, `llm_model`, `llm_request_id`, `tool_name`, `tool_call_id` |
| `LlmUsageDelta` | `provider`, `model` | `request_id`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `total_tokens` |
| `ToolUsageDelta` | `name`, `unit`, `quantity` | `call_id` |

Enums:
- `CostCategory` — `"llm"` | `"tool"`
- `Currency` — `"USD"`

### Low-level

| Function | Signature | Description |
|---|---|---|
| `current_span()` | `-> Span` | Return the active OTEL span; returns a no-op span when tracing is disabled/unconfigured |
| `add_event()` | `(name: str, attributes: dict) -> None` | Add event to current span (prefer typed wrappers above) |

### Errors

| Error | When |
|---|---|
| `TracingNotInitializedError` | Compatibility error retained for older fail-fast integrations |
| `NoActiveSpanError` | Recording function called outside any active span after tracing is configured |

## CLI Reference

### `ytrace server`

Receives OTLP/HTTP traces (JSON or Protobuf) and stores them to SQLite.

```bash
ytrace server --db ./traces.db --port 4318 --host 127.0.0.1
```

| Option | Default | Description |
|---|---|---|
| `--db` | `./traces.db` | SQLite database file path |
| `--port` | `4318` | HTTP server port |
| `--host` | `127.0.0.1` | Bind address |

### `ytrace ui`

Serves the trace visualization web UI with REST API.

```bash
ytrace ui --db ./traces.db --port 8080 --host 127.0.0.1
```

| Option | Default | Description |
|---|---|---|
| `--db` | `./traces.db` | SQLite database file path |
| `--port` | `8080` | HTTP server port |
| `--host` | `127.0.0.1` | Bind address |

**REST API endpoints:**

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/conversations` | List conversations (`?limit=50&offset=0&agent=...`) |
| GET | `/api/conversations/{id}` | Single conversation with all spans and events |
| GET | `/api/spans/{id}` | Single span detail |

## React Component Library

`@yuutrace/ui` exports pure presentation components. Data is injected via props — no built-in data fetching, no framework lock-in.

```tsx
import {
  ConversationList,
  ConversationFlow,
  CostSummary,
  UsageSummary,
  SpanTimeline,
  parseConversation,
} from "@yuutrace/ui";

function MyDashboard({ conversation }) {
  const { costs, usages } = parseConversation(conversation.spans);

  return (
    <>
      <SpanTimeline spans={conversation.spans} />
      <ConversationFlow spans={conversation.spans} />
      <CostSummary costs={costs} />
      <UsageSummary usages={usages} />
    </>
  );
}
```

### Components

| Component | Props | Description |
|---|---|---|
| `ConversationList` | `conversations`, `selectedId?`, `onSelect?` | Searchable conversation list |
| `ConversationFlow` | `spans` | Waterfall of LLM/tool cards |
| `LlmCard` | `span`, `usage?`, `cost?` | LLM call detail card |
| `ToolCard` | `span`, `usage?`, `cost?` | Tool call detail card |
| `CostSummary` | `costs` | Cost breakdown by category/model |
| `UsageSummary` | `usages` | Token usage by model |
| `SpanTimeline` | `spans` | Horizontal Gantt chart |

### Utilities

- `parseConversation(spans)` — extract typed cost/usage events from raw spans
- `extractCostEvents(span)` — cost events from a single span
- `extractLlmUsageEvents(span)` — LLM usage from a single span
- `extractToolUsageEvents(span)` — tool usage from a single span

## Examples

See [examples/](examples/) for complete working examples:

- **[weather_agent.py](examples/weather_agent.py)** — Multi-turn agent with LLM calls, tool execution, cost tracking, and error handling

```bash
# Terminal 1: Start collector
ytrace server --db ./traces.db --port 4318

# Terminal 2: Run example
python examples/weather_agent.py

# Terminal 3: Start UI
ytrace ui --db ./traces.db --port 8080
# Open http://localhost:8080
```

## Development

### Prerequisites

- Python >= 3.12
- Node.js >= 20
- [uv](https://docs.astral.sh/uv/) (Python package manager)

### Setup

```bash
# Python
uv sync

# React UI
cd ui && npm install
```

### Build the UI

```bash
# Build standalone app + copy to _static/ for ytrace ui
bash scripts/build_ui.sh

# Or build separately:
cd ui
npm run build:app    # standalone page → dist/app/
npm run build:lib    # npm library → dist/lib/
```

### Project Structure

```
yuutrace/
├── src/yuutrace/
│   ├── __init__.py          # public API
│   ├── types.py             # CostDelta, LlmUsageDelta, ToolUsageDelta
│   ├── context.py           # conversation(), turn(), tool_batch()
│   ├── cost.py              # record_cost(), record_cost_delta()
│   ├── usage.py             # record_llm_usage(), record_tool_usage()
│   ├── span.py              # current_span(), add_event()
│   ├── otel.py              # OTEL attribute keys + serialization
│   └── cli/
│       ├── main.py          # ytrace CLI entry point
│       ├── server.py        # OTLP collector (Starlette)
│       ├── ui.py            # REST API + static serving (Starlette)
│       ├── db.py            # SQLite persistence
│       └── _static/         # pre-built UI assets
├── ui/                      # @yuutrace/ui React package
│   ├── src/
│   │   ├── components/      # ConversationList, LlmCard, etc.
│   │   ├── hooks/           # useTraceData (standalone only)
│   │   ├── pages/           # TracePage
│   │   ├── utils/           # parse.ts
│   │   ├── types.ts
│   │   └── index.ts         # library exports
│   ├── vite.config.ts       # app build
│   └── vite.config.lib.ts   # library build
├── examples/                # Example applications
│   ├── weather_agent.py     # Multi-turn agent example
│   └── README.md            # Example documentation
├── scripts/
│   └── build_ui.sh
└── pyproject.toml
```

## License

MIT

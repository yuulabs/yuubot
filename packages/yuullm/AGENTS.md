# packages/yuullm

Provider-agnostic streaming LLM interface. One client (`YLLMClient`), one
history model (`History` of typed `ContentItem`s), one stream protocol
(`StreamItem`), and a provider pool that hides vendor SDK differences behind a
single `Provider` interface. Public API re-exported from
`src/yuullm/__init__.py`.

This package is a workspace member of the monorepo at the repo root. It has no
runtime dependency on `yuubot`; `yuubot` consumes it, `yuuagents` adapts it via
its own `llm_backend`.

## Source Map (`src/yuullm/`)

| Path | Responsibility |
|---|---|
| `client.py` | `YLLMClient` — entrypoint: `await client.stream(history)` → `(StreamResult, Store)`. Owns retry/recovery, `RawChunkHook`, per-call usage/cost. |
| `session.py` | `YuuSession` — stateful conversation session over a pool (message log, rollover, tool round-trips). Higher-level than `YLLMClient.stream`. |
| `pool.py` | `ProviderPool` — provider selection by priority + affinity; failover on provider error; `CallRecord` accounting. |
| `provider.py` | `Provider` protocol — the vendor contract every `providers/*` module implements (`stream(...)`, model listing, etc.). |
| `pricing.py` | `PriceCalculator` — token → cost using per-model `ModelBinding` pricing tables. |
| `cache_config.py` | `CacheConfig`, `ConstantRate`, `TrafficEstimator` — prompt-cache pricing modelling. |
| `types.py` | The whole typed message/content/stream/usage vocabulary: `Message`, `History`, `ContentItem` family (`TextItem`, `ImageItem`, `AudioItem`, `FileItem`, `ThinkingItem`, `RedactedThinkingItem`, `ToolCallItem`, `ToolResultItem`, …), `StreamItem` family (`Tick`, `Response`, `ToolCall`, `Reasoning`, `AttemptRecovery`, `StreamCursor`), `Store`, `StreamResult`, `Usage`, `Cost`, `ProviderSpec`, `ProviderModel`, `ModelBinding`, `CallRecord`, plus helpers (`system`/`user`/`assistant`/`tool`/`tools`/`tool_result`, `coerce_tool_output*`, `render_*`, `split_history`, `on_tool_call_name`). |
| `providers/__init__.py` | Lazy registry so importing `yuullm` doesn't require every vendor SDK. |
| `providers/_openai_chat.py` | Shared OpenAI-chat-completion conversion (request shaping, tool-call parsing, streaming). Base for openai / openrouter / aihubmix / deepseek style APIs. |
| `providers/_content.py` | yuullm `ContentItem` ↔ vendor request body conversion shared by chat providers. |
| `providers/openai.py`, `providers/openrouter.py`, `providers/anthropic.py` | Vendor-specific `Provider` implementations. |

## Streaming model (quick reference)

```text
caller builds History = [system(...), user(...), assistant(...), tool_result(...)]
  → YLLMClient.stream(history, tools=...) chooses a Provider via ProviderPool
    → Provider.stream converts ContentItems → vendor request, opens HTTP stream
      → each vendor delta is normalised into a StreamItem (Tick / Response / ToolCall / Reasoning / AttemptRecovery)
        → StreamResult accumulator + Store (full message + usage) returned at end
  → RawChunkHook receives raw vendor chunks for observability/persistence
```

Invariants to preserve:

- `ContentItem`/`StreamItem` are the **only** shapes that cross the
  `YLLMClient`↔caller boundary. Vendor-native dicts live inside `providers/*`.
- A provider implementation must never raise on a transient stream error
  mid-response without surfacing it as `AttemptRecovery` so the client/pool
  can decide failover.
- Token usage/cost is computed by the client from `Usage` + `PricingTable`,
  not by providers.

## Commands

```bash
uv sync                 # from monorepo root
uv run pytest           # from this package directory
uv run pytest tests/test_client.py -v
uv run pytest tests/test_types.py -v
uv run ruff check src/ tests/
uv build                # wheel build
```

## Coding style

Python 3.12+. `msgspec.Struct` for serialized stream types, `TypedDict` for
content items, `from __future__ import annotations`, `snake_case` modules /
`PascalCase` classes. Mock provider SDK calls in tests; do not hit live services.
`scripts/setup-dev.sh` installs git hooks (pre-push checks tag vs pyproject version).

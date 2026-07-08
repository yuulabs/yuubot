# LLM Provider Core

Supports [frontend-migrate/02-provider-llm-onboarding.md](../frontend-migrate/02-provider-llm-onboarding.md)
and [frontend-migrate/03-actor-authoring.md](../frontend-migrate/03-actor-authoring.md).

## Scenario

Admin creates named providers (`deepseek`, `openai`), refreshes model catalogs,
checks balance when the upstream exposes it, and configures model cards. Actor
`amy` binds one provider id plus one `ModelCard` snapshot.

At runtime Conversation calls `Provider.stream` directly. The provider talks to
the vendor, maps the raw completion stream to yuubot `StreamEvent`s, and emits
one terminal `StreamStop` with `usage` and `account`. Vendor differences stay
inside the provider implementation.

## Concepts

**Provider** = named account connection. Owns credentials, catalog ops, balance
query, vendor wire I/O, and conversion to yuubot stream events.

**ProviderProtocol** = registry key (`openai-compatible`, …) → config struct +
provider class.

**ProviderRecord** = durable `llm_providers` row: id, name, protocol, config.

**ModelCard** = selector, capability flags, per-million pricing. Actor stores a
snapshot at selection time.

**ProviderRegistry** = protocol → `ProviderSpec`, same role as
`IntegrationRegistry`.

Model card state: `Discovered` (selector only) → `Configured` (flags + pricing).

## Stored Shape

```text
llm_providers(id, name, protocol, config, updated_at)
model_cards(provider_id, selector, payload=ModelCard, updated_at)
  primary key (provider_id, selector)

ActorRecord.provider: str
ActorRecord.model: ModelCard
```

API keys live in `config`, never in env vars or snapshots.

## Provider Interface

```py
class Provider(Protocol):
    async def list_presets(self) -> list[ModelCard]: ...
    async def list_remote_models(self) -> list[str]: ...
    def merge_catalog(self, presets, remote) -> list[ModelCard]: ...
    async def get_balance(self) -> AccountSnapshot | None: ...
    async def validate(self) -> ValidationResult: ...

    async def stream(
        self,
        input: LLMInput,
        *,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]: ...

    async def close(self) -> None: ...
```

Conversation is the only runtime caller of `stream`. There is no separate
`LLMClient` layer.

### Catalog

- `list_presets`: built-in cards with defaults.
- `list_remote_models`: upstream model list; failures are validation errors.
- `merge_catalog`: remote selectors become name-only cards; presets win on collision.

### Balance

`get_balance` queries the vendor billing API when available; otherwise `None`.
The same logic may run again at stream end or on interrupt.

### Stream

Each provider converts vendor output to yuubot `StreamEvent` internally. Output
contract matches [design/archive/design.md](../archive/design.md#stream-protocol).

```py
async def stream(self, input, *, model, context, cache, stop_event):
    usage = Usage()
    upstream = await self._open(input, model, context, cache)
    try:
        async for chunk in upstream:
            if stop_event.is_set():
                yield stream_stop("interrupted", usage, await self._account())
                return
            yield from self._map_chunk(chunk)
            usage = self._coalesce_usage(usage, chunk)
    finally:
        await upstream.close()

    payg = usage.payg_cost
    cost_estimated = False
    if payg is None and has_tokens(usage):
        payg = estimate_cost(model, usage)
        cost_estimated = True
    yield stream_stop(finish_reason, usage.with_payg(payg), await self._account(), cost_estimated)
```

Provider responsibilities inside `stream`:

- encode multimodal `ContentItem` via `cache` (paths/urls in history, derived
  bytes in cache);
- map vendor chunks → `text_delta`, `tool_name`, …;
- accumulate token usage from vendor;
- set `payg_cost` when vendor reports it, else `estimate_cost(model, usage)`;
- attach `account` from vendor stream or balance query;
- yield exactly one `StreamStop` per call.

`StreamStop` fields: `reason`, `usage`, `account` (`{}` when unavailable),
`cost_estimated`.

## Protocol Registry

```text
ProviderSpec = { config_type, provider_type, default_endpoint }

openai-compatible ->
  OpenAIProviderConfig(endpoint="", api_key, options={})
  OpenAIProvider
  https://api.openai.com/v1
```

`registry.build(ProviderRecord)` decodes config and constructs the provider.

## Core Flows

```py
async def refresh_catalog(provider_id, *, store, registry):
    p = registry.build(await store.load_provider(provider_id))
    merged = p.merge_catalog(await p.list_presets(), await p.list_remote_models())
    for card in merged:
        existing = await store.load_model_card(provider_id, card.selector)
        if existing and is_configured(existing):
            continue
        await store.upsert_model_card(provider_id, card)
    return await store.list_model_cards(provider_id)


async def build_actor_provider(actor, *, store, registry) -> Provider:
    return registry.build(await store.load_provider(actor.provider))


async def conversation_llm_step(conversation, stop_event):
    chunks = [
        e async for e in conversation.provider.stream(
            conversation.history.to_llm_input(),
            model=conversation.context.model,
            context=conversation.context,
            cache=conversation.runtime.cache,
            stop_event=stop_event,
        )
    ]
    outputs, stop = merge(chunks)
    conversation.record_cost(stop.usage, stop.account, estimated=stop.cost_estimated)
    return outputs, stop
```

## Context Access

```text
ProviderRecord, ModelCard catalog <- ApplicationStateStore
ProviderRegistry                  <- injected
CachePool                         <- Runtime.cache (passed into stream)

Admin:  registry.build(record) -> get_balance / refresh_catalog
Runtime: registry.build(record) -> provider.stream(...)
```

Accepted debt: `ActorRecord.model` is a snapshot, not a catalog FK.

## Decisions

- **Only Provider.** Vendor protocol, encoding, usage, account, and stream mapping
  live in one layer. Conversation never branches on protocol.
- **One provider, many models.** Credentials per provider; models are catalog rows.
- **Refresh preserves configured cards.** Never downgrade to name-only.
- **Actor selects configured cards only.** No manual selector on actor form.

## Invariants

- `protocol` must exist in `ProviderRegistry`.
- Credentials never from environment; never in snapshots.
- `stream` yields exactly one `StreamStop`.
- Configured catalog cards survive refresh.

## Facade Surface

Admin HTTP lives under `admin_url_base` with AdminAuth. Error envelope follows
[design/services/02-admin-boundary.md](../services/02-admin-boundary.md). HTTP
never carries LLM token stream; runtime streaming stays on WebSocket
`conversation.stream` frames (see Runtime stream facade below).

Resource id in every path is `provider_id` — the durable `llm_providers.id`.
Frontend route `/providers` maps to these `/api/providers/*` endpoints.

### Wire Types

```py
class ProviderInput(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    protocol: str
    config: dict[str, object]          # protocol-specific; includes api_key on write

class ProviderSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    protocol: str
    configured: bool
    last_error: str | None = None
    model_count: int = 0
    configured_model_count: int = 0

class ProviderProtocolSpec(msgspec.Struct, frozen=True, kw_only=True):
    protocol: str
    title: str
    default_endpoint: str
    config_schema: dict[str, object]
    secret_fields: tuple[str, ...]    # e.g. ("api_key",)

class ModelCardInput(msgspec.Struct, frozen=True, kw_only=True):
    selector: str
    vision: bool = False
    toolcall: bool = True
    json: bool = True
    input_price_per_million: float = 0
    cached_input_price_per_million: float = 0
    output_price_per_million: float = 0

class ValidationResult(msgspec.Struct, frozen=True, kw_only=True):
    ok: bool
    message: str = ""
    detail: dict[str, object] = msgspec.field(default_factory=dict)

class AccountSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    balance: float | None = None
    currency: str | None = None
    raw: dict[str, object] = msgspec.field(default_factory=dict)
```

`ModelCardInput` maps 1:1 to durable `ModelCard`. A card is **configured** when
any of `vision`, `toolcall`, `json`, or pricing fields differ from protocol
defaults, or when explicitly saved via `PUT .../model-cards/{selector}`.

Responses that include provider `config` return a **redacted** view: values for
`secret_fields` are replaced with `"***"`. Plaintext secrets never appear in
bootstrap, list, or get responses.

### HTTP Routes

```http
GET  /api/provider-protocols
GET  /api/providers
GET  /api/providers/{provider_id}
PUT  /api/providers/{provider_id}
DELETE /api/providers/{provider_id}
POST /api/providers/{provider_id}/validate
GET  /api/providers/{provider_id}/balance
POST /api/providers/{provider_id}/catalog/refresh
GET  /api/providers/{provider_id}/model-cards
PUT  /api/providers/{provider_id}/model-cards/{selector}
DELETE /api/providers/{provider_id}/model-cards/{selector}
```

Actor binding uses existing actor config HTTP; field rename only:

```http
PUT /api/actors/{actor_id}
```

`ActorInput.provider` replaces legacy `llm`. `ActorInput.model` remains a full
`ModelCard` snapshot. Actor PUT rejects `model.selector` that is not a
**configured** card under the bound `provider`.

### Facade: `GET /api/provider-protocols`

```text
Entrypoint: GET /api/provider-protocols
Input protocol: no body.
Context collection: ProviderRegistry.specs().
Core call: list registered protocol specs.
Output protocol: 200 { "items": [ProviderProtocolSpec, ...] }.
Error protocol: 500 internal_error.
Persistence: read-only.
Compatibility: new protocols append items; unknown protocol on saved provider
  returns 422 on validate/enable paths, not here.
```

Example item:

```json
{
  "protocol": "openai-compatible",
  "title": "OpenAI-compatible",
  "default_endpoint": "https://api.openai.com/v1",
  "config_schema": {
    "type": "object",
    "properties": {
      "endpoint": { "type": "string" },
      "api_key": { "type": "string" },
      "options": { "type": "object" }
    },
    "required": ["api_key"]
  },
  "secret_fields": ["api_key"]
}
```

### Facade: `GET /api/providers`

```text
Entrypoint: GET /api/providers
Input protocol: no body.
Context collection: ApplicationStateStore.list_providers(); per-provider
  model_cards counts; runtime last_error cache if present.
Core call: build ProviderSnapshot per row (no secrets).
Output protocol: 200 { "items": [ProviderSnapshot, ...] }.
Error protocol: 500 internal_error.
Persistence: read-only.
Compatibility: items may gain summary fields; id and protocol are stable.
```

### Facade: `GET /api/providers/{provider_id}`

```text
Entrypoint: GET /api/providers/{provider_id}
Input protocol: path provider_id.
Context collection: ProviderRecord; model_cards list; runtime validation state.
Core call: load provider + redacted config + cards.
Output protocol: 200 {
  "id", "name", "protocol", "config": <redacted>,
  "configured", "last_error",
  "model_cards": [ModelCard, ...]
}.
Error protocol: 404 not_found; 500 internal_error.
Persistence: read-only.
Compatibility: config redaction shape is stable; config keys follow protocol
  schema extensions.
```

### Facade: `PUT /api/providers/{provider_id}`

```text
Entrypoint: PUT /api/providers/{provider_id}
Input protocol: JSON ProviderInput; path id wins over any body id.
Context collection: ProviderRegistry; existing ProviderRecord (merge secrets:
  omitted secret_fields keep stored values); AuthContext.
Core call: decode config via registry.build partial validation → upsert
  ProviderRecord → drop cached provider instance → optional background validate.
Output protocol: 200 full bootstrap snapshot (providers replace legacy llms
  array; see Bootstrap).
Error protocol: 400 bad_request (unknown protocol, schema violation);
  404 not_found only when PUT targets missing id with create=false semantics —
  v1 treats PUT as upsert, so 404 is not used; 500 internal_error.
Persistence: llm_providers row; api_key inside config JSON; never env vars.
Compatibility: new config keys pass through options; protocol string is registry
  key; changing protocol on existing id is rejected with 409 conflict.
```

Request body:

```json
{
  "name": "DeepSeek",
  "protocol": "openai-compatible",
  "config": {
    "endpoint": "https://api.deepseek.com",
    "api_key": "sk-...",
    "options": {}
  }
}
```

Secret merge rule: if `api_key` is `"***"` or absent on PUT, retain the stored
value. Empty string clears the key.

### Facade: `DELETE /api/providers/{provider_id}`

```text
Entrypoint: DELETE /api/providers/{provider_id}
Input protocol: path provider_id.
Context collection: actors referencing provider_id; model_cards rows.
Core call: reject when actors still bind provider; else delete provider +
  cascade model_cards + close runtime provider instance.
Output protocol: 200 bootstrap snapshot.
Error protocol: 404 not_found; 409 conflict (actor references);
  500 internal_error.
Persistence: delete llm_providers and child model_cards.
Compatibility: bootstrap.providers omits deleted id.
```

### Facade: `POST /api/providers/{provider_id}/validate`

```text
Entrypoint: POST /api/providers/{provider_id}/validate
Input protocol: no body.
Context collection: ProviderRecord; ProviderRegistry; ApplicationStateStore.
Core call: registry.build(record) → provider.validate() (credential probe +
  optional list_remote_models smoke).
Output protocol: 200 ValidationResult; updates durable last_error on failure.
Error protocol: 404 not_found; 503 provider_unavailable (upstream unreachable);
  500 internal_error.
Persistence: writes last_error on ProviderRecord or sidecar validation column;
  does not mutate model_cards.
Compatibility: detail dict may gain vendor-specific keys.
```

### Facade: `GET /api/providers/{provider_id}/balance`

```text
Entrypoint: GET /api/providers/{provider_id}/balance
Input protocol: no body.
Context collection: built Provider instance.
Core call: provider.get_balance().
Output protocol: 200 AccountSnapshot when vendor exposes billing; 200 null body
  or { "available": false } when protocol returns None.
Error protocol: 404 not_found; 503 provider_unavailable; 500 internal_error.
Persistence: none (live query).
Compatibility: raw vendor fields stay inside raw; top-level balance/currency are
  stable optional fields.
```

### Facade: `POST /api/providers/{provider_id}/catalog/refresh`

```text
Entrypoint: POST /api/providers/{provider_id}/catalog/refresh
Input protocol: no body.
Context collection: store, registry, ProviderRecord.
Core call: refresh_catalog(provider_id, store=..., registry=...) from Core Flows.
Output protocol: 200 { "model_cards": [ModelCard, ...] } full post-refresh
  catalog for this provider.
Error protocol: 404 not_found; 503 provider_unavailable (list_remote_models
  failure); 500 internal_error.
Persistence: upserts discovered cards; never downgrades configured cards.
Compatibility: new selectors appear; configured selectors keep flags and pricing.
```

### Facade: model card CRUD

```text
Entrypoint: GET  /api/providers/{provider_id}/model-cards
           PUT  /api/providers/{provider_id}/model-cards/{selector}
           DELETE /api/providers/{provider_id}/model-cards/{selector}
Input protocol: PUT body = ModelCardInput; selector in path URL-encoded.
Context collection: parent ProviderRecord must exist; selector uniqueness per
  provider_id.
Core call: GET → store.list_model_cards; PUT → upsert configured ModelCard;
  DELETE → remove row (reject if any actor snapshot references selector under
  this provider).
Output protocol: GET 200 { "items": [ModelCard] }; PUT 200 single ModelCard;
  DELETE 200 bootstrap snapshot.
Error protocol: 400 bad_request; 404 not_found; 409 conflict (actor still uses
  card); 500 internal_error.
Persistence: model_cards table.
Compatibility: ModelCard may gain capability flags; actors keep their snapshot
  until edited.
```

Actor forms load configured cards via
`GET /api/providers/{provider_id}/model-cards` and filter to configured rows
only (pricing or flags set, or explicitly PUT).

### Facade: bootstrap providers slice

`GET /api/bootstrap` replaces the legacy `llms` array with `providers`:

```json
{
  "providers": [
    {
      "id": "deepseek",
      "name": "DeepSeek",
      "protocol": "openai-compatible",
      "configured": true,
      "last_error": null,
      "model_count": 8,
      "configured_model_count": 3
    }
  ],
  "actors": [
    {
      "id": "amy",
      "provider": "deepseek",
      "model": { "selector": "deepseek-chat", "toolcall": true }
    }
  ]
}
```

```text
Entrypoint: GET /api/bootstrap (providers slice only)
Input protocol: none.
Context collection: ApplicationStateStore providers + validation state; actor
  records with provider id + model snapshot summary.
Core call: bootstrap_snapshot — merge durable rows with runtime status.
Output protocol: providers[] as ProviderSnapshot; actors[].provider + model
  summary; no secrets.
Error protocol: 500 internal_error.
Persistence: read-only.
Compatibility: schema_version bump when legacy llms key is removed; frontends
  must read providers, not llms. Actor field provider replaces llm in the same
  schema_version bump.
```

### Facade: actor provider binding

```text
Entrypoint: PUT /api/actors/{actor_id}
Input protocol: ActorInput with provider: str and model: ModelCard.
Context collection: ProviderRecord exists; model_cards configured row matches
  model.selector; registry for tools unchanged.
Core call: upsert ActorRecord(provider, model snapshot) → enable actor →
  build_actor_provider on next conversation.
Output protocol: 200 bootstrap snapshot.
Error protocol: 400 bad_request; 422 configuration_required (unknown provider,
  unconfigured model selector); 503 provider_unavailable; 500 internal_error.
Persistence: ActorRecord.provider + ActorRecord.model snapshot.
Compatibility: reject body field llm with 400; migration maps llm → provider
  once on import only.
```

### Facade: runtime stream (internal boundary)

`Provider.stream` is not a public HTTP or WebSocket entrypoint. External
callers use conversation commands:

```text
Entrypoint: WebSocket command conversation.send (admin /api/ws)
Input protocol: { conversation_id, actor_id, message }.
Context collection: Conversation loads actor → build_actor_provider(actor) →
  model from ActorRecord.model snapshot; Runtime.cache; stop_event from
  conversation.interrupt or TTL.
Core call: conversation_llm_step → provider.stream(...).
Output protocol: WS frames type=conversation.stream with StreamEvent payloads;
  terminal frame kind=stream_stop carrying reason, usage, account,
  cost_estimated. If the stop reason requests tool execution, Harness emits
  conversation.stream tool_result_delta/tool_result_end events for process
  output and final ToolResult content, followed by history append for durable
  ToolResult rows and conversation.tool_results as the batch notification.
Error protocol: WS error frame conversation_busy; provider failures become
  stream_stop with reason=stop and last_error on conversation record.
Persistence: cost from StreamStop.usage/account; history items from merged
  outputs.
Compatibility: new StreamEvent kinds may append; stream_stop fields are
  backward-compatible extensions.
```

### CLI

No dedicated provider CLI in v1. Operators use admin HTTP or bootstrap JSON.
`ybot chat` resolves the actor's `provider` id from durable state; it does not
accept inline provider credentials.

### File locations (boundary-visible)

```text
{data_dir}/state.db
  llm_providers(id, name, protocol, config, updated_at, last_error?)
  model_cards(provider_id, selector, payload, updated_at)

ActorRecord.provider → llm_providers.id
ActorRecord.model    → snapshot; not a FK to model_cards
```

`config.yaml` does not hold provider credentials or model catalogs.

### Extension points

| Boundary | Extension | Rejected at boundary |
| --- | --- | --- |
| New vendor | Register `ProviderSpec` in `ProviderRegistry` | Unknown `protocol` on PUT |
| New config field | `config.options` or protocol config_schema | Ad-hoc top-level ProviderInput fields |
| New model capability | New optional `ModelCard` flag | Changing selector semantics |
| New admin action | New POST under `/api/providers/{id}/...` | Provider-specific routes outside namespace |
| Stream mapping | Inside provider implementation | Conversation branching on protocol |

## Out of Scope

- Migration from `app_llms`, budget enforcement, Anthropic impl,
  first-provider onboarding dialog.

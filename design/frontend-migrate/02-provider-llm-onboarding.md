# Phase 2: Provider and LLM Onboarding

## Goal

Replace the current legacy LLM JSON editor with a guided provider setup flow
backed by the implemented provider core contract.

Current issue: `web/src/features/providers/provider-detail-page.tsx` and
`web/src/features/providers/providers-list-page.tsx` still read `data.llms`,
call legacy `putLlm` / `deleteLlm`, and write raw `LlmConfig` JSON. The backend
now exposes providers through `GET /api/provider-protocols`,
`/api/providers/*`, provider-scoped model cards, and actor records with
`provider` plus a `ModelCard` snapshot.

## Product Behavior

### Provider setup flow

The page should support:

- Protocol specs from `GET /api/provider-protocols`. Current v1 registers
  `openai-compatible`.
- UI presets for OpenAI, DeepSeek, and custom OpenAI-compatible. Presets are
  frontend defaults, not backend protocol names. OpenAI and DeepSeek both save
  `protocol: "openai-compatible"` with different endpoint/model defaults.
- Provider id and display name fields saved through
  `PUT /api/providers/{provider_id}` as `ProviderInput`.
- Base URL field with normalization preview:
  - blank means provider default;
  - custom endpoint removes trailing `/chat/completions` in backend;
  - UI should warn on malformed URLs before save.
- API key field that writes `config.api_key`. Detail/bootstrap responses never
  expose plaintext secrets; redacted `"***"` or omitted secret fields retain the
  stored value on update, and an empty string clears it.
- Advanced options editor for `config.options`.
- Validate, balance, and catalog refresh actions backed by provider endpoints.
- Historical providers (`anthropic`, `openrouter`, custom) shown only when a
  matching backend protocol exists, or represented as custom OpenAI-compatible
  where technically correct.

### Validation

Use the implemented backend validate action:

- Save provider config with `PUT /api/providers/{provider_id}`.
- Run `POST /api/providers/{provider_id}/validate` when the user requests a
  credential probe.
- Show `ValidationResult` immediately and keep rendering provider `last_error`
  from bootstrap/detail snapshots.
- UI must distinguish frontend URL/schema validation from real provider
  credential/model validation.

### Model catalog and pricing

Expose provider-scoped `ModelCard` rows used by actors:

- selector;
- vision/toolcall/json flags;
- input/cached-input/output price per million.

The provider config does not store a selected model. Model choices live in
`model_cards(provider_id, selector, payload)` and actors store
`model: ModelCard` as a snapshot.

Catalog behavior:

- Refresh catalog through
  `POST /api/providers/{provider_id}/catalog/refresh`.
- Load cards from provider detail or
  `GET /api/providers/{provider_id}/model-cards`.
- Save card edits through
  `PUT /api/providers/{provider_id}/model-cards/{selector}`.
- Remote selectors become name-only cards; built-in presets carry capability
  flags and pricing; refresh preserves configured cards.
- Actor forms should offer configured cards for the selected provider and submit
  the chosen card snapshot.

The UI should avoid implying provider-level pricing applies to actors unless the
actor form uses the same saved `ModelCard` snapshot.

### Budgets

Historical UI had daily/monthly budget fields. Provider core does not include a
budget enforcement contract.

Implementation options:

1. Store budget policy under `ProviderInput.config.options.budget` as a
   transitional frontend convention.
2. Add backend first-class budget fields before exposing a durable UI.
3. Keep budget fields read-only/planned in the UI until backend contract exists.

Preferred plan: do not silently bury budgets in `options` unless the backend and cost enforcement layer agree on that contract. Track as backend contract work if enforcement is expected.

## Preset Actor Onboarding

Restore the old "first provider creates preset actors" flow on top of provider
core:

- Detect first configured provider before create/save.
- After successful provider save and model card setup, prompt to create preset
  actors.
- Presets must bind to the newly created provider id, not `data.providers[0]`.
- Preset actor payloads should use the new `ActorRecord` shape:
  - `provider`;
  - `model: ModelCard`;
  - `persona`;
  - `tools` or later capability/tool preset mapping;
  - `workspace` if a preset needs one.

Actor PUT rejects deprecated `llm`; the frontend must submit `provider`.
Because old stable preset ids referenced capability-set ids, this phase depends
on the Phase 5 decision for full capability parity. Before that decision, create
only safe actor presets whose `tools` map is valid in the new backend.

## Implementation Steps

1. Replace legacy LLM API client calls with provider core clients:
   `provider-protocols`, provider CRUD, validate, balance, catalog refresh, and
   model-card CRUD.
2. Update bootstrap types and provider pages to read `data.providers`, not
   `data.llms`.
3. Create provider preset metadata in shared frontend code. Presets map to
   `ProviderInput` defaults and never invent backend protocol names.
4. Replace the provider detail JSON-first page with structured fields plus an
   advanced JSON editor for `config.options`.
5. Add model card catalog refresh, model card editing, and configured-card
   selection for actor forms.
6. Add first-provider onboarding dialog and preset actor creation using
   `provider` plus `ModelCard`.
7. Keep a raw JSON escape hatch only for unsupported provider `options`, not for
   the whole provider record.

## Tests

- Source or component tests for provider preset selection and id generation.
- API client tests for `PUT /api/providers/{provider_id}`, provider validate,
  catalog refresh, and model-card CRUD payloads.
- Workflow test markers for first-provider detection and preset actor creation.
- Regression test that provider pages do not call `/api/llms` or read
  `data.llms`.
- Regression test that preset actors bind to the new provider id and submit
  `provider`, not `llm`.

## Remaining Backend Constraints

- Provider core endpoints, validation, catalog refresh, model-card CRUD, and
  actor `provider` binding exist.
- Current v1 ships `openai-compatible` as the protocol registry entry. DeepSeek
  and OpenAI are frontend presets over that protocol.
- No explicit budget enforcement contract exists.
- Native Anthropic/OpenRouter support requires new registered protocols before
  being advertised as first-class providers.
- Balance may be unavailable for a protocol and returns `{ "available": false }`.

## Acceptance Criteria

- A user can create an OpenAI-compatible, OpenAI, or DeepSeek provider without
  editing raw JSON.
- The saved provider round-trips through bootstrap as `providers[]` with redacted
  secrets and correct configured status.
- The user can validate credentials, refresh the catalog, and configure model
  cards from the provider page.
- The UI does not advertise Anthropic/OpenRouter as working providers until backend support exists.
- First-provider onboarding can create valid actors bound to the created
  provider and a configured `ModelCard` snapshot.
- Provider UI no longer depends on `LlmConfig`, `data.llms`, or `/api/llms`.


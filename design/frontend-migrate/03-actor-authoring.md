# Phase 3: Actor Authoring and Detail Pages

## Goal

Restore Actor management as a structured workflow: create, edit, inspect, enable/disable, open workspace, inspect history, and start conversation.

Current issue: `web/src/features/actors/actor-edit-page.tsx` reconstructs an `ActorRecord` from snapshot data and hardcodes `llm` to `data?.llms[0]?.id`. That can corrupt existing actor bindings.

## Data Contract

The new backend `ActorRecord` supports:

```py
id: str
name: str
description: str
workspace: str
persona: str
model: ModelCard
llm: str
tools: dict[str, dict[str, object]]
```

The bootstrap `ActorSnapshot` is not enough to rebuild a full `ActorRecord` because it currently flattens `model` and `tools`, and does not expose `persona` or `llm`. Actor edit needs one of:

1. Backend exposes full actor records in a detail endpoint.
2. Bootstrap actor snapshots are expanded to include full editable fields.
3. Frontend keeps actor creation/editing state only for actors it created, which is not acceptable for durable admin.

Preferred plan: add a backend actor detail/full-record endpoint or expand bootstrap before replacing JSON editor fully.

## Structured Editor

The actor editor should expose:

- Identity: id, name, description.
- Runtime toggle: enabled/disabled action.
- LLM binding: select LLM by id with configured/error status.
- Model card:
  - selector;
  - vision/toolcall/json flags;
  - pricing fields.
- Persona/system prompt textarea.
- Workspace path.
- Tool scope:
  - current backend: edit `tools` map with known tool presets plus advanced JSON.
  - after Phase 5: optional capability/tool preset binding if a backend model exists.
- Budget controls only when backend enforcement contract exists.

## Actor Detail

The detail page should become the main launch surface:

- Status, enabled state, last error.
- Bound LLM and model.
- Tool/capability summary.
- Workspace panel entry.
- KV panel entry.
- Inbound test form.
- Recent conversations filtered by `actor_id`.
- Primary "Start conversation" action that routes to an actor-bound draft conversation.

## Preserving Bindings

Do not derive editable records from lossy snapshots. Until full actor records are available:

- Keep JSON editor labeled as limited.
- Avoid saving an edit payload that overwrites unknown `llm`, `persona`, `model`, or `tools`.
- Prefer blocking edit with a clear backend-contract message over silently writing incorrect data.

## Implementation Steps

1. Add/confirm full actor record read contract.
2. Update frontend types to separate `ActorSnapshot` from `ActorRecord`.
3. Rework new/edit pages around structured form state.
4. Add actor enable/disable/delete actions with bootstrap refresh.
5. Add actor detail panels for inbound, workspace, KV, and conversations as links or embedded summaries.
6. Move preset actor sync/update action into Actors list or Provider onboarding, using shared preset payload helpers.

## Tests

- Actor edit preserves `llm` binding from loaded record.
- Actor edit round-trips `persona`, `model`, `workspace`, and `tools`.
- Actor detail links to `/admin/conversations/new?actor={actorId}`.
- Actor detail filters conversations by `actor_id`.
- Enable/disable actions call the correct endpoints and refresh bootstrap.

## Backend Gaps

- Full editable actor record is not currently visible in `ActorSnapshot`.
- Budget and skill-scope concepts need current backend equivalents.
- Capability Set relationship is unresolved; current backend has `tools`, not a capability-set resource.

## Acceptance Criteria

- Editing an actor cannot accidentally bind it to the first LLM in bootstrap.
- A user can create a functional actor without writing JSON.
- Actor detail becomes the obvious place to inspect and start using an actor.


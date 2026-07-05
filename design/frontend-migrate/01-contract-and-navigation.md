# Phase 1: Contract and Navigation Baseline

## Goal

Make the frontend's shared API layer, route tree, labels, and type definitions accurately represent the backend surface before larger UI restoration starts.

This phase is intentionally small but blocking: later pages should use typed client functions rather than inventing request shapes inside components.

## Scope

### API client coverage

Add or verify shared client functions under `web/src/shared/lib/api` for:

| Capability | Required client functions |
| --- | --- |
| Routes | `listRoutes`, `createRoute`, `updateRoute`, `deleteRoute` |
| Actor inbound | `sendActorInbound(actorId, body)` |
| Actor workspace | `browseActor`, `getActorFileUrl`, `uploadActorFile` |
| Actor KV | `getActorKv`, `putActorKv`, `deleteActorKv` with ETag support |
| Tasks | `listTasks`, `getTask`, `cancelTask`; submit remains loopback/admin-gated and can be hidden or guarded |
| Conversation costs/delete | `getConversationCosts`, `deleteConversation` |
| Integrations | detail/config/enable/disable client functions with typed config wrapper |

### Type coverage

Extend `web/src/shared/types/api.ts` for:

- `ActorInboundBody` and response.
- `WorkspaceDirectorySnapshot`, file entries, upload response.
- `KvDocument`, `KvPutBody`, ETag-bearing client response.
- `TaskRecord` detail including `stdout` and `error`.
- `ConversationCostRecord` once the backend shape is confirmed from stored cost snapshots.
- Integration config schema helper types.

### Navigation and route corrections

- Stop mapping `/capability-sets` to "Shares" in `web/src/features/shell/app-layout.tsx`.
- Decide the public route for Shares. Prefer `/shares` if route tree churn is acceptable; otherwise label the current temporary path explicitly as a migration debt and do not call it Capability Sets.
- Keep `/capability-sets/new` and `/capability-sets/$id/edit` disabled or redirected only with an explicit "backend resource not available" migration note until Phase 5 decides the model.
- Ensure route modules and labels match user mental models: Actors, LLMs, Integrations, Routes, Conversations, Runtime, Tasks, Shares, Settings.

### Known bug to fix early

`POST /api/actors/{actor_id}/uploads` expects form field name `file`:

```py
async def api_upload_actor(actor_id: str, file: list[UploadFile] = File(...))
```

The frontend upload helper must append each file under `file`, not `files`.

## Dependencies

- Backend route surface in `src/yuubot/web/routes/admin.py`.
- Service docs in `design/services`, especially tasks, inbound, shares, and KV.

## Deliverables

- Typed API client functions for all backend capabilities that later phases need.
- Route/nav label correction, with Shares no longer masquerading as Capability Sets.
- One small API test file or existing API test extension covering:
  - upload form field name `file`;
  - `PUT /api/routes/{route_id}`;
  - KV `If-Match` header and returned ETag handling;
  - actor inbound request path.

## Acceptance Criteria

- Components can import all required operations from `@/shared/lib/api`.
- No page needs to manually build a URL for actor workspace, KV, route update, task cancel, or actor inbound.
- `/capability-sets` no longer silently renders Shares under a false label.
- Existing JSON editor pages still work after the client/type refactor.

## Non-goals

- No large visual redesign.
- No field-based provider or actor forms yet.
- No attempt to recreate old `capability-sets` backend contract in frontend only.


> **已过时**：这是历史迁移计划，仅供追溯，不得作为当前实现依据。当前权威设计见
> [`design/system-design.md`](../../system-design.md)。

# Phase 6: Workspace, KV, Tasks, and Runtime Operations

## Goal

Expose the backend operational capabilities that make actors useful beyond static configuration: workspace files, uploads, KV state, tasks, runtime status, and shares.

## Actor Workspace

Backend surface:

- `GET /api/actors/{actor_id}/browse?path=...`
- `GET /api/actors/{actor_id}/files/{file_path}`
- `POST /api/actors/{actor_id}/uploads`

Required UI:

- File browser rooted at actor workspace.
- Breadcrumb navigation.
- File preview/download links.
- Upload button using form field name `file`.
- Optional "share this path" action that pre-fills the Shares create flow.
- Clear handling for actors without workspace.

Tests:

- Browse path URL encodes correctly.
- Upload appends files under `file`.
- Share action passes actor id and source path.

## Actor KV

Backend surface:

- `GET /api/actors/{actor_id}/kv/{key}`
- `PUT /api/actors/{actor_id}/kv/{key}`
- `DELETE /api/actors/{actor_id}/kv/{key}`

Required UI:

- Actor detail KV panel.
- Key input and JSON value editor.
- ETag display and optimistic update handling with `If-Match`.
- Conflict state that offers reload/overwrite choices.
- Delete action.

Backend limitation: there is no list-keys endpoint. A usable browser requires either:

1. Add `GET /api/actors/{actor_id}/kv` to list keys.
2. Keep v1 as direct key lookup/edit only.

Preferred plan: implement direct key lookup first, then add list endpoint if KV becomes a primary admin workflow.

## Tasks

Backend surface:

- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/tasks` is loopback-only submit and should not be a normal remote admin button.

Required UI:

- Tasks list page or runtime subtab.
- Filters by owner and name glob.
- Task detail drawer/page with stdout, status, error, timestamps.
- Cancel action for pending/running tasks.
- Link owner `actor:{id}:conv:{conversation_id}` to actor and conversation when parseable.

Tests:

- Cancel calls `POST /api/tasks/{id}/cancel`.
- Detail view requests stdout-inclusive endpoint.
- Owner parser links actor/conversation only when safe.

## Runtime and Shares

Current Runtime and Shares pages exist but should be integrated with the new operations model:

- Runtime page should show listener/events/task/actor health in compact operational panels.
- Shares page should live at a truthful route/label, ideally `/shares`.
- Workspace file browser should be able to create a share for a selected path.
- Share revoke should refresh list and show revoked/expired state distinctly.

## Dynamic Page Workflow

KV, workspace, shares, and actor inbound together support the dynamic page pattern described in `design/services/06-kv.md`:

```text
LLM writes HTML to workspace
  -> admin opens it in workspace browser
  -> page JavaScript writes KV
  -> page POSTs actor inbound
  -> actor wakes and continues the conversation
```

The UI should make this workflow discoverable through concrete controls, not explanatory copy:

- workspace preview/open;
- KV key editor;
- inbound test/send panel;
- share static snapshot when public read-only access is needed.

## Implementation Steps

1. Add workspace browser to Actor detail.
2. Add upload and file preview/download.
3. Add direct KV key editor with ETag conflict handling.
4. Add Tasks page or Runtime tab with detail/cancel.
5. Connect workspace selected path to Shares create.
6. Polish Runtime and Shares labels/routes after Phase 1 nav correction.

## Acceptance Criteria

- A user can browse and upload files for an actor.
- A user can read/write/delete a known KV key and recover from ETag conflict.
- A user can inspect and cancel runtime tasks.
- Shares are reachable under a correct label and can be created from workspace paths.

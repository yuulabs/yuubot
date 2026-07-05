# Phase 5: Routes, Integrations, and Capability Model

## Goal

Restore the resource-management pages around external entry points and tool/capability selection.

This phase groups three related areas because they determine how messages enter actors and which tools actors can use:

- Routes/ingress rules.
- Integration configuration.
- Capability Sets or their new-backend replacement.

## Routes

Current issue: Routes page only creates and deletes records, even though the backend has `PUT /api/routes/{route_id}` and `RouteRecord.enabled`.

### Required UI

- Table or dense list of route records.
- Inline create row or modal.
- Edit existing route:
  - id;
  - integration type;
  - pattern;
  - actor;
  - enabled.
- Enable/disable toggle using `PUT`.
- Filter by integration and actor.
- Visual rule summary: integration/source pattern -> actor.
- Delete with confirmation.

### Tests

- `updateRoute` calls `PUT /api/routes/{route_id}`.
- Toggle changes `enabled` without losing other fields.
- Filters by actor and integration are applied.
- Empty state does not hide the create path.

## Integrations

Current issue: list supports enable/disable, but detail is JSON config only.

### Required UI

- Schema-driven config form from `IntegrationSnapshot.config_schema`.
- Preserve advanced JSON editor for schemas the form renderer cannot express.
- Validate required fields client-side when schema allows.
- Show configured/enabled/last_error distinctly.
- Enable/disable actions on detail page.
- Inbound/webhook test entry:
  - for app-level debug path `POST /api/inbound/{integration_type}` if kept for admin testing;
  - for actor-level direct test, link to Actor inbound panel instead.
- Show related routes filtered by integration type.

### Backend gaps

- Integration detail snapshot currently includes config schema but not necessarily current config. Confirm whether detail endpoint returns saved config safely, with secrets redacted.
- Webhook public path in service docs is `/webhooks/app/{integration_type}`, while current admin route includes historical `/api/inbound/{integration_type}`. UI should not present the historical debug path as the production public webhook contract.

## Capability Sets Decision

Current issue: `/capability-sets` now renders Shares and `new/edit` routes are redirect/empty shells. Historical frontend had independent capability-set pages backed by endpoints such as `capability-sets` and `live-capabilities`; the current backend exposes actor `tools` instead.

### Decision required before implementation

Choose one model:

| Option | Meaning | Tradeoff |
| --- | --- | --- |
| Restore backend resource | Reintroduce durable `CapabilitySetRecord` plus live capability discovery | Best parity with historical UI; more backend work |
| Tool presets only | Keep actor `tools` as the durable field and add frontend presets | Smaller backend scope; old `/capability-sets` semantics do not return |
| Hybrid | Tool preset resources compile into actor `tools` | Good admin UX; needs a clear compile/ownership model |

Preferred plan: do not build fake Capability Sets on top of frontend-only state. Pick backend-backed resources or rename the UI to Tool Presets.

### If backend resource is restored

Pages to implement:

- `/capability-sets`: browse/search/delete/duplicate.
- `/capability-sets/new`: form with live capability tree.
- `/capability-sets/{id}/edit`: form with selected capabilities.
- Actor editor field to bind a capability set or import its tools.

### If tool presets are chosen

Pages to implement:

- `/tool-presets`: browse frontend/backend preset definitions.
- Actor editor tool section with preset apply/reset.
- No `/capability-sets` label unless the resource exists.

## Implementation Steps

1. Complete Routes edit/toggle/filter UI.
2. Upgrade Integration detail to schema-driven forms.
3. Resolve Capability Sets vs Tool Presets with backend contract.
4. Restore route modules and nav labels according to the chosen model.
5. Add tests for old high-value source contracts adapted to new names and endpoints.

## Acceptance Criteria

- Routes can be created, edited, enabled/disabled, filtered, and deleted.
- Integration config can be edited without raw JSON for common schemas.
- The UI no longer has a false Capability Sets page.
- Capability/tool selection has a backend-backed durable story.


# Frontend Migration Plan

本目录拆分当前管理面前端能力补全计划。目标不是复刻旧 UI 代码，而是在新后端 contract 下恢复可用的管理工作流，并把 JSON-only 页面逐步替换为字段化、可验证、可测试的页面。

## Current Backend Surface

当前后端管理面已提供这些资源能力：

| Resource | Backend surface | Current frontend state |
| --- | --- | --- |
| LLMs | `PUT/DELETE /api/llms/{id}` via bootstrap snapshot | JSON editor only |
| Actors | `PUT/DELETE /api/actors/{id}`, enable/disable, inbound | JSON editor only; edit path loses actual `llm` binding |
| Integrations | list/detail/config/enable/disable | list can enable/disable; detail JSON editor only |
| Conversations | list/detail/history/costs/delete + `/api/ws` | raw history + raw WS event panel |
| Routes | `GET/POST/PUT/DELETE /api/routes` | create/delete only |
| Runtime tasks | `GET /api/tasks`, detail, cancel, loopback submit | list only through runtime summary |
| Shares | `GET/POST/DELETE /api/shares` | basic page exists |
| Actor workspace | browse/file/upload | API pieces exist; no meaningful UI; upload field mismatch risk |
| Actor KV | `GET/PUT/DELETE /api/actors/{id}/kv/{key}` | no frontend entry |
| Actor inbound | `POST /api/actors/{id}/inbound` | no actor detail/test entry |

Relevant source anchors:

- Backend route registration: `src/yuubot/web/routes/admin.py`.
- Actor durable record: `src/yuubot/domain/records.py`.
- Provider implementation boundary: `src/yuubot/llm/client.py`.
- Current shell nav: `web/src/features/shell/app-layout.tsx`.
- Current JSON-first pages: `web/src/features/providers/provider-detail-page.tsx`, `web/src/features/actors/actor-edit-page.tsx`, `web/src/features/integrations/integration-detail-page.tsx`.

## Migration Principles

- Build on the new backend resource names first. Old names such as `llm-backends`, `ingress-rules`, `live-capabilities`, `preset-actors`, and `actor-skills` are historical references, not contracts to reintroduce blindly.
- Keep API client and type coverage ahead of page work. A page should not hand-roll fetch calls when a shared client function belongs in `web/src/shared/lib/api`.
- Replace JSON editors only where the data shape is stable enough to field. Keep an advanced JSON escape hatch for `options`, provider-specific config, and integration schemas.
- Preserve true bindings. Actor edit must round-trip the stored `llm`, `model`, `persona`, `tools`, and `workspace` instead of reconstructing from snapshots.
- Make conversation entry actor-first. The primary human workflow is "open actor, start or resume a conversation", with the conversation id switching from draft to real id once the backend reports it.
- Treat Capability Sets as a product decision point. The current backend `ActorRecord.tools` model does not expose the old independent `capability-sets` resource. Do not fake the old route without deciding whether to restore a backend resource or map it to actor tool presets.

## Phase Order

| Phase | File | Outcome |
| --- | --- | --- |
| 1 | [01-contract-and-navigation.md](01-contract-and-navigation.md) | Frontend API/types/nav match the backend surface; broken route labels and upload field mismatch are tracked/fixed first. |
| 2 | [02-provider-llm-onboarding.md](02-provider-llm-onboarding.md) | LLM/provider setup becomes a guided flow with presets, validation hooks, model config, budget fields, and preset actor onboarding. |
| 3 | [03-actor-authoring.md](03-actor-authoring.md) | Actor create/edit/detail become structured resource pages that preserve bindings and expose runtime affordances. |
| 4 | [04-conversation-workflow.md](04-conversation-workflow.md) | Actor-to-conversation workflow becomes usable: draft flow, id reconciliation, attachments, transcript rendering, costs, and controls. |
| 5 | [05-resources-routes-integrations-capabilities.md](05-resources-routes-integrations-capabilities.md) | Routes, integrations, and capability/tool grouping get full CRUD-level UI and a clear decision for the missing capability sets model. |
| 6 | [06-workspace-kv-tasks-runtime.md](06-workspace-kv-tasks-runtime.md) | Workspace browser/upload, actor KV, tasks, runtime, and shares become a coherent operations surface. |

## Global Acceptance Bar

Each implementation phase should include:

- Focused frontend tests for the restored workflow markers and critical state transitions.
- API client tests for request paths, request bodies, and upload field names.
- At least one manual smoke path through `uv run ybot serve config.example.yaml` and the React dev/build flow when frontend behavior changes.
- No regression to raw JSON-only editing for fields that have a stable first-class contract.


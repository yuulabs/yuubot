# Kanban Position Report

Date: 2026-06-04

## 2026-06-04 Workflow A/B Fix Pass

Implemented fixes:

- Workflow A: `ybot dev` now health-checks daemon and Admin during startup and returns non-zero if a child exits before becoming healthy.
- Workflow A: daemon actor reconciliation now quarantines actor startup failures instead of failing lifespan startup; `/api/status` reports `actor_startup_failures` when this happens.
- Workflow A: Admin lifecycle toggles now call `POST /api/resources/{type}/{id}/enable|disable`, matching the daemon route surface.
- Workflow A/B: React `/monitor` is no longer shadowed by the embedded trace UI; trace UI mounts at `/monitor/trace`.
- Workflow B: actor assembly derives `yext` imports/expanded functions from actor-visible capability specs instead of hardcoding `yext.echo`.
- Workflow B: the actor create form can select integration capabilities and sends native `allowed_capability_ids`.
- Workflow B: Admin has `POST /api/providers/{id}/validate`, and the Provider detail page exposes backend validation status for connection/default model checks.

Verification:

- `timeout 180s uv run ruff check src tests` passed.
- `timeout 180s uv run ty check` passed.
- `timeout 240s uv run pytest` passed, 84 tests.
- `npm run build` in `web/` passed.
- `timeout 180s uv run ybot --config config.yaml check` passed.
- `timeout 20s uv run ybot --config config.yaml dev` reached both health checks and was stopped by timeout. The persisted OpenAI actor still logged its missing API key, but daemon startup continued and stayed healthy.

Remaining Workflow B gaps:

- LLM API keys are still environment-secret references, not resource-backed `Secret` values for LLM backends.
- Backend validation reports configured capability flags and model reachability, but does not prove streaming/tool-calling/vision/reasoning support with live capability probes.
- Web Chat remains synchronous and non-persistent.
- Monitor still lacks trace tree and cost aggregation views.

Scope: current working tree against `design/kanban.md`, with emphasis on:

1. Code functionality: a feature is either not implemented or implemented correctly enough to work end to end.
2. Code quality: no god objects, no unexpected redundancy, no ugly patches for sibling package abstraction gaps, good typing, good design, good extensibility.

## Execution Workflows First

### Workflow A: Developer Starts The Service Locally

Expected workflow:

1. Developer prepares `config.yaml`.
2. Developer builds or uses existing `web/dist`.
3. Developer runs `uv run ybot --config config.yaml dev`.
4. `ybot dev` starts two child processes:
   - daemon on `127.0.0.1:8780`
   - admin on `127.0.0.1:8781`
5. Daemon opens the resource DB under `paths.data_dir`.
6. Daemon loads persisted resources, reconciles integrations/routes/actors, and remains healthy even if one actor cannot start.
7. Admin serves the SPA and proxies `/api/resources/*` and `/api/chat/*` to daemon.
8. Developer opens `http://127.0.0.1:8781`, creates or edits runtime resources, launches an actor, and tests it through Web Chat.

Where the current code breaks (post-fix state):

- **Step 3/6 RESOLVED.** `_run_dev()` health-checks daemon and admin during startup, returns non-zero on failure. `ActorManager.reconcile()` catches per-actor startup failures and reports them via `/api/status` `actor_startup_failures` without crashing lifespan startup. A persisted OpenAI actor missing its API key will log a warning and appear in `actor_startup_failures`, but daemon stays healthy.
- **Step 8 partially resolved.** `setResourceEnabled()` now calls `POST /api/resources/{type}/{id}/enable|disable`. The actor wizard can now select integration capabilities (`allowed_capability_ids`). Remaining gaps: no yuuagents tool selection in wizard, Web Chat still synchronous/non-persistent.

Remaining Workflow A gaps:

- `ybot check` requires `--config config.yaml` (bare command fails on empty defaults without `secrets.master_key`).
- Actor startup during web chat (`start_actor()`) still raises on failure — only reconcile quarantines.
- No temporary dev data directory or smoke check mode.

Conclusion: `ybot dev` now reliably starts and stays healthy; the developer can work e2e with echo integration. The OpenAI path works if an API key is configured.

### Workflow B: User Creates And Tests The First Actor

Expected workflow from `design/kanban.md`:

1. User opens Admin.
2. User creates an OpenAI-compatible LLM backend with base URL and API key.
3. User validates the backend and selected default model.
4. User clones or creates a character and edits the prompt.
5. User creates a `simple_loop` actor:
   - selects character
   - selects backend/model
   - selects yuuagents tools
   - selects allowed integration capabilities
   - configures budget/runtime/resource policy
6. User opens Web Chat from the created actor.
7. User sends a message.
8. Actor sees the intended `yb` system facade and only allowed `yext` functions.
9. Actor replies.
10. User opens Monitor and sees trace tree, tool calls, integration calls, token use, and cost.

Where the current code breaks (post-fix state):

- **Step 2** still incomplete. API keys are environment-secret references, not resource-backed `Secret` values.
- **Step 3 partially resolved.** `POST /api/providers/{id}/validate` exists and checks connection + default model reachability. Does not yet probe streaming/tool-calling/vision/reasoning with live capability probes.
- **Step 5 partially resolved.** Actor form now sends `allowed_capability_ids` for integration capabilities. Does not yet expose yuuagents tool selection (`agent_tools`).
- **Step 8 partially resolved.** `assembly.py` now derives `yext` imports/expanded functions from actor-visible capability specs dynamically (`_facade_imports()`, `_facade_expand_functions()`), no longer hardcodes `yext.echo`. No `PromptAssembler` yet — system prompt is still `binding.character.system_prompt` only.
- **Step 10 partially resolved.** `/monitor` route conflict fixed — trace UI mounts at `/monitor/trace`. React Monitor page still lacks trace tree and cost aggregation views.

Remaining Workflow B gaps:

- LLM API keys still environment-secret references.
- No live capability probes in backend validation.
- No yuuagents tool selection in actor wizard.
- No `PromptAssembler`; no `design/prompts.md`.
- No Web Chat streaming, dialog persistence, or trace linking.
- Monitor lacks trace/cost views.

Conclusion: the critical path blockers (lifecycle toggles, route conflicts, hardcoded imports) are resolved. The product workflow is functional for echo integration and has been verified with tests; real-LLM and full-capability paths need the remaining gaps closed.

### Workflow C: Deploy The Service

Expected deployment workflow:

1. Operator builds or pulls a yuubot image.
2. Operator provides `config.yaml` or generated environment variables.
3. Operator mounts only `data_dir` for durable state.
4. Operator starts daemon/admin behind explicit ports.
5. Health checks confirm Admin is reachable, daemon is reachable, resources load, and traces are available.
6. Operator can upgrade with backup/rollback.

Where the current code breaks:

- No compose file, installer, systemd unit, reverse proxy config, deployment health command, upgrade helper, or rollback path exists.
- The checked-in `Dockerfile` does not match this repo root as a build context: it copies `yuutrace/ui`, `yuuagents`, `yuubot`, `yuullm`, `yuutools`, and `yuutrace`, but those paths do not exist under the current repo root.
- The Docker entrypoint invokes stale CLI commands/options: `ybot -c "$CONFIG_PATH" _recorder` and `ybot -c "$CONFIG_PATH" up`. The current CLI exposes `--config`, `check`, `daemon`, `admin`, `dev`, `export`, and `import`; it has no `-c`, `_recorder`, or `up`.
- `docker_config.yaml` uses an older config shape (`recorder`, `daemon`, `web`, `network`, etc.), not the current `BootstrapConfig` shape (`admin`, `server`, `database`, `secrets`, `trace`, `paths`, `yuuagents`).

Conclusion: deployment infrastructure is not just incomplete; the current Docker path is stale and not executable as the service startup path for this codebase.

## Verification

Commands run:

- `timeout 180s uv run ruff check src tests` -> passed.
- `timeout 180s uv run ty check` -> passed.
- `timeout 240s uv run pytest` -> passed, 84 tests.
- `timeout 180s uv run ybot check` -> failed because the CLI validates empty defaults when no `--config` is supplied, and empty defaults do not include `secrets.master_key`.
- `timeout 180s uv run ybot --config config.yaml check` -> passed.
- `timeout 20s uv run ybot --config config.yaml dev` -> reached both health checks and was stopped by timeout. The persisted OpenAI actor still logged its missing API key as a warning, but daemon startup continued and stayed healthy. `actor_startup_failures` appeared in `/api/status`.
- `./.venv/bin/ybot --help` -> current CLI commands are `admin`, `check`, `daemon`, `dev`, `export`, `import`.
- `./.venv/bin/ybot -c config.yaml up` -> failed with `No such option: -c`.
- `find . -maxdepth 3 ... compose ...` -> no compose/deployment service file found.

Interpretation: core code quality gates pass. `ybot dev` now reliably starts and health-checks both processes; daemon survives individual actor startup failures. The bare `ybot check` command still requires a config with `secrets.master_key`. Deployment Docker files remain stale.

## Executive Position

The codebase is past backbone/prototype and into a working local daemon/Admin resource system. The strongest implemented path is:

Admin API proxy -> daemon resource CRUD -> resource refresh/reconcile -> enabled actor/integration startup -> echo integration ingress -> `simple_loop` actor -> `execute_python` -> generated `yext` facade -> integration capability invocation -> trace/cost instrumentation tests.

However, the kanban target scenario is not done. The current product is around:

- Section 1: roughly 65-75 percent functionally complete. Lifecycle toggles fixed, actor capability selection wired, provider validation endpoint added. Web Chat still synchronous; secrets not yet resource-backed.
- Section 2: roughly 35-45 percent complete. Permission backend exists, facade imports now dynamic from capability specs. No `PromptAssembler` yet; no tool catalog API.
- Section 3: roughly 15-20 percent complete. Route conflict resolved, trace plumbing tested. Monitor still lacks trace tree and cost aggregation views.
- Section 4: roughly 10-20 percent complete. External plugin loop exists; real-world builtins are not implemented.
- Sections 5-8: mostly not implemented beyond config/layout/archive primitives.

Important correction: `ybot dev` now reliably starts and stays healthy (health probes + actor quarantine). The `ybot dev` exit code is non-zero when a child fails before health. Deployment is still not viable from Docker path; the Docker assets remain stale.

## Kanban Status

### 1. Backend/Frontend Config And Web Chat Launch Path

Status: partially implemented, not ready to mark complete.

Implemented correctly:

- Admin resource proxy over daemon `/api/resources` exists and preserves daemon secret handling and daemon responses. See `src/yuubot/runtime/admin/app.py` routes for `/api/resources/...`.
- Daemon resource CRUD/lifecycle handlers are separated into `ResourceCommandHandlers` and `ResourceService`.
- Core resource API and lifecycle flows are tested.
- Frontend pages exist for providers, characters, actors, routes, integrations, settings, chat, and monitor.
- Provider model fetching endpoint exists at `POST /api/providers/{id}/models`.
- Web Chat backend path exists at `POST /api/chat/{actor_id}/messages` and the Admin proxy exposes it.
- There is an E2E test proving Admin can create backend -> character -> actor -> send Web Chat message -> receive actor reply.

Not implemented correctly yet:

- LLM backend templates are partial. Provider presets exist in the frontend, but API keys are modeled as `api_key_secret_id` environment references, not written through the existing `Secret` wrapping/redaction path.
- Backend validation is partial. `POST /api/providers/{id}/validate` exists and reports connection/default-model reachability. Does not yet prove streaming/tool-calling/vision/reasoning support with live capability probes.
- Actor create wizard is partial. It selects character/backend/model, basic budget/runtime/workspace fields, and integration capabilities (`allowed_capability_ids`). Does not yet select yuuagents tools (`agent_tools`).
- Web Chat is partial. It uses synchronous HTTP, not WebSocket/streaming; it does not persist dialog state; it does not return turn errors as persisted browser-visible dialog events; it does not link replies to trace detail.
- Launch smoke path is partial. Actor pages have chat affordances, but actor creation does not provide a complete post-create launch flow, and Chat lists enabled actors rather than verified running actors.
- **Lifecycle toggles RESOLVED.** `setResourceEnabled()` calls `POST /api/resources/{type}/{id}/enable|disable`. Verified against admin route `POST /api/resources/{resource_type}/{id}/{action}`.

### 2. Tool, Facade, And Prompt Visibility

Status: mostly not implemented.

Implemented correctly:

- Generated `yext` package creation is dynamic and actor-specific in `src/yuubot/core/facade/codegen.py` and `src/yuubot/core/facade/workspace.py`.
- `IntegrationCore.invoke()` enforces actor-level `allowed_capability_ids`.
- `yb` is handwritten and kept separate from generated `yext`.
- `yb.tasks.submit_bg` exists and has tests for background lifecycle messages.

Not implemented correctly yet:

- `design/prompts.md` does not exist.
- There is no `PromptAssembler`; `build_agent_definition()` still passes only `binding.character.system_prompt`.
- There is no full tool/facade catalog API for yuuagents tool backends, builtin `yb` modules, and actor-visible `yext` capabilities.
- Permission tests do not yet prove that an actor with no `allowed_capability_ids` cannot import/see generated integration functions. They prove invocation permission and echo visibility for allowed cases.
- **Hardcoded `yext.echo` RESOLVED.** `_facade_imports()` and `_facade_expand_functions()` now derive per-capability imports and expanded function hints from `facade.capabilities` dynamically. Actor assembly no longer hardcodes echo namespace.

### 3. Monitor Panel And Trace Visualization

Status: mostly not implemented.

Implemented correctly:

- Trace/cost backend instrumentation has E2E tests.
- `/api/status` reports daemon/admin-ish runtime summary data.
- Admin can mount the yuutrace UI.
- React Monitor page has placeholder health/resource cards.

Not implemented correctly yet:

- No Admin monitor summary endpoints for recent traces or cost aggregation.
- No yuubot trace adapter over `traces.db`.
- No visual trace tree.
- No failure-oriented views.
- **Route conflict RESOLVED.** Trace UI now mounts at `/monitor/trace` (line 448: `Mount("/monitor/trace", app=build_trace_app(...))`). React `/monitor` page is no longer shadowed.

### 4. Real-World Integrations First Wave

Status: mostly not implemented.

Implemented correctly:

- The integration contract, lifecycle, capability schema, invocation context, generated facade, and storage boundaries are in place.
- Echo builtin integration is a good test fixture.
- External plugin manifest/install/subprocess/facade path exists and has tests.

Not implemented correctly yet:

- No Web Search/Web Read builtin.
- No GitHub integration.
- No Linear/Lark/Notion integration.
- No Telegram/Discord IM integration.
- External plugin developer loop has code and tests, but no complete documentation/sample plugin story matching the kanban item.

### 5. Deployment Infrastructure

Status: not executable as a deployment path.

Implemented:

- `Dockerfile`, `docker/entrypoint.sh`, `docker_config.yaml`, data layout, archive export/import, and bootstrap config validation exist.

Not implemented correctly yet:

- The Docker assets are stale relative to the current codebase. The entrypoint calls removed/unknown CLI commands and options.
- The Dockerfile expects a different build context than the current repo root.
- The Docker config file uses an older config schema.
- Published/runnable compose profiles, interactive installer, HTTPS/reverse proxy automation, upgrade helper, runtime backup/restore UI, and deployment hardening command are missing.

### 6. Yuu Network

Status: not implemented.

There is no design doc or runtime/client implementation for Yuu Network.

### 7. Skills Management

Status: mostly not implemented.

Implemented:

- `DataLayout` creates `data_dir/skills`.

Missing:

- Resource-backed skill catalog, browse/edit/import/reset UI, actor-visible `load_skills`, prompt expansion integration.

### 8. Runtime Operations

Status: not implemented.

No Web PTY, Web FS, archive conflict UI, or Admin restart/reload controls are implemented.

## Functionality Findings

### F1. ~~`ybot dev` can fail daemon startup while still returning success~~ RESOLVED

Severity: ~~high~~ resolved. Implemented in 2026-06-04 fix pass.

- `_run_dev()` in `src/yuubot/cli.py` now health-checks both daemon and admin children via `/healthz` probes. Returns non-zero if any child exits before becoming healthy (lines 111-127).
- `ActorManager.reconcile()` via `_start_missing_actors_locked()` catches per-actor startup failures and records them in `_startup_failures` without crashing the lifespan (lines 150-156).
- `/api/status` reports `actor_startup_failures` for visibility (daemon/app.py lines 294-302).
- `ybot dev` with `config.yaml` now reaches both health checks and stays healthy even with a misconfigured persisted actor.

### F2. Deployment workflow is stale and not executable

Severity: high for deployment readiness.

The expected deployment flow is image build -> config/env injection -> data mount -> daemon/admin startup -> health checks. The checked-in deployment files do not implement that flow:

- `Dockerfile` copies sibling/workspace paths that are not present in this repo root.
- `docker/entrypoint.sh` calls `ybot -c "$CONFIG_PATH" _recorder` and `ybot -c "$CONFIG_PATH" up`, but the current CLI has no `-c`, `_recorder`, or `up`.
- `docker_config.yaml` is not shaped like the current `BootstrapConfig`.
- There is no compose profile or installer to define the intended build context, mounts, secrets, ports, health checks, or reverse proxy.

Recommended fix: decide the deployment unit first. For this repo, the minimal coherent path is a container that runs current `ybot --config /config/config.yaml daemon` and `ybot --config /config/config.yaml admin` as supervised processes, with one mounted `data_dir`, current config shape, and health checks for Admin and daemon.

### F3. ~~Frontend enable/disable actions call an unsupported HTTP method~~ RESOLVED

Severity: ~~high~~ resolved. Implemented in 2026-06-04 fix pass.

`web/src/lib/api.ts` `setResourceEnabled()` now calls `POST /api/resources/{resourceType}/{id}/{action}` where action is `"enable"` or `"disable"` (lines 100-111). Admin routes expose `POST /api/resources/{resource_type}/{id}/{action}` at lines 414-418 of admin/app.py, proxying to daemon. Verified against test suite.

### F4. ~~React Monitor route is shadowed by yuutrace mount in normal Admin runtime~~ RESOLVED

Severity: ~~high~~ resolved. Implemented in 2026-06-04 fix pass.

Trace UI now mounts at `/monitor/trace` (admin/app.py line 448: `Mount("/monitor/trace", app=build_trace_app(...))`). React `/monitor` page is no longer shadowed and is accessible in normal Admin runtime.

### F5. Web Chat is working but not the kanban Web Chat

Severity: medium.

The current endpoint can send a message to a `SimpleLoopActor` and wait up to 5 seconds for `actor.next_turn_result()`. This is enough for a smoke test but not for the kanban target: no streaming endpoint, no WebSocket, no dialog persistence, no reloadable message history, no trace link, and no first-party `MailMessage` subclass.

Recommended fix: define explicit Web Chat message/result records first, then add persistence and a streaming response path. Keep the current HTTP endpoint as smoke-compatible if useful.

### F6. Provider validation is model listing, not backend validation

Severity: medium.

`POST /api/providers/{id}/models` can fetch models. It does not validate the selected default model or return model capability support for streaming/tool calling/vision/reasoning.

Recommended fix: add a separate validation endpoint that returns a typed validation result instead of overloading model listing.

### F7. Actor creation cannot configure integration capabilities or yuuagents tools

Severity: medium.

The frontend actor creation form does not expose `agent_tools` or `allowed_capability_ids`, so the user cannot create the target actor described in the kanban through Admin alone.

Recommended fix: build a catalog endpoint first, then wire actor creation to selected tool and capability IDs.

## Code Quality Findings

### Q1. Core boundaries are generally clean

The core backend is not dominated by a god object. Good separations exist:

- Resource schema/storage/repository/service are separate.
- Daemon HTTP handlers delegate domain operations to `ResourceService`.
- Actor lifecycle is in `ActorManager`.
- Integration lifecycle/invocation is in `IntegrationCore`.
- Facade codegen/workspace/bridge/client are separate.
- Gateway routing is isolated from integration implementation details.

This is a solid foundation.

### Q2. `runtime/admin/app.py` is too broad

`src/yuubot/runtime/admin/app.py` mixes Admin app assembly, daemon proxying, provider model client construction, integration secret reveal, plugin install/uninstall, static SPA serving, and yuutrace mounting.

This is not yet catastrophic, but it is becoming the largest coordination file on the Admin side. The next features will make it worse unless provider validation, plugin admin, and monitor routes move into smaller route modules.

### Q3. Frontend resource forms duplicate model/pricing logic

Provider and actor pages duplicate model option merging, pricing checks, budget checks, backend provider key resolution, and UI-level validation. Some of this already leaks into inconsistent behavior, especially around actor creation versus provider editing.

Recommended fix: extract resource form adapters and shared domain helpers, or move more validation to typed backend endpoints.

### Q4. Transitional payload normalization is useful but should not grow

`ResourceCommandHandlers._normalize_actor_payload()` is a pragmatic bridge from simplified Admin form payloads to `ActorRecord`. It is acceptable now, but it will become an ugly patch if the frontend keeps sending semi-records with resolved nested resources plus shortcut fields.

Recommended fix: define explicit Admin command DTOs for actor creation/update once tool/capability selection is added.

### Q5. ~~Hardcoded facade imports are the main extensibility break~~ RESOLVED

Dynamic `yext` generation is already implemented. `_facade_imports()` (assembly.py lines 372-380) and `_facade_expand_functions()` (lines 383-391) now derive per-capability imports and expanded function hints from `facade.capabilities` dynamically. The constant `FACADE_IMPORTS` (lines 53-58) provides the fixed `yb.*` + `yext` base; per-capability submodules like `yext.discord` are added from capability specs. No more hardcoded `yext.echo`.

## Recommended Next Kanban Moves

1. **Fix deployment Docker assets** to match current CLI/config shape, or decide deployment unit first. The minimal coherent path is `ybot daemon` + `ybot admin` as supervised processes.
2. **Resource-backed LLM API keys**: Wire provider API keys through the existing `Secret` wrapping/redaction path.
3. **Finish Section 1**: Actor wizard must include yuuagents tool selection (`agent_tools`). Web Chat must persist/reload dialog state with trace links and streaming.
4. **Start Section 2**: Create `design/prompts.md`, implement `PromptAssembler`, and build tool/facade catalog API.
5. **Monitor endpoints**: Add trace summary and cost aggregation endpoints after the route conflict is resolved (already done).
6. **Real-world integrations**: Keep blocked until capability visibility and permission tests are tightened.
7. **Backend capability probes**: Extend `POST /api/providers/{id}/validate` to report streaming/tool-calling/vision/reasoning support with live probes.

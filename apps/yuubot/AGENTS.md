# apps/yuubot

The runnable yuubot application: HTTP daemon, Admin UI process, actor runtime,
the `yb`/`yext` facade, integration & tool registries, and the v2 resource
layer. Sibling packages (`yuuagents`, `yuullm`, `yuutools`, `yuutrace`) are
resolved as workspace members from the monorepo root.

This package's source is split under `src/`. `yuubot` (application core),
`yb` (handwritten system facade), and `yext` (generated integration facades)
are all shipped as importable top-level packages from the same wheel.

## Source Map

### `src/yuubot/` — application core

| Path | Responsibility |
|---|---|
| `cli.py` | `ybot` entrypoint (`check`, `daemon`, `admin`, `dev`, `trace ui`, `export`, `import`). Owns `ybot dev`: builds the Admin UI, spawns daemon+admin children, health probes. Also owns worktree-shared dev caches (`_find_monorepo_root`, `_git_common_dir_parent`). |
| `bootstrap/` | `config.py` — v2 `BootstrapConfig` (msgspec), loads `config.yaml` + `.env`. `layout.py` — `DataLayout`, the single source of truth for every on-disk path derived from `paths.data_dir`. |
| `core/gateway.py` | Event ingress → routes accepted events into conversations. First hop after the recorder. |
| `core/routing.py` | `RouteTable` matching: event → `ConversationRoute` (integration/character/actor). |
| `core/conversations.py`, `core/conversation_events.py` | Conversation state + event queue/mailbox enqueue. |
| `core/actors/` | `contracts.py`, `manager.py`, `registry.py`, `workspace.py`; `impls/` (`echo`, `simple_loop`, `python_session`). `SimpleLoopActor` runs `YuuAgentsActorRuntime.run_delegate`. |
| `core/assembly/_runtime.py` | The actor/agent loop: `_run_agent_turn` (`append` → `while not done: step → charge budget → run tools`) and `run_delegate` (one-shot wrapper). The yuubot↔yuuagents boundary. |
| `core/assembly/` (rest) | `_definition.py` (agent definition build), `_history_codec.py`, `_llm_session.py`, `_prompt.py`, `_python_tool.py`, `_rollover.py` (history compaction/summary), `_stage.py`, `_tool_runtime.py`, `_tools.py`, `_constants.py`. |
| `core/facade/protocol.py` | `FacadeRpcRequest` / `FacadeRpcResponse` (msgspec; `kind` discriminates 6 request kinds) for the daemon↔agent TCP line protocol. |
| `core/facade/bridge.py` | Daemon-side `IntegrationInvokeBridge`: TCP server, token auth, dispatch by `kind` (invoke / delegate_submit / im_response / background_started / background_finished / schedule). Sends `FacadeDelegateTask` mail; results come back as conversation turns (`BackgroundCompletedMessage`), **not** as RPC return values. |
| `core/facade/client.py`, `workspace.py`, `codegen.py` | Generated `yext` package (one fn per capability) emit + per-actor workspace symlink. |
| `core/integrations/` | `contracts.py`, `context.py`, `core.py` (`IntegrationCore`), `registry.py`; `impls/` (`echo`, `github`, `test_im`). |
| `core/tools/` | `contracts.py`, `registry.py`; `impls/` (`bash.py`, `execute_python.py`, `file_tools.py`). |
| `core/observability.py` | `YuubotTraceContextProvider` — augments yuuagents OTEL spans with `yuubot.*` attrs (conversation_id, character, model, actor, integration, capability, task). |
| `core/` (rest) | `bindings.py`, `capabilities.py`, `cache.py`, `costing.py`, `events.py`, `llm.py`, `messages.py`, `message_rendering.py`, `secrets.py`, `validation.py`, `builtin_tools.py`. |
| `resources/` | V1 resource layer: `root.py` (`Resources` aggregate), `store/`, `repository.py`, `records.py`, `orm.py`, `registry.py`, `service.py`, `codec.py`, `events.py`, `errors.py`, `secrets.py`. Loaded from `config.yaml` into DB tables at startup. |
| `runtime/daemon/` | `app.py` (`build_daemon`, ASGI wiring, `logger`), `handlers.py` (HTTP + SSE producer, `logger`), `middleware.py`, `validators.py`, `commands/` (`_app.py`, `_handlers.py` [has `logger`], `_codec.py`, `_middleware.py`, `_schemas.py`, `_helpers.py`). |
| `runtime/admin/` | `app.py` (`build_admin`), `handlers/` (`_proxy.py` — daemon proxy + SSE relay; `_daemon.py` — `_stream_daemon_sse`; `_provider_admin.py`, `_plugin_admin.py`, `_meta.py`, `_helpers.py`, `_types.py`). |
| `runtime/plugin/` | External plugin subprocess host: `_manager.py` (`logger`), `_process.py` (`logger`), `_facade.py`, `_lifecycle.py`, `_manifest.py`. |
| `runtime/archive.py` | `export_data` / `import_data` for `ybot export|import`. |
| `runtime/process.py` | `configure_file_logging` (rotating 10 MB×5), `ASGIServer`/`UvicornServer`, `ServiceHost`, `TraceService` (yuutrace collector+UI threads), `open_store`, `open_resources`. |
| `events.py`, `process.py` (top) | Cross-cutting event types and small process helpers. |

### `src/yb/` — handwritten system facade (runtime, in agents)

Imported by the ipykernel agent subprocess; talks to the daemon over TCP.

| Path | Responsibility |
|---|---|
| `_client.py` | Opens a **new TCP connection per RPC** to the daemon bridge. |
| `_context.py` | Per-call execution context. |
| `actor.py` | `yb.actor` — agent-side actor introspection / messaging helpers. |
| `delegate.py` | `delegate.submit()` — fire-and-forget delegate mail. |
| `schedule.py` | `yb.schedule` — cron-style scheduling. |
| `tasks.py` | Background task lifecycle primitives. |

### `src/yext/` — generated integration facades

Not hand-edited: emitted by `core/facade/codegen.py` per integration, symlinked
into actor workspaces. Subdirs mirror integrations (`echo`, `github`, …), one
Python function per capability.

### `web/` — Admin UI (Vite + React, served by the admin process)

`pnpm install` + `pnpm run build` emits `web/dist/`, served by the admin
process. `ybot dev` builds this automatically when stale via `_build_web`.

## Key data-flow facts

- **`delegate.submit()` does not return a result.** `delegate.submit()` does
  not return a result. The bridge sends a `FacadeDelegateTask` mail; the
  daemon's `SimpleLoopActor` (`core/actors/impls/simple_loop.py`) runs it via
  `YuuAgentsActorRuntime.run_delegate` and injects the result back as a **new
  conversation turn** (`BackgroundCompletedMessage`). There is no result store.

- The agent loop (`YuuAgentsActorRuntime._run_agent_turn` in
  `core/assembly/_runtime.py`) is the yuubot↔yuuagents boundary:
  `append` → `while not agent.done: step → charge budget → run tools`.
  `run_delegate` is a one-shot wrapper around it.

- The daemon and admin processes are **separate children** launched by
  `ybot dev`. The admin proxies most API calls to the daemon and only owns the
  SSE relay + plugin/provider admin UI surfaces.

## Debug instrumentation points

Drop `print` / `logger.debug` here first, in this order, when the symptom is
localised to a layer:

| Symptom | First file | What to log |
|---|---|---|
| Event never enters the platform | `core/gateway.py` | accepted event payload, route table result |
| Route maps wrong conversation | `core/routing.py` | event → `ConversationRoute` resolution |
| Mailbox never receives | `core/conversations.py`, `core/conversation_events.py` | conversation_id, enqueue |
| Agent doesn't run / hangs / budget | `core/assembly/_runtime.py` `_run_agent_turn`, `run_delegate` | append/step/done, budget, tool calls (this file currently logs **nothing**) |
| Capability call from agent vanishes | `src/yb/_client.py` | each `FacadeRpc` request+response |
| Daemon-side capability dispatch | `core/facade/bridge.py` `IntegrationInvokeBridge` | per-`kind` route, errors (this file currently logs **nothing**) |
| Admin UI events stop / drop | `runtime/admin/handlers/_proxy.py`, `runtime/admin/handlers/_daemon.py` `_stream_daemon_sse` | upstream connect/disconnect, frame bytes, EventSource reconnects |
| HTTP handler blows up | `runtime/daemon/handlers.py`, `runtime/daemon/commands/_handlers.py` | already log via `logger`; check `logs/daemon.log` |
| Plugin subprocess misbehaves | `runtime/plugin/_process.py`, `runtime/plugin/_manager.py` | already log via `logger` |
| Trace spans missing/wrong | `core/observability.py` + `packages/yuuagents/.../obs/` | span start/end attrs, `yuubot.*` attrs |

Existing `logger = logging.getLogger(__name__)` sites: `runtime/daemon/app.py`,
`runtime/daemon/handlers.py`, `runtime/daemon/commands/_handlers.py`,
`runtime/plugin/_process.py`, `runtime/plugin/_manager.py`. **The agent loop
and the facade bridge log nothing** — instrument those two first when the bug
sits at the yuubot↔yuuagents or daemon↔agent boundary.

## Artifact inspection (no daemon restart needed)

| Artifact | Path | How |
|---|---|---|
| Platform DB | `<data_dir>/yuubot/yuubot.db` (default `~/.yuubot/yuubot/yuubot.db`) | `sqlite3 -readonly <path>` — `conversations`, resources, routes |
| Trace DB | `<data_dir>/yuubot/traces.db` | `uv run ybot --config config.yaml trace ui`, or `sqlite3 -readonly` |
| Logs | `<data_dir>/yuubot/logs/{daemon,admin}.log` | tail / grep for tracebacks |

## Commands

```bash
uv run ybot --config config.yaml check         # validate bootstrap config
uv run ybot --config config.yaml dev           # daemon + admin + web build
uv run ybot --config config.yaml trace ui      # browse traces
uv run pytest                                   # package-local tests
uv run pytest tests/test_<x>.py -v
cd web && pnpm install && pnpm run build        # frontend (also done by dev)
```

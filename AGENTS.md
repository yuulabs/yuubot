# yuubot Monorepo

This repository is the canonical `yuubot` development root. The Git root, uv
workspace root, CI root, and YuuCoder worktree root are the same directory.

Each package carries its own `AGENTS.md` with a detailed module map. Read it
before editing that package.

## Scenario: Local Bot Development

```text
Developer clones github.com/yuulabs/yuubot
  → enters the repository root
    → uv sync resolves apps/yuubot plus packages/* through workspace sources
      → uv run ybot --config config.yaml dev
        → builds frontend (cache hit) → spawns daemon + admin child processes
          → /healthz green on both → ready
```

## Layout & Per-Package Guidance

- `apps/yuubot/` — yuubot application (daemon, admin, actor runtime,
  integrations, facade, Admin UI). See `apps/yuubot/AGENTS.md`.
- `packages/yuuagents/` — agent runtime primitives (`Agent`, `Stage`,
  `Runtime`, `MailBox`, tools, python kernel). See `packages/yuuagents/AGENTS.md`.
- `packages/yuullm/` — provider-agnostic streaming LLM interface
  (`YLLMClient`, `ProviderPool`, `YuuSession`). See `packages/yuullm/AGENTS.md`.
- `packages/yuutools/` — explicit async-first tool framework. See
  `packages/yuutools/AGENTS.md`.
- `packages/yuutrace/` — OpenTelemetry-based LLM observability SDK + React UI.
  See `packages/yuutrace/AGENTS.md`.

Do not migrate or restore the legacy `yuubot` v1 repository into this tree.

## Fixed Commands

Run workspace-level commands from the repository root:

```bash
uv sync                                     # resolve + link workspace
uv run ruff check                           # lint
uv run ty check                             # type check
uv run ybot --config config.yaml check       # validate bootstrap config
uv run ybot --config config.yaml dev         # daemon + admin + web build
```

If the root `config.yaml` is absent, copy
`apps/yuubot/config.example.yaml` to `config.yaml` first and fill the required
environment variables referenced by the file.

Run package-local tests from the package directory when validating one member:

```bash
cd apps/yuubot     && uv run pytest        # or: uv run pytest tests/test_<x>.py -v
cd packages/yuuagents && uv run pytest
cd packages/yuullm     && uv run pytest
cd packages/yuutools    && uv run pytest
cd packages/yuutrace    && uv run pytest
```

Note: in this monorepo the root `pytest` is provided via the `dev`
dependency-group; if `uv run pytest` reports "Failed to spawn", run
`.venv/bin/python -m pytest` instead (the venv is already synced).

Frontend (Admin UI):

```bash
cd apps/yuubot/web && pnpm install && pnpm run build   # also done by `ybot dev`
```

## Codex Sandbox Blocker

Codex tool sandboxing can distort behavior for code paths that combine
`asyncio`, background threads, `sqlite3`/`aiosqlite`, `pytest`. If you find it hanging, stop and ask the user to approve FULL-ACCESS Mode and rerun it. This is a known codex BUG. Don't try to solve it by yourself.

## Triage Protocol

### Artifact Map (where the truth lives)

`config.yaml` is the strict bootstrap contract consumed at startup. Runtime
resources such as LLM providers, models, pricing, actors, characters,
capability sets, routes, integrations, plugins, and runtime policies are stored
in the resource DB and managed through Admin/API surfaces.

Resolved at runtime through `DataLayout` (`apps/yuubot/src/yuubot/bootstrap/layout.py`):

| Artifact | Default path | Config key |
|---|---|---|
| Platform DB (conversations, resources, routes) | `~/.yuubot/yuubot/yuubot.db` | `database.path` or `paths.data_dir` |
| Trace DB (OTEL spans, yuutrace UI) | `~/.yuubot/yuubot/traces.db` | `paths.data_dir` (derived) |
| Logs (daemon.log, admin.log, rotating 10 MB ×5) | `~/.yuubot/yuubot/logs/` | `paths.data_dir` (derived) |
| Integration workspaces | `~/.yuubot/integrations/<id>/` | derived |
| Actor workspaces | `~/.yuubot/workspace/actors/<id>/` | derived |
| Generated facades (yext) | `~/.yuubot/yuubot/runtime/facades/` | derived |
| External plugins | `~/.yuubot/yuubot/plugins/` | derived |

All artifact stores are inspectable without stopping the daemon. Open SQLite
files with `sqlite3` read-only, or use the Admin Monitor for trace-derived
runtime visibility.

### Triage Flow

Use scenarios to localize the failure before editing code.

```text
Reported symptom (e.g. "bot didn't reply" / "admin showed no new message")
  → 1. Check the source of the event:
       Plugin event → plugin HTTP ingest → /ingest → core/gateway.py core/routing.py
       Admin action → browser → admin:8781 → runtime/admin/handlers/_proxy.py
  → 2. Follow it through the boundary you suspect (see Instrumentation Points):
       front/back boundary: admin proxy SSE stream (runtime/admin/handlers/_proxy.py
                             make_proxy_daemon_conversations_handler, GET .../events)
       yuubot↔yuuagents:     core/assembly/_runtime.py _run_agent_turn (agent loop)
                             core/facade/bridge.py (delegate/im/background)
       LLM call:             yuullm.YLLMClient.stream → packages/yuullm/providers/
       trace wire-up:        core/observability.py + packages/yuutrace
  → 3. Confirm against the three artifact stores:
       logs/daemon.log, logs/admin.log — exception tracebacks
       yuubot.db conversations table — was the turn persisted?
       traces.db (or yuutrace UI) — did spans fire for the agent loop?
```

### Instrumentation Points (where to insert print/breakpoint)

| Layer | File | What to log |
|---|---|---|
| Ingress / route match | `core/gateway.py`, `core/routing.py` | event accepted, route hit |
| Conversation enqueue | `core/conversations.py`, `core/conversation_events.py` | conversation_id, mailbox enqueue |
| Actor loop (yuubot↔yuuagents) | `core/assembly/_runtime.py` `_run_agent_turn`, `run_delegate` | append/step/done, budget, tool calls |
| Daemon↔agent RPC | `core/facade/bridge.py` `IntegrationInvokeBridge` | per-kind dispatch, delegate/im/background |
| Agent↔daemon client | `src/yb/_client.py` | each FacadeRpc round-trip |
| SSE proxy (front/back boundary) | `runtime/admin/handlers/_proxy.py`, `runtime/admin/handlers/_daemon.py` `_stream_daemon_sse` | event-frame bytes, reconnects |
| Daemon HTTP handlers | `runtime/daemon/handlers.py`, `runtime/daemon/commands/_handlers.py` (already has `logger`) | request lifecycle, exceptions |
| LLM streaming | `packages/yuullm/src/yuullm/client.py`, `providers/` | `RawChunkHook`, token deltas |
| Trace emission | `apps/yuubot/.../core/observability.py`, `packages/yuuagents/.../obs/` | span start/end attrs |

Existing `logger = logging.getLogger(__name__)` sites: `runtime/daemon/app.py`,
`runtime/daemon/handlers.py`, `runtime/daemon/commands/_handlers.py`,
`runtime/plugin/_process.py`, `runtime/plugin/_manager.py`. The agent loop and
facade bridge currently log nothing — drop a `logger.debug`/`print` there first
when those layers are in question.

### SSE / Network Data Inspection

The Admin↔daemon boundary streams conversation events over SSE:

```text
Browser EventSource → admin :8781 /api/admin/conversations/{id}/events
  → runtime/admin/handlers/_proxy.py.make_proxy_daemon_conversations_handler
    → _daemon._stream_daemon_sse(daemon, path)  # streaming proxy
      → daemon SSE producer in runtime/daemon/handlers.py
```

To inspect raw frames without the browser: `curl -N` against the admin `/events`
endpoint and against the daemon directly; compare the two streams to localise a
drop. `EventSource` reconnects are silent — log the upstream connection lifecycle
in `_stream_daemon_sse` if events stop arriving.

## Worktree & Dev Cache

### YuuCoder Worktree Rule

YuuCoder must create worktrees from this monorepo root, not from a subpackage:

```bash
git worktree add .tmp/<task>/<slug>/worktrees/<branch-name> <base-branch>
```

A worktree created from the root carries the full uv workspace graph
(`uv.lock` resolves `apps/yuubot` + `packages/*` together). Worktrees checked
out from `apps/yuubot/` or `packages/*` would not, and must not be created.

### Shared Dev Caches (goal: just run `ybot dev`)

`ybot dev` auto-discovers the shared monorepo root via
`git rev-parse --git-common-dir` (same path for the main checkout and every
worktree) and creates the shared caches on first run. No manual
`--store-dir` / `--cache` flag is needed from inside any worktree.

| Cache | Location | Used by |
|---|---|---|
| uv global cache | `~/.cache/uv` (uv default) | `uv sync` for every worktree |
| per-worktree venv | `<worktree>/.venv` | workspace-linked, fast via uv cache |
| shared pnpm store | `<monorepo-root>/.tmp/cache/pnpm-store` | `apps/yuubot/web` |
| shared npm cache | `<monorepo-root>/.tmp/cache/npm` | `packages/yuutrace/ui` |

Rules:

- Each worktree owns its own `dist/` (reflects that worktree's source
  snapshot) — never copy or symlink `dist/` between worktrees.
- Never copy `node_modules/` between worktrees — use the package manager cache.
- Resolved in `apps/yuubot/src/yuubot/cli.py`: `_find_monorepo_root` and
  `_git_common_dir_parent`.

### Worktree Workflow

```text
YuuCoder creates .tmp/<task>/<slug>/worktrees/<branch-name>/
  → uv sync (reuses ~/.cache/uv, links worktree-local .venv)
  → uv run ybot --config config.yaml dev
    → _build_web detects stale frontend → _install_command points pnpm/npm
      at <monorepo-root>/.tmp/cache/... (shared, already populated)
      → pnpm/npm install is a no-op or near-instant on second worktree
      → pnpm run build emits <worktree>/apps/yuubot/web/dist/
```

Cache directories under `.tmp/cache/` are local developer artifacts. Do not
commit them, force-add them, or treat them as release inputs.

## Developer WIP Material

`warroom/` and `apps/yuubot/warroom/` are intentionally ignored local developer
notes. They may be copied between worktrees for continuity, but must not be
force-added or treated as tracked project documentation.

## Roadmap

PyPI publishing and credential / Trusted Publishing migration are roadmap items.
Do not implement release credential changes as part of layout migration work.

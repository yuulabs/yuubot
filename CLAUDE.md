# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/flows/test_llm_session.py -v

# Run tests matching a pattern
uv run pytest -k "test_soft_timeout" -v

# Lint
uv run ruff check src/

# Type check
uv run ty check

# Start NapCat + Recorder (background screen sessions)
uv run ybot launch --config config.yaml

# Run the bot daemon (foreground)
uv run ybot up --config config.yaml
```

## Architecture

yuubot is a QQ bot daemon that bridges NapCat/OneBot events to LLM agents. Five stable concepts define the entire system:

1. **Message** (`core/types.py`) — `InboundMessage` is the typed business-layer message converted from raw OneBot events. Never pass raw `event` dicts past the ingress layer.
2. **Route** (`core/types.py`, `daemon/routing.py`) — Pure function `resolve_route()` returns either `CommandRoute` or `ConversationRoute`. No routing logic lives outside this module.
3. **Conversation** (`daemon/conversation.py`) — `ConversationManager` owns all session state (replaces the old session/flow/ping/auto-mode scatter). States: `idle → running → closed`. Pending messages accumulate in `pending_messages`; rendering happens only when a turn actually fires.
4. **Capability** (`capabilities/`) — Typed contracts for bot-native capabilities (im, mem, web, img, schedule). Each capability has a YAML contract at `capabilities/<name>/contract.yaml`. LLM calls capabilities via `call_cap_cli` tool using raw CLI syntax: `im send --ctx 12 -- [...]`.
5. **RenderPolicy** (`daemon/render.py`) — `RenderPolicy` is a frozen msgspec.Struct that centrally declares how messages become LLM input (format, name resolution, image handling, etc.).

### Request flow

```
NapCat WS → recorder/relay.py
    → daemon/ws_client.py
        → daemon/dispatcher.py   (parse event → InboundMessage → Route)
            → CommandRoute → commands/tree.py (click command tree)
            → ConversationRoute → daemon/agent_runner.py
                → daemon/render.py       (build LLM input via RenderPolicy)
                → yuuagents runtime      (LLM loop)
                    → call_cap_cli → capabilities/ (im/mem/web/img/schedule)
```

### Key module locations

| Concept | Files |
|---------|-------|
| Domain types | `core/types.py`, `core/models.py` |
| Routing | `daemon/routing.py` |
| Conversation state | `daemon/conversation.py` |
| Agent execution | `daemon/agent_runner.py`, `daemon/builder.py`, `daemon/runtime.py` |
| Rendering | `daemon/render.py`, `rendering.py` |
| Capability contracts | `capabilities/contract.py`, `capabilities/<name>/contract.yaml` |
| Capability implementations | `capabilities/im/`, `capabilities/mem/`, etc. |
| Characters/agents | `characters/*.py` — registered via `characters.register(Character(...))` |
| Commands (admin/user) | `commands/builtin.py`, `commands/ychar.py` |
| Config | `config.py` — `load_config()` reads `config.yaml` as the single source of truth and synthesizes the yuuagents runtime config |
| DB | `core/db.py` (Tortoise ORM + SQLite with optional libsimple FTS5) |
| Errors | `core/errors.py` — `YuubotError`, `ConfigurationError`, `CapabilityError`, `MessageSendError` |

### Characters

Characters are registered in `characters/*.py` using `register(Character(...))`. Each defines:
- `name` — agent identifier used in routing and CLI commands
- `spec` — `AgentSpec` with tools, prompt sections, capabilities, `max_steps`
- `min_role` — minimum user role required (`"folk"`, `"mod"`, `"master"`)

The `main` character is the default QQ bot agent (夕雨/Yuu). Other characters: `general`, `researcher`, `curator`.

### Config files

- `config.yaml` — bot/daemon/recorder/session/DB settings (no LLM provider details)
- `llm.yaml` — all LLM provider config: `families`, `providers`, `provider_aliases`, `provider_priorities`, `provider_affinity`, `llm_roles`, `agent_llm_refs`. Loaded first as base; `config.yaml` overrides on top.
- `.env` — env vars, loaded alongside config; supports `${VAR}` substitution in YAML

`load_config()` merges `llm.yaml` (base) → `config.yaml` (override), then synthesizes the yuuagents runtime config.

### capabilities/

`capabilities/` is the single built-in capability layer. It contains runtime implementations, shared helper modules, CLI wrappers, contracts, and user-facing docs.

## Debugging / DevOps

### Log files

Logs are written by loguru via `src/yuubot/log.py`. The `setup(log_dir, name=...)` call happens once at daemon/recorder startup.

| Sink | Level | Location |
|------|-------|----------|
| Console (stderr) | INFO+ | colored, compact |
| File | DEBUG+ | `~/.yuubot/logs/daemon.log` and `~/.yuubot/logs/recorder.log` (each rotated at 20 MB, 5 gz archives kept) |

All stdlib logging (uvicorn, tortoise-orm, websockets) is intercepted and routed through loguru automatically.

**Common log queries:**

```bash
# All events for a specific conversation context
grep "ctx=5" ~/.yuubot/logs/daemon.log

# Trace a specific agent run by task_id prefix
grep "task_id=abc123" ~/.yuubot/logs/daemon.log

# See what the dispatcher accepted/rejected
grep "should_respond\|Command accepted\|Permission denied" ~/.yuubot/logs/daemon.log

# Watch live (daemon running)
tail -f ~/.yuubot/logs/daemon.log

# Agent failures only
grep "agent failed\|exception" ~/.yuubot/logs/daemon.log -i
```

**Log anatomy:** Each line is `YYYY-MM-DD HH:mm:ss.SSS L module:line | message`. Key structured fields emitted by the daemon:

- `event: type=group user=... group=... ctx=...` — every incoming message (DEBUG, dispatcher)
- `should_respond: user=... group=... type=... result=...` — routing decision (INFO)
- `Command accepted: user=... cmd=...` — command dispatched (INFO)
- `agent failed: ctx=... agent=... task_id=...` — agent crash (ERROR + traceback)
- `Flow cancelled for ctx=...` — user or timeout cancel (INFO)

### Conversation traces

Conversation traces live in `~/.yagents/traces.db` (span-based, no events). Use `scripts/conv.py` to inspect them:

```bash
# List recent conversations (short IDs, local time)
uv run python scripts/conv.py

# Show the latest conversation in full
uv run python scripts/conv.py -l

# Show a conversation by ID prefix (no need to copy full UUID)
uv run python scripts/conv.py abc12345

# Compact view — collapses tool calls into a count, no tool output
uv run python scripts/conv.py -l -n

# Filter list by agent
uv run python scripts/conv.py --agent main --limit 10

# Debug a specific tool — only show matching tool calls, full payload
uv run python scripts/conv.py abc12345 --tool "im send" --full

# Search/highlight within a conversation
uv run python scripts/conv.py abc12345 --grep "错误"
```

Compact mode (`-n`) is the go-to for quick reads: it shows USER/ASSISTANT turns and collapses all tool calls into `(N tool calls)`. Full mode shows each `TOOL:` span with output (truncated at 600 chars by default, use `--full` to disable).

**Trace DB schema:** `conversation` → `turn` (role=user/assistant, items in `yuu.turn.items`) → `tools` → `tool:*` (input/output in `yuu.tool.input`/`yuu.tool.output`). Events only contain `yuu.llm.usage` / `yuu.cost`.

### Health / operational endpoints

The daemon exposes a minimal HTTP API (default port 8780):

```bash
# Check if daemon is up and how many per-ctx workers are active
curl http://127.0.0.1:8780/health

# Graceful shutdown
curl -X POST http://127.0.0.1:8780/shutdown

# Reload cron schedules from DB without restarting
curl -X POST http://127.0.0.1:8780/schedule/reload
```

## Testing

Tests are end-to-end only (no unit tests). Run against a test SQLite DB; live external services are skipped via `@pytest.mark.live`.

Key fixtures in `tests/conftest.py`:
- `test_characters` — registers lightweight test Characters, auto-used
- `db` — async fixture that inits/closes a temp SQLite DB
- `dispatcher` — fully wired Dispatcher with mocked AgentRunner

Known issues tracked in `design/issues.md`. When fixing bugs, confirm the test fails first, then passes after the fix.

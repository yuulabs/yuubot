# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_rfc2_skeleton.py -v

# Run tests matching a pattern
uv run pytest -k "test_agent_fns" -v

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
3. **Conversation** (`daemon/conversation.py`) — `ConversationManager` owns all session state. States: `idle → running → closed`. Pending messages accumulate in `pending_messages`; rendering happens only when a turn actually fires.
4. **Agent Functions** (`agent_fns/`) — Python functions exposed as `yb.*` inside the agent's Python session. Facades in `agent_fns/facades/` compose the relevant functions per character. Agents call `yb.send_message(...)` etc.; calls are proxied via HTTP to daemon-local services.
5. **RenderPolicy** (`daemon/render.py`) — `RenderPolicy` is a frozen msgspec.Struct that centrally declares how messages become LLM input (format, name resolution, image handling, etc.).

### Request flow

```
NapCat WS → recorder/relay.py
    → daemon/ws_client.py
        → daemon/dispatcher.py   (parse event → InboundMessage → Route)
            → CommandRoute → commands/tree.py (click command tree)
            → ConversationRoute → daemon/agent_runner.py
                → daemon/render.py       (build LLM input via RenderPolicy)
                → yuuagents Engine       (RFC2 step loop)
                    → execute_python → import yb → agent_fns/
                        → HTTP POST /agent-fns/{service}/{action}
                            → services/ (im/mem/web/media/schedule/workspace/delegate)
```

### Key module locations

| Concept | Files |
|---------|-------|
| Domain types | `core/types.py`, `core/models.py` |
| Routing | `daemon/routing.py` |
| Conversation state | `daemon/conversation.py` |
| Agent execution | `daemon/agent_runner.py`, `daemon/runtime.py`, `daemon/runtime_session.py` |
| Rendering | `daemon/render.py`, `rendering.py` |
| Daemon-local API | `daemon/local_api.py` — FastAPI router at `/agent-fns/{service}/{action}` |
| Services | `services/im.py`, `services/mem.py`, `services/web.py`, `services/media.py`, `services/schedule.py`, `services/workspace.py`, `services/delegate.py` |
| Agent function facades | `agent_fns/facades/` — per-character `yb` module compositions |
| Agent function implementations | `agent_fns/im.py`, `agent_fns/mem.py`, `agent_fns/web.py`, etc. |
| Characters/agents | `characters/__init__.py` — all characters registered here via `register(Character(...))` |
| Prompt assembly | Character `system_prompt` stored as explicit text; Prompt Templates are UI editing aids only |
| Commands (admin/user) | `commands/builtin.py`, `commands/ychar.py` |
| Config | `config.py` — `load_config()` merges `llm.yaml` base + `config.yaml` override |
| DB | `core/db.py` (Tortoise ORM + SQLite with optional libsimple FTS5) |
| Errors | `core/errors.py` — `YuubotError`, `ConfigurationError`, `CapabilityError`, `MessageSendError` |
| Auth | `auth.py` — `bot_kind_for_message()` classifies messages as `"master"` or `"group"` |

### Characters

All characters are registered in `characters/__init__.py` using `register(Character(...))`. Each defines:
- `name` — agent identifier used in routing and CLI commands
- `description` — shown when listing delegates
- `spec` — `AgentSpec` with `facade_module`, explicit `system_prompt`, `delegate_policy`, `max_turns`
- `bot_kinds` — optional tuple restricting which bot_kind can use this character (`"master"` or `"group"`); default allows all

Current characters:
- `yuu` — default group/private chat agent (夕雨); delegates to `general`
- `shiori` — master private chat character; long-lived kernel, workspace management; master-only
- `general` — general task execution delegate; master-only
- `mem_curator` — memory maintenance delegate; master-only

### Agent Functions and Services (RFC2)

Agents interact with bot capabilities exclusively via Python (`execute_python` tool):

```python
# Inside agent's Python session:
import yb
yb.send_message("Hello!")
result = yb.web_search("latest news")
```

`import yb` is resolved to the character's `facade_module` (e.g., `yuubot.agent_fns.facades.yuu`). Each facade re-exports functions from `agent_fns/*.py`. Each agent function makes an HTTP POST to the daemon-local API (`/agent-fns/{service}/{action}`) which is handled by a corresponding `Service` class in `services/`.

**Token binding**: `build_runtime()` issues a `KernelTokenBinding` (`daemon/runtime.py`) — opaque token injected as `YUUBOT_AGENT_TOKEN` env var. The daemon-local API resolves this token to authenticate and scope each call.

### Dual Python Backends

`bot_kind` determines the Python execution backend:
- **`master`** → `kernel` backend: long-lived `PythonSession` per `(user_id, agent_name)`, persists variables across turns, supports `top-level await`
- **`group`** (or any non-master) → `restricted` backend: sandboxed `RestrictedPythonSession` per turn, no file/network/process access, no `while` loops, synchronous `yb.*` only, 8s timeout by default

### Prompt Assembly

Every character stores its full system prompt explicitly as plain text. Prompt Templates may be copied into a Character in the UI, but they are not runtime dependencies. No hidden prompt insertions.

### Config files

- `config.yaml` — bot/daemon/recorder/session/DB settings (no LLM provider details)
- `llm.yaml` — all LLM provider config: `families`, `providers`, `provider_aliases`, `provider_priorities`, `provider_affinity`, `llm_roles`, `agent_llm_refs`. Loaded first as base; `config.yaml` overrides on top.
- `.env` — env vars, loaded alongside config; supports `${VAR}` substitution in YAML

`load_config()` merges `llm.yaml` (base) → `config.yaml` (override), then synthesizes the yuuagents runtime config.

## Debugging / DevOps

### Runtime data locations (Docker deployment)

The production deployment runs in Docker at `~/.local/share/yuubot-docker/`.

| Data | Host path |
|------|-----------|
| Main DB (messages, memories, images) | `~/.local/share/yuubot-docker/data/yuubot/yuubot.db` |
| Conversation traces | `~/.local/share/yuubot-docker/data/yuubot/traces.db` |
| Logs | `~/.local/share/yuubot-docker/data/yuubot/logs/daemon.log` |
| Workspace | `~/.local/share/yuubot-docker/workspace/` |

Inside the container these map to `/data/yuubot/...`. Config at `~/.local/share/yuubot-docker/config/config.yaml`.

**Inspect Docker traces with conv.py:**
```bash
uv run python scripts/conv.py --db ~/.local/share/yuubot-docker/data/yuubot/traces.db
uv run python scripts/conv.py --db ~/.local/share/yuubot-docker/data/yuubot/traces.db -l
uv run python scripts/conv.py --db ~/.local/share/yuubot-docker/data/yuubot/traces.db abc12345
```

**Direct DB queries (messages, memories):**
```bash
DB=~/.local/share/yuubot-docker/data/yuubot/yuubot.db

# Recent messages in a context
sqlite3 $DB "SELECT id, timestamp, nickname, content FROM messages WHERE ctx_id=2 ORDER BY id DESC LIMIT 20;"

# Memory count and recent entries
sqlite3 $DB "SELECT COUNT(*) FROM memories WHERE trashed_at IS NULL;"
sqlite3 $DB "SELECT id, created_at, content FROM memories ORDER BY id DESC LIMIT 10;"

# Context list (ctx_id ↔ group/private target_id)
sqlite3 $DB "SELECT id, type, target_id FROM contexts ORDER BY id;"
```

**Message history note:** The DB contains records from 2026-02-11 onwards. There is a gap from 2026-04-19 05:33 to 2026-04-25 14:21 (migration window — those messages were not captured). `search_messages` defaults to 180 days.

### Log files

Logs are written by loguru via `src/yuubot/log.py`. The `setup(log_dir, name=...)` call happens once at daemon/recorder startup.

| Sink | Level | Location |
|------|-------|----------|
| Console (stderr) | INFO+ | colored, compact |
| File | DEBUG+ | `~/.local/share/yuubot-docker/data/yuubot/logs/daemon.log` (rotated at 20 MB, 5 gz archives kept) |

All stdlib logging (uvicorn, tortoise-orm, websockets) is intercepted and routed through loguru automatically.

**Common log queries:**

```bash
# All events for a specific conversation context
grep "ctx=5" ~/.local/share/yuubot-docker/data/yuubot/logs/daemon.log

# Trace a specific agent run by task_id prefix
grep "task_id=abc123" ~/.local/share/yuubot-docker/data/yuubot/logs/daemon.log

# See what the dispatcher accepted/rejected
grep "should_respond\|Command accepted\|Permission denied" ~/.local/share/yuubot-docker/data/yuubot/logs/daemon.log

# Watch live (daemon running)
tail -f ~/.local/share/yuubot-docker/data/yuubot/logs/daemon.log

# Agent failures only
grep "agent failed\|exception" ~/.local/share/yuubot-docker/data/yuubot/logs/daemon.log -i
```

**Log anatomy:** Each line is `YYYY-MM-DD HH:mm:ss.SSS L module:line | message`. Key structured fields emitted by the daemon:

- `event: type=group user=... group=... ctx=...` — every incoming message (DEBUG, dispatcher)
- `should_respond: user=... group=... type=... result=...` — routing decision (INFO)
- `Command accepted: user=... cmd=...` — command dispatched (INFO)
- `agent failed: ctx=... agent=... task_id=...` — agent crash (ERROR + traceback)
- `RFC2 run cancelled for ctx=...` — user or timeout cancel (INFO)

### Conversation traces

Conversation traces live in `~/.local/share/yuubot-docker/data/yuubot/traces.db` (span-based, no events). Use `scripts/conv.py` to inspect them (pass `--db` for the Docker path):

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
uv run python scripts/conv.py --agent yuu --limit 10

# Debug a specific tool — only show matching tool calls, full payload
uv run python scripts/conv.py abc12345 --tool "execute_python" --full

# Search/highlight within a conversation
uv run python scripts/conv.py abc12345 --grep "错误"
```

Compact mode (`-n`) is the go-to for quick reads: it shows USER/ASSISTANT turns and collapses all tool calls into `(N tool calls)`. Full mode shows each `TOOL:` span with output (truncated at 600 chars by default, use `--full` to disable).

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

## Deployment constraints

**Never restart the `napcat` container unless absolutely necessary.** NapCat holds the QQ session; restarting it risks triggering QQ's risk-control system and getting the account temporarily or permanently banned. To apply yuubot code changes, only rebuild and restart the `yuubot` container:

```bash
cd ~/.local/share/yuubot-docker
docker compose build yuubot
docker compose up -d yuubot   # restarts yuubot only, leaves napcat untouched
```

## Testing

Tests are end-to-end only (no unit tests). Run against a test SQLite DB; live external services are skipped via `@pytest.mark.live`.

Key fixtures in `tests/conftest.py`:
- `db` — async fixture that inits/closes a temp SQLite DB
- `dispatcher` — fully wired Dispatcher with mocked AgentRunner

When fixing bugs, confirm the test fails first, then passes after the fix.

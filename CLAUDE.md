# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

yuubot is a QQ bot framework built on yuuagents, providing IM/Web/Memory skills as CLI tools that agents invoke via subprocess. It receives QQ messages through NapCat (OneBot V11), parses commands, and triggers agents to respond.

## Commands

```bash
# Setup & dependencies
uv sync                          # Install dependencies (uses .venv/)
source .venv/bin/activate        # Activate virtual environment
ybot setup                       # First-time setup (NapCat install, config generation)

# Run
ybot launch                      # Start NapCat + Recorder in background screen sessions
ybot up                          # Start Daemon (foreground)
ybot down                        # Stop Daemon
ybot shutdown                    # Stop Recorder + NapCat

# Test
pytest tests/                    # Run all tests
pytest tests/test_models.py      # Run a single test file
pytest tests/ -m live            # Run live integration tests (require real services)
pytest tests/ -k "test_name"     # Run specific test by name
```

## Architecture

Three-process design, each with independent lifecycle:

```
NapCat (QQ login) → [反向WS] → Recorder (落盘+转发) → [内部WS] → Daemon (Agent驱动)
                                     ↕                              ↓ subprocess
                                   SQLite                      ybot CLI (Skills)
```

- **NapCat**: Maintains QQ login state. Independent process, survives bot restarts.
- **Recorder** (`src/yuubot/recorder/`): Receives NapCat events via reverse WS, persists messages to SQLite, relays to Daemon via internal WS, exposes HTTP API for sending messages back through NapCat.
- **Daemon** (`src/yuubot/daemon/`): Receives relayed events, parses commands (tree matching + role permissions), triggers agents via yuuagents SDK. Agents call skills through `ybot <skill> <command>` subprocess invocations.

Key reason for separation: Recorder stays up while Daemon restarts during development, so messages are never lost and NapCat doesn't need re-login.

## Key Concepts

- **ctx_id**: Auto-incrementing integer mapping to (type, target_id) pairs (private/group + user_id/group_id). Assigned on first message from a chat. Avoids exposing raw QQ numbers to LLM. Hot-loaded from DB on startup.
- **Skills**: CLI tools under `ybot im|web|mem`. Agent calls them via subprocess (`execute_skill_cli` tool). Each skill has a SKILL.md injected into agent prompt.
- **Command Tree** (`commands/tree.py`): Hierarchical longest-prefix-first matching. Entry prefixes (`/y`, `/yuu`) are stripped before matching.
- **Roles**: Master > Mod > Folk > Deny. Per-agent `min_role` in config controls access.
- **Message format**: JSON array of segments: `[{"type":"text","text":"hello"}, {"type":"image","url":"..."}, {"type":"at","qq":"123456"}]`

## Module Map

| Module | Responsibility |
|--------|---------------|
| `cli.py` | Click CLI entry point (`ybot`), registers all subcommands |
| `config.py` | Loads `config.yaml`, validates, provides typed config |
| `core/models.py` | Data models (msgspec.Struct for segments, Tortoise ORM for DB) |
| `core/onebot.py` | OneBot V11 protocol parsing/construction (CQ codes ↔ internal models) |
| `core/context.py` | ctx_id ↔ (type, target_id) bidirectional mapping |
| `core/db.py` | SQLite connection management (Tortoise ORM, WAL mode, FTS5) |
| `core/audit.py` | Audit logging |
| `recorder/server.py` | Reverse WS server receiving NapCat events |
| `recorder/store.py` | Message persistence to SQLite |
| `recorder/relay.py` | Internal WS relay to Daemon |
| `recorder/api.py` | HTTP API proxying NapCat (used by skills to send messages) |
| `daemon/app.py` | FastAPI app + lifecycle (connects recorder, inits agent, starts scheduler) |
| `daemon/dispatcher.py` | Message dispatch: command parse → permission check → agent trigger |
| `daemon/agent_runner.py` | yuuagents SDK wrapper for creating/running agents |
| `daemon/guard.py` | Rate limiting & safety guards |
| `daemon/scheduler.py` | APScheduler for cron-based proactive mode |
| `commands/tree.py` | Tree-based command matching |
| `commands/roles.py` | Role permission system |
| `commands/builtin.py` | Built-in commands (/bot, /help) |
| `skills/im/` | IM skill: send, search, browse, list |
| `skills/web/` | Web skill: search (Tavily), read (Playwright+Trafilatura), download |
| `skills/mem/` | Memory skill: save, recall, delete, show, auto-forget |

## Configuration

- `config.yaml` — Main bot config (QQ number, ports, DB path, permissions)
- `yuuagents.config.yaml` — Agent definitions (providers, personas, tools, skills)
- `.env` — Environment variables (API keys)
- `config.example.yaml` — Template for config.yaml

## Dependencies & Tooling

- Python 3.14+, managed with `uv`
- `yuuagents` is a sibling package (`../yuuagents`, editable install)
- Key deps: Click, FastAPI, Tortoise ORM, websockets, httpx, msgspec, attrs, Playwright, Trafilatura, APScheduler 4.x
- pytest with `pytest-asyncio` (asyncio_mode = "auto")
- Live tests marked with `@pytest.mark.live`

## Design Documents

Detailed design docs live in `design/`. Read these before making architectural changes:
- `architecture.md` — System overview, process design, message flow
- `design.md` — Core design principles, message format, skill list
- `daemon.md` / `recorder.md` — Per-process detailed design
- `commands.md` — Command tree, roles, built-in commands
- `skills.md` — Skill specifications (im, web, mem)
- `database.md` — SQLite schema, FTS5, concurrent access
- `config.md` — Configuration format & loading logic

## API References

External SDK docs live in `apis/`:
- `yuuagents.md` — yuuagents SDK reference
- `yuullm.md` — LLM provider reference
- `yuutools.md` — Tool framework reference

## Known Issues

### Docker 镜像构建需要代理

构建 `yuuagents-runtime` 镜像时需传入代理（bun/opencode/uv 从 GitHub 下载）：
```bash
cd ../yuuagents
docker build --no-cache \
  --build-arg http_proxy="$http_proxy" --build-arg https_proxy="$https_proxy" \
  -f src/yuuagents/daemon/runtime.Dockerfile -t yuuagents-runtime:latest .
```

运行时代理由 `DockerManager` 自动从宿主机环境继承，无需手动配置。

agent 以宿主机 UID/GID 运行（非 root），需要的工具应预装在镜像中。

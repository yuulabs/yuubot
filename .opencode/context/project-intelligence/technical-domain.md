<!-- Context: project-intelligence/technical | Priority: critical | Version: 1.0 | Updated: 2026-04-29 -->

# Technical Domain

**Purpose**: Tech stack, architecture, and development patterns for yuubot.
**Last Updated**: 2026-04-29

## Quick Reference

**Update Triggers**: Tech stack changes | New agent patterns | Architecture decisions | Character registration changes
**Audience**: Developers, AI agents

## Primary Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Language | Python 3.14 | Required by `.python-version`; modern syntax |
| CLI Framework | Click | Entrypoint via `ybot` command |
| Bot Protocol | NapCat / OneBot WebSocket | QQ message relay |
| Database | SQLite (via Tortoise ORM) | Lightweight, async; models in `core/models.py` |
| Config | llm.yaml → config.yaml → .env | Layered merge with env-var substitution |
| Type Checker | `ty` | Not mypy/pyright; config in pyproject.toml |
| Linter | ruff | `uv run ruff check src/` |
| Test Framework | pytest | `asyncio_mode=auto`, E2E only |
| Sibling Packages | yuuagents, yuullm, yuutools, yuutrace | Monorepo, one level up from repo root |
| Serialization | attrs (data), msgspec (wire) | attrs for domain objects, msgspec for JSON |

## Architecture

```
NapCat WS → recorder/relay.py → daemon/ws_client.py → dispatcher.py
  → routing.py (InboundMessage → CommandRoute)
    → commands/tree.py (click command tree) or agent_runner.py
```

**Key principle**: Message boundary at dispatcher ingress — raw OneBot event dicts → `InboundMessage` (`core/types.py`). Never pass raw events downstream.

## Code Patterns

### Message Dispatch Pipeline

```python
# daemon/dispatcher.py:124 — raw event → structured message → route → handler
async def dispatch(self, event: dict) -> None:
    if event.get("post_type") != "message":
        return
    inbound = to_inbound_message(event)  # dict → InboundMessage
    route = resolve_route(inbound, self.root, bot_qq=..., master_id=...)
    if route is None:
        route = await self._resolve_dynamic_entry_route(inbound)
```

### Pure Function Routing

```python
# daemon/routing.py:11 — pure function, no IO, no side effects
def resolve_route(msg: InboundMessage, root: RootCommand,
                  bot_qq: int, master_id: int) -> Route | None:
    """Returns CommandRoute or None. Never ConversationRoute."""
```

### Agent Function Pattern

```python
# agent_fns/im/__init__.py:130 — agents call yb.send_message(...)
async def send_message(content: Content, *, ctx_id: int | None = None) -> SendMessageResult:
    """Send text/images to QQ chat. content follows yuullm.Content schema."""
```

### Character Registration

```python
# characters/__init__.py:10 + characters/maid.py:15
register(
    Character(
        name="maid",
        spec=AgentSpec(
            tools=("execute_python", "read_file", "edit_file"),
            import_modules=(ya.PythonImport("yuubot.agent_fns", alias="yb"), ...),
            prompt_sections=(FileSection("maid/persona.md"), ...),
            delegate_policy=DelegatePolicy(allowed_agents=("general",), max_depth=1),
        ),
        bot_kind="master",
    )
)
```

## Naming Conventions

| Type | Convention | Example |
|------|-----------|---------|
| Files | snake_case | `agent_runner.py`, `ws_client.py` |
| Directories | snake_case | `agent_fns/`, `command_tree/` |
| Classes | PascalCase | `InboundMessage`, `CommandRoute`, `AgentSpec` |
| Functions | single verb preferred; snake_case when disambiguation needed | `dispatch`, `resolve_route`, `send_message` |
| Constants | UPPER_SNAKE | `CHARACTER_REGISTRY`, `PYTHON_RUNTIME_SECTION` |
| Database | snake_case | Tortoise ORM models, SQLite columns |

## Code Standards

1. Type checking via `ty` (configured in pyproject.toml `extra-paths` for sibling packages)
2. Lint with `ruff check src/` (run: `uv run ruff check src/`)
3. Test with `pytest` — E2E only, `asyncio_mode=auto` (run: `uv run pytest`)
4. Config loading: `load_config()` merges llm.yaml (base) → config.yaml (override) → `.env` substitution
5. Pure functions preferred for routing and config loading (no IO, no side effects)
6. `attrs` for domain data classes, `msgspec` for JSON serialization
7. Full type annotations — Python 3.14+ modern syntax
8. Never commit secrets (`.env`, `credentials.json`, `config.yaml`)
9. Function naming: prefer single verbs (`dispatch`, `load`, `resolve`), use snake_case only when a single verb would cause semantic conflict (`resolve_route`, `send_message`)

## Security Requirements

- Config secrets via `.env` (never committed to git)
- Agent sandboxing: group sessions use restricted sandbox per turn; master uses long-lived kernel
- Input validation at message boundary: raw dict → `InboundMessage` via `to_inbound_message()`
- Bot ignores own messages (self_id check in `dispatcher.py:127`)
- Agent functions run in sandboxed Python sessions with restricted imports

## Project Structure

```
yuubot/
├── src/yuubot/
│   ├── agent_fns/       # Functions agents call (im, mem, schedule, delegate, vision, mate)
│   ├── characters/      # Character definitions + registry
│   ├── commands/        # Click command tree
│   ├── core/            # Types, models, OneBot protocol
│   ├── daemon/          # WS client, dispatcher, routing
│   ├── prompt/          # AgentSpec, Character, prompt sections
│   ├── services/        # Domain services (im, etc.)
│   ├── config.py        # Config loading
│   └── cli.py           # ybot entrypoint
├── tests/               # E2E tests (conftest.py has key fixtures)
├── pyproject.toml
└── AGENTS.md            # Agent instructions
```

## 📂 Codebase References

| Pattern | Location |
|---------|----------|
| Dispatch pipeline | `src/yuubot/daemon/dispatcher.py:124` |
| Routing (pure function) | `src/yuubot/daemon/routing.py:11` |
| Agent function send_message | `src/yuubot/agent_fns/im/__init__.py:130` |
| Character registry | `src/yuubot/characters/__init__.py:10` |
| Character example | `src/yuubot/characters/maid.py:15` |
| InboundMessage type | `src/yuubot/core/types.py` |
| Config loading | `src/yuubot/config.py` |
| IM service | `src/yuubot/services/im.py` |
| CLI entrypoint | `src/yuubot/cli.py` |
| Test fixtures | `tests/conftest.py` |
| Agent instructions | `AGENTS.md` |

## Related Files

- `AGENTS.md` — Critical gotchas, commands, architecture overview

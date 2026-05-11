# AGENTS.md

## Critical gotchas

- **Python 3.14** (`.python-version`, `requires-python = ">=3.14"`). Agents defaulting to 3.11–3.12 will fail.
- **`config.yaml` is gitignored** — clone won't work without copying `config.example.yaml` → `config.yaml`. Same for `.env`.
- **Type checker is `ty`**, not mypy/pyright. `uv run ty check`.
- **Monorepo**: sibling packages (`yuuagents`, `yuullm`, `yuutools`, `yuutrace`) live in `../` from repo root. `ty check` resolves them via `extra-paths` in pyproject.toml. If type-check errors appear in these packages, the source is one level up.
- **No `ConversationRoute` type exists** despite older docs. Routing returns only `CommandRoute` (with `command_path=("llm",)` for agent triggers) or `None`.
- **Docker is not production-hardened yet**: Admin is published by compose and currently includes unauthenticated file/terminal surfaces when `admin.secret` is empty or not enforced. Keep it local-only unless `issues/013-docker-deploy-hardening.md` is resolved.

## Commands

```bash
uv run ruff check src/          # lint
uv run ty check                 # type check
uv run pytest                   # all tests (async by default via asyncio_mode=auto)
uv run pytest -k "pattern" -v   # single test
uv run pytest --markers         # list markers; @pytest.mark.live skips external services
```

Tests are end-to-end only (no unit tests). Key fixtures in `tests/conftest.py`: `db` (temp SQLite), `yuubot_config` (programmatic Config), `make_group_event` / `make_private_event` (OneBot event builders).

## Config loading

`load_config()` merges **llm.yaml (base) → config.yaml (override)**, then env-var substitution (`${VAR}`) via `.env`. LLM-provider config lives in `llm.yaml`; operational config in `config.yaml`. The key `agent_llm_refs` maps agent names to `"provider/model"` refs.

## Architecture

- **`src/yuubot/cli.py`** is the real entrypoint (`ybot` command via `[project.scripts]`). `main.py` is a stub.
- **Message boundary**: raw OneBot event dicts → `InboundMessage` (`core/types.py`) at the dispatcher ingress. Never pass raw events downstream.
- **Routing** (`daemon/routing.py`): pure function `resolve_route()` → `CommandRoute | None`. LLM-triggered conversations are `CommandRoute(command_path=("llm",), entry="@")` or `entry="master"`.
- **Agent functions** (`agent_fns/`): agents use `import yb; yb.send_message(...)` via execute_python tool. HTTP POST → `/agent-fns/{service}/{action}` → `services/`.
- **Dual Python backends**: `master` uses long-lived kernel sessions; `group` uses sandboxed restricted sessions per turn.
- **Characters** store a complete `system_prompt` explicitly. Prompt Templates are UI editing aids only — no hidden runtime injections.

## Request flow

```
NapCat WS → recorder/relay.py → daemon/ws_client.py → dispatcher.py
  → routing.py (InboundMessage → CommandRoute)
    → commands/tree.py (click command tree) or agent_runner.py
```

## Debugging

```bash
# Logs
cd ~/.local/share/yuubot-docker
# Conversation traces (span-based DB)
uv run python scripts/conv.py          # list recent
uv run python scripts/conv.py -l -n    # latest, compact
uv run python scripts/conv.py ID       # by short ID prefix
```


非必要不使用frozen类。

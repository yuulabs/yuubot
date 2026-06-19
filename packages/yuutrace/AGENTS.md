# Repository Guidelines

## Project Structure & Module Organization

`yuutrace` is a Python SDK plus a React UI for LLM observability. Python code lives in `src/yuutrace/`, with tests in `tests/` and runnable examples in `examples/`. The UI is a separate Vite app under `ui/`; its library output is built into `ui/dist/lib`, while the standalone `ytrace ui` bundle is built into `ui/dist/app`. The CLI entry point is `ytrace`.

## Build, Test, and Development Commands

Use `uv sync` from the workspace root to install Python dependencies. Common package-local commands:

```bash
uv run ytrace --help
uv run ytrace server --db ./traces.db --port 4318
uv run ytrace ui --db ./traces.db --port 8080
uv run pytest
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

For the UI:

```bash
cd ui
npm ci
npm run typecheck
npm run build
npm run dev
```

Run `bash scripts/build_ui.sh` after changing UI code that affects packaged static assets.

## Coding Style & Naming Conventions

Follow the existing Python style: 4-space indentation, `from __future__ import annotations`, type hints on public APIs, and `msgspec.Struct` for serializable data. Prefer `snake_case` for functions and modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Keep OTEL attribute keys centralized in the SDK, not duplicated across callers. For UI code, use TypeScript `PascalCase` components, `camelCase` helpers, and double-quoted strings.

## Testing Guidelines

`pytest` is the primary test runner. Name tests `tests/test_*.py` and keep async tests compatible with `pytest-asyncio`. Favor focused tests around span recording, serialization, and CLI behavior. For UI changes, run `npm run typecheck` and the relevant build command. Use the example script in `examples/weather_agent.py` for manual smoke checks when trace output changes.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects with Conventional Commit prefixes such as `feat:`, `fix:`, `refactor(context):`, and `chore:`. Keep changes scoped to one package when possible. PRs should explain the behavior change, list verification commands, and call out any schema, OTEL attribute, or packaged-asset impact. Include screenshots only when the UI changes.

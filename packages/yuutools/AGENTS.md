# Repository Guidelines

## Project Structure & Module Organization

`yuutools` is a small Python library in `src/yuutools/` with a narrow public API exposed from `src/yuutools/__init__.py`. Core implementation lives in `_tool.py`, `_spec.py`, `_schema.py`, and `_depends.py`. Tests live in `tests/` and focus on the public decorator, registry, and schema conversion behavior.

## Build, Test, and Development Commands

Use `uv` from the package root:

```bash
uv sync
uv run pytest
uv run pytest tests/test_core.py -v
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv build
```

Install the library with `uv add yuutools`, or include the YAML extra with `uv add 'yuutools[yaml]'`.

## Coding Style & Naming Conventions

Target Python 3.12+. Use 4-space indentation, `from __future__ import annotations`, and standard type hints like `list[int]` and `str | None`. Keep functions and modules in `snake_case`, classes in `PascalCase`, and constants in `UPPER_SNAKE_CASE`. Prefer `attrs.define(slots=True)` for runtime classes and keep the public API surface small and explicit.

## Testing Guidelines

The test suite uses `pytest` with `pytest-asyncio` for async execution. Name tests `tests/test_*.py` and keep them close to the behavior they cover. Add regression tests for spec generation, dependency injection, and `ToolManager` lookup rules when changing core behavior. Run the full package suite before opening a PR.

## Commit & Pull Request Guidelines

Recent history in this repo uses short imperative commits with Conventional Commit prefixes such as `chore:`, `feat:`, and `fix:`. Keep commits focused on one change. PRs should describe the user-visible effect, mention any API or schema changes, and list the verification commands you ran.

# packages/yuutools

Explicit, async-first tool framework for LLM agents. Tiny, narrow public API —
a `@tool` decorator, a `ToolSpec`/`ParamSpec` model, a `ToolManager` registry
with dependency-injection lookup, and a `depends()` marker for runtime deps.
Public exports live in `src/yuutools/__init__.py`.

This package is a workspace member of the monorepo at the repo root. It has no
runtime dependency on `yuubot` or `yuuagents`; `yuuagents.tool.primitives` is a
separate, host-facing tool type system that can interoperate with this one.

## Source Map (`src/yuutools/`)

| Path | Responsibility |
|---|---|
| `__init__.py` | Public API surface: `tool` (decorator), `Tool`, `BoundTool`, `ToolSpec`, `ParamSpec`, `ToolManager`, `depends`, `DependencyMarker`. |
| `_tool.py` | `Tool` / `BoundTool` — wraps a Python callable into a tool with a spec; binding resolves `depends()` params into a runnable async handler. |
| `_spec.py` | `ToolSpec`, `ParamSpec` — the declared shape of a tool (name, description, params) independent of the callable. |
| `_schema.py` | Spec → JSON-schema conversion for LLM tool-call schemas. |
| `_depends.py` | `depends(marker)` / `DependencyMarker` — declares a runtime dependency the `ToolManager` resolves at call time (no global state). |
| `_manager.py` | `ToolManager` — registry + lookup: holds specs and dependency providers, dispatches a tool call to the right `BoundTool`. |

## Execution model (quick reference)

```text
@tool
def search(query: str, http: HttpClient = depends(HttpClient)) -> str: ...
  → registers a ToolSpec (name, params schema) + a callable
    → ToolManager.bind(providers={HttpClient: instance}) → BoundTool
      → ToolManager.invoke(name, args) → resolves depends() → runs callable → result
```

Invariants to preserve:

- No global state — all runtime deps flow through `ToolManager` providers.
- `ToolSpec`/`ParamSpec` are the only thing an LLM ever sees; the Python
  callable is an implementation detail hidden behind `BoundTool`.
- Schema conversion (`_schema.py`) must round-trip `ParamSpec` to JSON Schema
  and back losslessly for the param types we declare.

## Commands

```bash
uv sync                 # from monorepo root
uv run pytest           # from this package directory
uv run pytest tests/test_core.py -v
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv build
```

Install with `uv add yuutools`, or the YAML extra `uv add 'yuutools[yaml]'`.

## Coding style

Python 3.12+. `from __future__ import annotations`, `list[int]` / `str | None`
hints, `snake_case` modules / `PascalCase` classes / `UPPER_SNAKE_CASE` consts.
Prefer `attrs.define(slots=True)` for runtime classes. Add regression tests for
spec generation, dependency injection, and `ToolManager` lookup rules when
changing core behavior.

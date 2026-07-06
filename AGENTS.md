# Project Notes

## Environment

- This Python project is managed with `uv`.
- Use `uv sync` to install or refresh dependencies.
- Run project commands through `uv run`; do not call tools from the virtualenv directly unless there is a specific reason.
- CLI entry points: `ybot` and `yuubot`.

## Project Shape

```
src/yuubot/   # runtime backend
src/yb/       # office/task helpers
src/yext/     # extension integrations
tests/        # backend tests
web/          # React admin UI
design/       # architecture notes
```

## Common Commands

```bash
uv sync
uv run ybot serve config.example.yaml
uv run ybot chat config.example.yaml amy "hello"
uv run pytest -q
cd web && pnpm install && pnpm run build
```

## Development Expectations

- Keep changes small and consistent with the existing modules.
- Preserve strict typing expectations from `pyproject.toml`.
- Add or update focused tests when behavior changes.

## Prompt Visibility

- Ensure that any function/tool descriptions defined in the framework are successfully injected into the LLM's system prompt or existing in tool specs and can be accurately understood(not too verbose or too concise).

DEBUG: 找config example yaml里面的数据库/log
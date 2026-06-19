# yuubot app

Runnable yuubot application package inside the monorepo.

From the repository root:

```bash
uv sync
uv run ybot dev
```

From this package directory, run app-local checks such as:

```bash
uv run pytest
uv run ruff check src tests
uv run ty check
```

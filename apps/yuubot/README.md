# yuubot app

Runnable yuubot application package inside the monorepo.

From the repository root:

```bash
uv sync
cp apps/yuubot/config.example.yaml config.yaml
uv run ybot --config config.yaml dev
```

`config.yaml` contains only bootstrap settings. Runtime resources are stored in
the resource DB and managed through Admin/API surfaces.

From this package directory, run app-local checks such as:

```bash
uv run pytest
uv run ruff check src tests
uv run ty check
```

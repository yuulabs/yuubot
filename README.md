# yuubot

Configurable AI Agent platform host with a daemon, Admin UI, actor runtime, and
integration capability system.

## Quickstart

```bash
git clone git@github.com:yuulabs/yuubot.git
cd yuubot
uv sync
cp apps/yuubot/config.example.yaml config.yaml
uv run ybot --config config.yaml dev
```

The development server uses the monorepo workspace so the yuubot app and local
YuuLabs packages resolve together without `../` path dependencies.
`config.yaml` is bootstrap-only; runtime resources are managed through the
resource DB and Admin/API surfaces.

## Scenario: First Local Run

```text
You clone github.com/yuulabs/yuubot
  → uv sync creates one workspace environment at the repo root
    → internal packages resolve from packages/* through workspace=true
      → uv run ybot --config config.yaml dev launches apps/yuubot's CLI entry point
        → daemon and Admin UI start with the local application code
```

## Repository Layout

- `apps/yuubot/` — runnable yuubot app, Admin UI, Docker files, and app tests.
- `packages/yuullm/` — streaming LLM abstraction.
- `packages/yuutools/` — explicit tool framework for agents.
- `packages/yuutrace/` — LLM observability SDK and UI.
- `packages/yuuagents/` — agent runtime used by yuubot.

PyPI publishing setup is not part of this migration; it remains a roadmap
milestone.

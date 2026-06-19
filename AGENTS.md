# yuubot Monorepo

This repository is the canonical `yuubot` development root. The Git root, uv
workspace root, CI root, and YuuCoder worktree root are the same directory.

## Scenario: Local Bot Development

```text
Developer clones github.com/yuulabs/yuubot
  → enters the repository root
    → uv sync resolves apps/yuubot plus packages/* through workspace sources
      → uv run ybot --config config.yaml dev starts the yuubot app from apps/yuubot
```

## Layout

- `apps/yuubot/` — yuubot application, Admin UI, Docker files, app tests.
- `packages/yuullm/` — provider-agnostic streaming LLM interface.
- `packages/yuutools/` — explicit async-first tool framework.
- `packages/yuutrace/` — OpenTelemetry-based LLM observability SDK and UI.
- `packages/yuuagents/` — agent runtime primitives used by yuubot.

Do not migrate or restore the legacy `yuubot` v1 repository into this tree.

## YuuCoder Worktree Rule

YuuCoder must create worktrees from this monorepo root, not from a subpackage:

```bash
git worktree add .tmp/<task>/<slug>/worktrees/<branch-name> <base-branch>
```

All implementation, verification, commits, and PR documents for yuubot tasks
happen inside that monorepo-root worktree. Do not create isolated worktrees from
`apps/yuubot/` or `packages/*`; those checkouts would not contain the full uv
workspace dependency graph.

## Commands

Run workspace-level commands from the repository root:

```bash
uv sync
uv run ruff check
uv run ty check
uv run ybot --config config.yaml dev
```

Run package-local tests from the package directory when validating one member:

```bash
cd apps/yuubot && uv run pytest
cd packages/yuullm && uv run pytest
cd packages/yuutools && uv run pytest
cd packages/yuutrace && uv run pytest
cd packages/yuuagents && uv run pytest
```

## Frontend Worktree Cache Rule

YuuCoder worktrees may share frontend dependency caches, but must not share
frontend build outputs. Each worktree owns its own `dist/` directory because
`dist/` reflects that worktree's current source snapshot.

### Scenario: Cached Admin UI Build

```text
YuuCoder creates .tmp/<task>/<slug>/worktrees/<branch-name>/
  → enters apps/yuubot/web inside that worktree
    → installs dependencies using .tmp/cache/pnpm-store from the monorepo root
      → pnpm reuses cached packages instead of downloading from scratch
        → pnpm run build generates this worktree's own apps/yuubot/web/dist/
```

Use shared cache directories under the monorepo root:

- `.tmp/cache/pnpm-store/` — pnpm package store for `apps/yuubot/web`.
- `.tmp/cache/npm/` — npm cache for `packages/yuutrace/ui`.

Do not copy or symlink `node_modules/` between worktrees. Use the package
manager cache instead. Do not copy or reuse `dist/` between worktrees.

For the yuubot Admin UI:

```bash
YUUBOT_ROOT=$(git rev-parse --show-toplevel)
cd apps/yuubot/web
pnpm install --store-dir "$YUUBOT_ROOT/.tmp/cache/pnpm-store"
pnpm run build
```

For the yuutrace UI:

```bash
YUUBOT_ROOT=$(git rev-parse --show-toplevel)
cd packages/yuutrace/ui
npm ci --cache "$YUUBOT_ROOT/.tmp/cache/npm"
npm run build
```

Cache directories under `.tmp/cache/` are local developer artifacts. Do not
commit them, force-add them, or treat them as release inputs.

## Developer WIP Material

`warroom/` and `apps/yuubot/warroom/` are intentionally ignored local developer
notes. They may be copied between worktrees for continuity, but must not be
force-added or treated as tracked project documentation.

## Roadmap

PyPI publishing and credential / Trusted Publishing migration are roadmap items.
Do not implement release credential changes as part of layout migration work.

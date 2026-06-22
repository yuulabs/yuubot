# yuubot Roadmap v2

**Created**: 2026-06-18  
**Moved into `roadmap/`**: 2026-06-20

This roadmap is the stable direction for the current yuubot monorepo landing
work. Detailed execution notes remain in `warroom/landing-plan/`.

## Current Status

**Repository migration**: done. The old `agent-kits/yuubot-v2` landing plan has
been moved into this monorepo.

**Phase 1**: done. Docker/backend/Admin UI conversation flow has reached the
usable baseline:

```text
LLM Backend -> Character -> Capability Set -> Actor -> Admin Conversation -> Agent reply
```

Final Phase 1 notes are in
`warroom/landing-plan/phase1-docker-instructions.md`.

**Phase 2**: accepted implementation shape updated. Workspace file access is
implemented as builtin capabilities backed by yuuagents tools:

```text
CapabilitySet selects builtin.read / builtin.edit / builtin.write
  -> yuubot maps selected builtin capabilities to yuuagents tool configs
    -> yuuagents registers read/edit/write with workspace_root
      -> read returns UTF-8 text or multimodal image content
      -> edit replaces exactly one old_string match
      -> write writes UTF-8 text under the workspace
```

Detailed Phase 2 status and follow-up validation live in
`warroom/landing-plan/phase2-workspace-read-edit-instructions.md`.

## Execution Order

```text
Phase 1: Docker usable ✅ DONE
  |
  -> Phase 2: Workspace + builtin read/edit/write ✅ IMPLEMENTED, E2E VALIDATION PENDING
        |
        +-> Phase 3: GitHub Integration
        |
        +-> Phase 4: HTML Canvas
              |
              -> Phase 5: Cost Guard  <- required before serious daily use
                    |
                    -> Phase 6: Lark/Feishu Integration
```

Finish and validate one phase before starting the next. Phase 3 and Phase 4
both depend on Phase 2 being accepted.

## MVP Definition

Phase 4 (Canvas) completes the practical MVP. Phase 5 (Cost Guard) is required
before serious daily use because users must be able to see spend.

## Key Decisions

1. **Workspace file tools are builtin capabilities**. They are selected through
   Capability Sets as `builtin.read`, `builtin.edit`, and `builtin.write`, then
   assembled into yuuagents tool configs with `workspace_root`.

2. **Workspace file tools are workspace-scoped**. Relative paths resolve under
   the actor workspace. Absolute paths and `..` escapes are rejected. This is
   the accepted implementation contract for Phase 2, replacing the older draft
   that allowed absolute paths.

3. **Docker completion means end-to-end Admin Conversation usability**. The bar
   is not just containers starting; the user must be able to complete the local
   path from API key setup through agent reply.

4. **Cost Guard is not parallel work**. It follows Canvas as Phase 5.

5. **`docker-compose.yml` does not include napcat for MVP**. MVP uses Admin
   Conversation and does not depend on IM.

6. **Feishu/Lark uses `lark-oapi` SDK**. Do not hand-roll websocket connection
   and token refresh plumbing.

## Instruction Files

| Phase | File | Branch | Dependency |
| --- | --- | --- | --- |
| 1 | `warroom/landing-plan/phase1-docker-instructions.md` | `feature/docker-landing` + follow-up fixes | none |
| 2 | `warroom/landing-plan/phase2-workspace-read-edit-instructions.md` | `feature/workspace-read-edit` / landed builtin file tools | Phase 1 |
| 3 | `warroom/landing-plan/phase3-github-integration-instructions.md` | `feature/github-integration` | Phase 2 |
| 4 | `warroom/landing-plan/phase4-html-canvas-instructions.md` | `feature/html-canvas` | Phase 2 |
| 5 | `warroom/landing-plan/phase5-cost-guard-instructions.md` | `feature/cost-guard` | Phase 4 |
| 6 | `warroom/landing-plan/phase6-lark-integration-instructions.md` | `feature/lark-integration` | Phase 5 |

## Verification Standard

- `uv run ruff check`
- package-local `uv run pytest` for changed package areas
- focused frontend build when Admin UI changes: `pnpm run build` from
  `apps/yuubot/web`
- manual E2E when a phase changes user-facing behavior:

```text
docker compose up or uv run ybot --config config.yaml dev
  -> Admin UI operation
    -> expected user-visible result
```

## Constraints

- Do not write new design docs unless documenting implemented code.
- Do not touch the plugin system for landing-plan phases.
- Do not expand framework contracts/core/registry unless a concrete integration
  requires it.
- Do not write skills/meta-guidelines files as part of landing work.

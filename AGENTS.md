# AGENTS.md

## Commands

```bash
uv run ruff check src tests
uv run ty check
uv run pytest
uv run ybot check
uv run ybot daemon
uv run ybot admin
uv run ybot dev
```

Python is 3.14. Type checking uses `ty`.

## Navigation Map

### Product Direction

- `design/checklist.md` — current hand-written product and architecture checklist.
- `demo/` — static Web Admin UI exploration pages.
- `config.example.yaml` — bootstrap config shape for local runtime startup.

### Entrypoints

- `src/yuubot/cli.py` — `ybot` command group: config validation, daemon/admin processes, dev launcher, archive import/export.
- `src/yuubot/runtime/daemon/app.py` — daemon assembly, ASGI routes, service lifecycle, resource refresh, plugin ingest.
- `src/yuubot/runtime/admin/app.py` — admin ASGI routes, integration kind metadata, secret reveal, plugin install/uninstall, trace UI mount.
- `src/yuubot/runtime/process.py` — local service host, uvicorn server wrapper, trace service, shared resource opening.
- `src/yuubot/runtime/archive.py` — data directory archive import/export.
- `src/yuubot/runtime/plugin_manager.py` — external integration plugin manifest, install, subprocess lifecycle, HTTP facade calls.

### Bootstrap And Layout

- `src/yuubot/bootstrap/config.py` — typed bootstrap config, env substitution, path expansion, startup validation.
- `src/yuubot/bootstrap/layout.py` — canonical `data_dir` layout for database, traces, logs, facades, plugins, integrations, workspaces, and skills.

### Resource Storage

- `src/yuubot/resources/records.py` — persisted msgspec resource records.
- `src/yuubot/resources/store/models.py` — Tortoise ORM models generated from resource records.
- `src/yuubot/resources/repository.py` — CRUD boundary with table-level resource change events.
- `src/yuubot/resources/service.py` — domain operations for resource CRUD and runtime reconciliation.
- `src/yuubot/resources/registry.py` — resource type registry and event-driven refresh dispatcher.
- `src/yuubot/resources/orm.py` — record/ORM conversion, references, encrypted secret handling.
- `src/yuubot/core/secrets.py` — `Secret` wrapper, master key validation, AES-GCM codec, schema hooks, config secret wrapping.

### Messages And Routing

- `src/yuubot/core/messages.py` — `IncomingMessage`, `MessageSource`, system source helpers.
- `src/yuubot/core/gateway.py` — integration ingress, actor mailboxes, message fanout.
- `src/yuubot/core/routing.py` — source glob route projection and resolution.
- `src/yuubot/core/bindings.py` — actor resource binding loader.

### Integrations

- `src/yuubot/core/integrations/contracts.py` — integration factory/instance/storage protocols and admin-facing kind metadata.
- `src/yuubot/core/integrations/registry.py` — builtin and external integration factory registry and route collection.
- `src/yuubot/core/integrations/core.py` — integration enable/disable/reconcile, capability indexing, actor capability authorization, storage cleanup.
- `src/yuubot/core/integrations/context.py` — invocation context for actor-visible capability calls.
- `src/yuubot/core/integrations/impls/echo.py` — loopback integration used by runtime and HTTP E2E tests.
- `src/yuubot/core/integrations/impls/echo_routes.py` — HTTP ingress routes for the echo integration.

### Actors And Agents

- `src/yuubot/core/actors/contracts.py` — actor and actor factory protocols.
- `src/yuubot/core/actors/manager.py` — actor lifecycle, reconciliation, mailbox ownership.
- `src/yuubot/core/actors/registry.py` — actor factory registry.
- `src/yuubot/core/actors/workspace.py` — actor workspace path resolution.
- `src/yuubot/core/actors/impls/simple_loop.py` — default yuuagents-backed actor runtime.
- `src/yuubot/core/actors/impls/python_session.py` — generated facade package lifecycle and execute_python bridge.
- `src/yuubot/core/actors/impls/echo.py` — test actor for integration/facade plumbing.
- `src/yuubot/core/assembly.py` — yuuagents `Stage` and `AgentDefinition` assembly from an `ActorBinding`.
- `src/yuubot/core/llm.py` — bound LLM data shape.
- `src/yuubot/core/costing.py` — pricing-aware LLM client wrapper.
- `src/yuubot/core/observability.py` — trace context registration.

### Actor Facade

- `src/yuubot/core/facade/codegen.py` — generated Python facade source for actor capability access.
- `src/yuubot/core/facade/bridge.py` — facade RPC server and background task mailbox messages.
- `src/yuubot/core/facade/client.py` — generated facade client request helpers.
- `src/yuubot/core/facade/workspace.py` — generated package paths and startup code.

### Daemon APIs

- `src/yuubot/runtime/daemon/commands.py` — `/api/resources` CRUD and lifecycle HTTP handlers.
- `src/yuubot/runtime/daemon/validators.py` — resource reference and deletion validation.
- `src/yuubot/runtime/http_utils.py` — shared JSON error response helper.

### Tests

- `tests/__init__.py` — makes `tests` a proper Python package (required for relative imports across subdirectories).
- `tests/test_daemon_commands.py` — resource API CRUD and validation.
- `tests/test_daemon_refresh_api.py` — admin-triggered daemon refresh.
- `tests/test_actor_lifecycle.py` — actor start/stop/reconcile behavior.
- `tests/test_actor_workspace.py` — workspace path behavior.
- `tests/test_integration_actor_echo.py` — integration-to-actor facade path.
- `tests/test_echo_http_e2e.py` — HTTP ingress round trip.
- `tests/test_route_bindings.py` — Gateway route matching.
- `tests/test_trace_cost_e2e.py` — trace and pricing plumbing.
- `tests/test_external_plugin.py` — external plugin manifest and subprocess integration.
- `tests/test_archive_export_import.py` — data archive import/export.

#### LLM Prompt Visibility Tests

These tests verify that the LLM sees the right information in its prompt — tool specs, system prompt content, capability imports — by running **scripted scenarios** where a fake LLM records every invocation while the test asserts on the captured content.

```
tests/llm_prompt/
├── scenario.py             # Core types: PromptScenario, ScenarioStep, ToolCall, PromptSnapshot, assertion builders
├── framework.py            # PromptCapture (scripted LLM provider) + ScenarioRunner
├── scenarios/              # One file per feature being tested
│   └── execute_python_visibility.py
└── test_all_scenarios.py   # Parameterized entry: add new scenarios to ALL_SCENARIOS list
```

**Pattern** — Each scenario declares an **Assert → Action → Assert → …** chain:

- **Assert** checks the current prompt snapshot (e.g. `AssertToolExists("execute_python")`).
- **Action** simulates the LLM calling a tool (e.g. `ToolCall("execute_python", {"code": "..."})`). The daemon processes it, appends the result to the LLM history, and the next assert checks the new state.

Format a new scenario:

```python
class MyFeatureVisibility(PromptScenario):
    @property
    def name(self) -> str: ...

    async def setup(self, ctx: ScenarioContext) -> None:
        # start daemon, insert resources, start actor, send trigger message
        ctx.daemon = daemon  # runner stops it after assertions

    def steps(self) -> list[ScenarioStep]:
        return [
            ScenarioStep(assertion=AssertToolExists("execute_python")),
            ScenarioStep(
                assertion=AssertHistoryContains("expected text"),
                action=ToolCall("execute_python", {"code": "print(...)"}),
            ),
        ]
```

Then add `MyFeatureVisibility()` to `ALL_SCENARIOS` in `test_all_scenarios.py` — the test runner picks it up automatically.

**Run**: `uv run pytest tests/llm_prompt/`

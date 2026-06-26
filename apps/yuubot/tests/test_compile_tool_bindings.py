"""Unit tests for the tool compiler (§3) and ToolFactory.derive (§6).

Covers:

* ``compile_tool_bindings`` — the pure 1:1 compiler that turns
  ``ToolSelection`` entries into ``ToolBinding`` objects via
  ``ToolFactory.derive``.
* ``ExecutePythonToolFactory.derive`` — context-driven config assembly
  (no real kernel spawn; we test config shape, not execution).
* ``BashToolFactory.derive`` / ``ReadToolFactory.derive`` — workspace_root
  propagation.
* ``CapabilitySetRecord`` round-trip through the resource repository
  (insert → get → compare) for the new data model (§2.7).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yuubot.core.assembly._compiler import (
    ToolDeriveContext,
    compile_tool_bindings,
)
from yuubot.core.tools import ToolRegistry, default_tool_factories
from yuubot.core.tools.impls.bash import BashToolFactory
from yuubot.core.tools.impls.execute_python import ExecutePythonToolFactory
from yuubot.core.tools.impls.file_tools import ReadToolFactory
from yuubot.core.tools.impls.restart_kernel import RestartKernelToolFactory
from yuubot.resources.records import (
    CapabilitySetRecord,
    LoopPolicy,
    ToolSelection,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _context(workspace: str = "/tmp/test-ws") -> ToolDeriveContext:
    return ToolDeriveContext(
        workspace_path=workspace,
        venv_python="/tmp/test-ws/.venv/bin/python",
        facade=None,
        actor_id="actor-1",
        agent_name="agent-1",
        session_id="session-1",
        mailbox_id="mailbox-1",
    )


# ── compile_tool_bindings ────────────────────────────────────────────────────


def test_compile_empty_selections_returns_empty() -> None:
    registry = default_tool_factories()
    bindings = compile_tool_bindings([], _context(), registry)
    assert bindings == []


def test_compile_bash_factory_derives_workspace_root() -> None:
    registry = ToolRegistry()
    registry.register(BashToolFactory())
    context = _context(workspace="/custom/ws")

    bindings = compile_tool_bindings(
        [ToolSelection(tool_name="bash")],
        context,
        registry,
    )

    assert len(bindings) == 1
    assert bindings[0].tool_name == "bash"
    assert bindings[0].config.workspace_root == "/custom/ws"


def test_compile_read_edit_write_workspace_root() -> None:
    registry = ToolRegistry()
    from yuubot.core.tools.impls.file_tools import (
        EditToolFactory,
        WriteToolFactory,
    )
    registry.register(ReadToolFactory())
    registry.register(EditToolFactory())
    registry.register(WriteToolFactory())
    context = _context(workspace="/rw/ws")

    bindings = compile_tool_bindings(
        [
            ToolSelection(tool_name="read"),
            ToolSelection(tool_name="edit"),
            ToolSelection(tool_name="write"),
        ],
        context,
        registry,
    )

    assert len(bindings) == 3
    for binding in bindings:
        assert binding.config.workspace_root == "/rw/ws"


def test_compile_restart_kernel_returns_default_config() -> None:
    registry = ToolRegistry()
    registry.register(RestartKernelToolFactory())
    context = _context()

    bindings = compile_tool_bindings(
        [ToolSelection(tool_name="restart_kernel")],
        context,
        registry,
    )

    assert len(bindings) == 1
    assert bindings[0].tool_name == "restart_kernel"
    # RestartKernelConfig has no required constructor args; just verify it's
    # an instance of the factory's config_schema.
    assert isinstance(bindings[0].config, RestartKernelToolFactory().config_schema)


def test_compile_unknown_tool_raises_lookup_error() -> None:
    registry = default_tool_factories()
    with pytest.raises(LookupError, match="not registered"):
        compile_tool_bindings(
            [ToolSelection(tool_name="nonexistent")],
            _context(),
            registry,
        )


# ── ExecutePythonToolFactory.derive ──────────────────────────────────────────


def test_execute_python_derive_without_facade() -> None:
    """derive() with no facade yields a PythonRuntime with system imports
    and data-analysis aliases but no yext.* modules."""
    factory = ExecutePythonToolFactory()
    config = factory.derive({}, _context())

    assert config.config.cwd == "/tmp/test-ws"
    assert config.config.python == "/tmp/test-ws/.venv/bin/python"
    # System facade imports (yb, yb.actor, yb.delegate, yb.schedule, yb.tasks, tim)
    import_modules = [imp.module for imp in config.imports]
    assert "yb" in import_modules
    assert "yb.delegate" in import_modules
    assert "tim" in import_modules
    # No yext.* modules without a facade
    assert not any(m.startswith("yext.") for m in import_modules)
    # State carries identity fields
    assert config.state["actor_id"] == "actor-1"
    assert config.state["mailbox_id"] == "mailbox-1"
    # Data-analysis aliases present in startup_code
    assert "import pandas as pd" in config.config.startup_code
    assert "import numpy as np" in config.config.startup_code
    assert 'matplotlib.use("Agg")' in config.config.startup_code


def test_execute_python_derive_with_github_facade() -> None:
    """derive() with a facade exposing github.* capabilities adds yext.github
    to imports and expand_functions."""
    import msgspec

    from yuubot.core.capabilities import CapabilitySpec
    from yuubot.core.facade.workspace import ActorFacadeBinding

    class _EmptyInput(msgspec.Struct):
        pass

    class _EmptyOutput(msgspec.Struct):
        pass

    github_cap = CapabilitySpec(
        id="github.create_issue",
        name="create_issue",
        description="Create an issue",
        input_type=_EmptyInput,
        output_type=_EmptyOutput,
        namespace="github",
        effect="write",
    )

    facade = ActorFacadeBinding(
        actor_id="actor-1",
        agent_name="agent-1",
        session_id="session-1",
        mailbox_id="mailbox-1",
        capabilities=(github_cap,),
        root=Path("/tmp/test-ws"),
        sys_path=["/tmp/test-ws/site-packages"],
        startup_code="# facade init\n",
        session_state={},
        venv_python="/tmp/test-ws/.venv/bin/python",
    )
    factory = ExecutePythonToolFactory()
    config = factory.derive(
        {},
        ToolDeriveContext(
            workspace_path="/tmp/test-ws",
            venv_python="/tmp/test-ws/.venv/bin/python",
            facade=facade,
            actor_id="actor-1",
            agent_name="agent-1",
            session_id="session-1",
            mailbox_id="mailbox-1",
        ),
    )

    import_modules = [imp.module for imp in config.imports]
    assert "yext.github" in import_modules
    assert "yext.github.*" in config.expand_functions
    assert "# facade init" in config.config.startup_code


# ── CapabilitySetRecord round-trip through the repository ────────────────────


@pytest.mark.asyncio
async def test_capability_set_record_round_trip(resources) -> None:
    """Insert a CapabilitySetRecord (new data model) and read it back,
    verifying all fields survive the ORM codec transform."""
    from yuubot.resources.store.models import CapabilitySetORM

    repo = resources.repository
    record = CapabilitySetRecord(
        id="caps-rt",
        name="test-caps",
        description="Test capability set",
        workspace_path="test-ws",
        tools=(
            ToolSelection(tool_name="bash"),
            ToolSelection(
                tool_name="execute_python",
                user_fields={"something": 42},
            ),
        ),
        integration_ids=("integration-a", "integration-b"),
        loop_policy=LoopPolicy(
            rollover_enabled=True,
            idle_timeout_s=30.0,
            summarize_steps_span=10,
        ),
    )

    inserted = await repo.insert(CapabilitySetORM, record)
    assert inserted.id != ""
    assert inserted.name == "test-caps"
    assert len(inserted.tools) == 2
    assert inserted.tools[0].tool_name == "bash"
    assert inserted.tools[1].user_fields == {"something": 42}
    assert inserted.integration_ids == ("integration-a", "integration-b")
    assert inserted.loop_policy.rollover_enabled is True
    assert inserted.loop_policy.idle_timeout_s == 30.0
    assert inserted.loop_policy.summarize_steps_span == 10

    fetched = await repo.get(CapabilitySetORM, inserted.id)
    assert fetched is not None
    assert fetched.name == "test-caps"
    assert len(fetched.tools) == 2
    assert fetched.tools[1].user_fields == {"something": 42}
    assert fetched.integration_ids == ("integration-a", "integration-b")
    assert fetched.loop_policy.summarize_steps_span == 10

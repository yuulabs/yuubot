"""restart_kernel tool: lazy kernel reset semantics (Phase 2 wiring).

Public boundary: ``yuubot.core.tools.impls.restart_kernel.RestartKernelTool``,
discovering the live ``execute_python`` tool from the yuuagents runtime
registry by name at call time (W1: no shared session-handle type), and
calling ``ExecutePythonTool.restart_session()``.

Observable outcome (Acceptance Criteria #2, #3):
- After ``restart_kernel``, prior session state is gone (NameError on the
  pre-restart global). Behavioral assertion — no mock of ``restart_session``.
- After ``restart_kernel``, a fresh ``execute_python`` re-spawns the kernel in
  the SAME workspace ``.venv`` (path unchanged).
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import cast

from yuuagents import Stage
from yuuagents.core.task import Owner, OwnerType
from yuuagents.obs import EntityLog
from yuuagents.tool.primitives import ToolCallParams, ToolCallTask, ToolContext

from yuubot.core.assembly._compiler import ToolDeriveContext
from yuubot.core.assembly._python_tool import (
    ExecutePythonParams,
    ExecutePythonTool,
)
from yuubot.core.facade import ActorFacadeBinding
from yuubot.core.facade.workspace import FacadeEndpoint, FacadeWorkspace
from yuubot.core.tools.impls.execute_python import ExecutePythonToolFactory


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _provision_facade(tmp_path: Path) -> ActorFacadeBinding:
    ws = FacadeWorkspace(root=tmp_path, package_name="yext")
    return ws.bind_actor(
        actor_id="restart-actor",
        agent_name="restart-agent",
        session_id="s",
        mailbox_id="m",
        capabilities=(),
        endpoint=FacadeEndpoint(host="127.0.0.1", port=_free_port(), token="t"),
    )


async def _fixture(
    tmp_path: Path,
) -> tuple[Stage, ActorFacadeBinding, ExecutePythonTool]:
    """Provision a real venv + assemble execute_python the way the daemon does.

    Returns the Stage (so tests can probe its registry), the facade binding
    (for ``venv_python``), and the live ExecutePythonTool instance.
    """
    facade = _provision_facade(tmp_path)
    assert facade.venv_python is not None

    stage = Stage.from_config()
    runtime = stage.runtime

    ep_config = ExecutePythonToolFactory().derive(
        {},
        ToolDeriveContext(
            workspace_path=str(facade.root),
            venv_python=facade.venv_python or "",
            facade=facade,
            actor_id=facade.actor_id,
            agent_name=facade.agent_name,
            session_id=facade.session_id,
            mailbox_id=facade.mailbox_id,
        ),
    )
    ep_tool = ExecutePythonTool.from_startup(runtime=runtime, config=ep_config)
    runtime.registry.register(ep_tool.definition, ep_tool)
    return stage, facade, ep_tool


def _ep_task(code: str, call_id: str = "ep-call") -> ToolCallTask:
    return ToolCallTask(
        id="ep-task",
        owner=Owner(type=OwnerType.AGENT, id="restart-actor"),
        coro=None,
        tool_call_params=ToolCallParams(
            tool_call_id=call_id,
            tool_name="execute_python",
            params=ExecutePythonParams(code=code),
        ),
    )


def _restart_task(call_id: str = "restart-call") -> ToolCallTask:
    from yuubot.core.tools.impls.restart_kernel import RestartKernelParams

    return ToolCallTask(
        id="restart-task",
        owner=Owner(type=OwnerType.AGENT, id="restart-actor"),
        coro=None,
        tool_call_params=ToolCallParams(
            tool_call_id=call_id,
            tool_name="restart_kernel",
            params=RestartKernelParams(),
        ),
    )


def _ctx(call_id: str) -> ToolContext:
    return ToolContext(
        agent_id="restart-actor",
        tool_call_id=call_id,
        eventbus=None,
        entity_log=EntityLog(),
    )


async def _run_ep(tool: ExecutePythonTool, code: str) -> str:
    call_id = "ep-call"
    return cast(
        str,
        await tool.create_coro(_ep_task(code, call_id), _ctx(call_id)),
    )


async def _run_restart(tmp_path: Path, restart_tool) -> str:  # type: ignore[no-untyped-def]
    call_id = "restart-call"
    return cast(
        str,
        await restart_tool.create_coro(_restart_task(call_id), _ctx(call_id)),
    )


async def test_restart_kernel_clears_prior_session_state(tmp_path: Path) -> None:
    stage, _facade, ep_tool = await _fixture(tmp_path)
    from yuubot.core.tools.impls.restart_kernel import (
        RestartKernelConfig,
        RestartKernelTool,
    )

    restart_tool = RestartKernelTool.from_startup(
        runtime=stage.runtime, config=RestartKernelConfig(),
    )

    await _run_ep(ep_tool, "SESSION_FLAG = 'before-restart'")
    sanity = await _run_ep(ep_tool, "print(SESSION_FLAG)")
    assert "before-restart" in sanity

    await _run_restart(tmp_path, restart_tool)

    post = await _run_ep(ep_tool, "print(SESSION_FLAG)")
    assert "NameError" in post and "SESSION_FLAG" in post, (
        f"post-restart kernel still saw the pre-restart global:\n{post}"
    )


async def test_restart_kernel_respawns_in_same_venv(tmp_path: Path) -> None:
    stage, facade, ep_tool = await _fixture(tmp_path)
    from yuubot.core.tools.impls.restart_kernel import (
        RestartKernelConfig,
        RestartKernelTool,
    )

    restart_tool = RestartKernelTool.from_startup(
        runtime=stage.runtime, config=RestartKernelConfig(),
    )

    first = await _run_ep(ep_tool, "import sys; print(sys.executable)")
    assert facade.venv_python in first, (
        f"kernel not running in the bound venv:\n{first}"
    )

    await _run_restart(tmp_path, restart_tool)

    second = await _run_ep(ep_tool, "import sys; print(sys.executable)")
    assert facade.venv_python in second, (
        f"post-restart venv python changed:\n  first: {first}\n  second: {second}"
    )


async def test_restart_kernel_safe_when_session_already_none(
    tmp_path: Path,
) -> None:
    """``restart_kernel`` when no session was ever started must be a no-op
    (close + null the already-None handle), not an error."""
    stage, _facade, ep_tool = await _fixture(tmp_path)
    from yuubot.core.tools.impls.restart_kernel import (
        RestartKernelConfig,
        RestartKernelTool,
    )

    restart_tool = RestartKernelTool.from_startup(
        runtime=stage.runtime, config=RestartKernelConfig(),
    )
    assert ep_tool._session is None  # never executed yet

    result = await _run_restart(tmp_path, restart_tool)  # must not raise

    assert isinstance(result, str)
    assert ep_tool._session is None


async def test_restart_kernel_lookup_resolves_execute_python_by_name(
    tmp_path: Path,
) -> None:
    """RestartKernelTool discovers execute_python via the runtime registry.

    W1 contract: discovery is by name at call time. The registry must hold
    the live ExecutePythonTool instance under ``execute_python``.
    """
    stage, _facade, ep_tool = await _fixture(tmp_path)
    _edef, resolved = stage.runtime.registry.resolve("execute_python")
    assert resolved is ep_tool

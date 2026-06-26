"""execute_python kernel runs on the workspace .venv (Phase 2 wiring).

Public boundary: ``yuubot.core.assembly._python_tool.ExecutePythonTool`` driven
through the real ``_python_tool_runtime`` config assembly that the daemon uses.

Observable outcome (Acceptance Criteria #1, #4):
- ``import sys; print(sys.executable)`` inside execute_python returns a path
  containing ``.venv`` and NOT equal to the test process' interpreter.
- ``pd``/``np``/``plt`` are usable without an explicit ``import`` in
  execute_python (the agent kernel pre-imports them via ``startup_code``).

The kernel must run for real: the venv is provisioned by the Phase 1+
``FacadeWorkspace.bind_actor`` helper (ipykernel, pandas, numpy, matplotlib,
msgspec all pre-installed), and an actual ipykernel subprocess is started.
No mocking.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from typing import cast

from yuuagents.core.task import Owner, OwnerType
from yuuagents.obs import EntityLog
from yuuagents.tool.primitives import ToolCallParams, ToolCallTask, ToolContext

from yuubot.core.assembly._compiler import ToolDeriveContext
from yuubot.core.assembly._python_tool import ExecutePythonParams, ExecutePythonTool
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
        actor_id="venv-test-actor",
        agent_name="venv-test-agent",
        session_id="s",
        mailbox_id="m",
        capabilities=(),
        endpoint=FacadeEndpoint(host="127.0.0.1", port=_free_port(), token="t"),
    )


def _make_ep_tool(facade: ActorFacadeBinding) -> ExecutePythonTool:
    config = ExecutePythonToolFactory().derive(
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
    return ExecutePythonTool.from_startup(runtime=None, config=config)


async def _run_code(tool: ExecutePythonTool, code: str) -> str:
    task = ToolCallTask(
        id="task-1",
        owner=Owner(type=OwnerType.AGENT, id="actor-1"),
        coro=None,
        tool_call_params=ToolCallParams(
            tool_call_id="call-1",
            tool_name="execute_python",
            params=ExecutePythonParams(code=code),
        ),
    )
    result = await tool.create_coro(
        task,
        ToolContext(
            agent_id="actor-1",
            tool_call_id="call-1",
            eventbus=None,
            entity_log=EntityLog(),
        ),
    )
    return cast(str, result)


async def test_execute_python_runs_in_workspace_venv(tmp_path: Path) -> None:
    facade = _provision_facade(tmp_path)
    assert facade.venv_python is not None
    tool = _make_ep_tool(facade)

    out = await _run_code(tool, "import sys; print(sys.executable)")

    assert ".venv" in out, f"kernel executable is not the workspace .venv:\n{out}"
    assert facade.venv_python in out, (
        f"expected venv python {facade.venv_python!r} in output:\n{out}"
    )
    assert sys.executable not in out, (
        f"kernel ran on the TEST/DAEMON interpreter ({sys.executable!r}) — "
        f"isolation is broken:\n{out}"
    )


async def test_pd_np_plt_prebound_without_import(tmp_path: Path) -> None:
    facade = _provision_facade(tmp_path)
    tool = _make_ep_tool(facade)

    out = await _run_code(
        tool,
        "print(pd.DataFrame.__name__)\n"
        "print(int(np.array([1, 2, 3]).sum()))\n"
        "assert plt is not None\n"
        "print('ok')",
    )

    assert "DataFrame" in out, f"pd alias missing from startup_code:\n{out}"
    assert "6" in out, f"np alias broken:\n{out}"
    assert "ok" in out, f"plt alias broken:\n{out}"


async def test_matplotlib_agg_backend_forced(tmp_path: Path) -> None:
    """The kernel bootstrap forces the Agg backend so inline auto-capture of
    figures (which produces the misleading ``<Figure ...>`` repr) is disabled."""
    facade = _provision_facade(tmp_path)
    tool = _make_ep_tool(facade)

    out = await _run_code(
        tool,
        "import matplotlib\n"
        "print(matplotlib.get_backend())",
    )

    assert "Agg" in out, (
        f"matplotlib backend is not Agg; got:\n{out}\n"
        "the startup_code must call matplotlib.use('Agg') before importing pyplot."
    )


async def test_restart_kernel_resets_session_state(tmp_path: Path) -> None:
    """After ``ExecutePythonTool.restart_session()``, prior globals are gone.

    Behavioral assertion (NameError on the post-restart reference), not a
    private-attribute probe — per the instruction's forbidden-test list.
    """
    facade = _provision_facade(tmp_path)
    tool = _make_ep_tool(facade)

    await _run_code(tool, "SESSION_SCOPED_FLAG = 'before-restart'")

    # Confirm the flag is reachable without restart (sanity: kernel is alive).
    pre = await _run_code(tool, "print(SESSION_SCOPED_FLAG)")
    assert "before-restart" in pre, f"pre-restart sanity failed:\n{pre}"

    # Restart closes the session handle; next call re-spawns lazily.
    await tool.restart_session()

    post = await _run_code(tool, "print(SESSION_SCOPED_FLAG)")
    assert "NameError" in post and "SESSION_SCOPED_FLAG" in post, (
        f"post-restart kernel still saw the pre-restart global:\n{post}"
    )


async def test_restart_kernel_respawns_in_same_venv(tmp_path: Path) -> None:
    """Regression gate (Acceptance Criterion #3): after restart, the next
    execute_python re-spawns the kernel in the SAME venv (path unchanged)."""
    facade = _provision_facade(tmp_path)
    assert facade.venv_python is not None
    tool = _make_ep_tool(facade)

    first = await _run_code(tool, "import sys; print(sys.executable)")
    assert facade.venv_python in first

    await tool.restart_session()

    second = await _run_code(tool, "import sys; print(sys.executable)")
    assert facade.venv_python in second, (
        f"post-restart venv python changed:\n  first: {first}\n  second: {second}"
    )

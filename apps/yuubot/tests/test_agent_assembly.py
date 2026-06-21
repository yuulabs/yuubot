"""Agent definition assembly: LLM-facing prompt construction and capability wiring."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import msgspec
from yuuagents.core.task import Owner, OwnerType
from yuuagents.obs import EntityLog
from yuuagents.python.runtime import PythonRuntime
from yuuagents.python.session import PythonExecResult, PythonSession
from yuuagents.tool.primitives import ToolCallParams, ToolCallTask, ToolContext
from tests.helpers import (
    make_actor_record,
    make_capability_set_record,
    make_character_record,
    make_llm_backend_record,
)
from yuubot.core.assembly._python_tool import ExecutePythonParams, ExecutePythonTool
from yuubot.core.assembly import build_agent_definition
from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import ActorFacadeBinding


def test_python_tool_facade_imports_include_supported_surfaces(tmp_path: Path) -> None:
    character = make_character_record("actor-1")
    backend = make_llm_backend_record("actor-1")
    actor = make_actor_record(
        "actor-1",
        character=character,
        llm_backend=backend,
    )
    binding = ActorBinding(actor=actor, workspace_path=tmp_path).default_agent_binding()

    no_capability_tool = build_agent_definition(
        binding,
        facade=_facade(tmp_path, capabilities=()),
    ).tools["execute_python"]
    github_tool = build_agent_definition(
        binding,
        facade=_facade(tmp_path, capabilities=(_github_capability(),)),
    ).tools["execute_python"]

    no_capability_imports = {
        item["module"]
        for item in cast(list[dict[str, str]], no_capability_tool["imports"])
    }
    github_imports = {
        item["module"] for item in cast(list[dict[str, str]], github_tool["imports"])
    }
    no_capability_expand_functions = cast(
        tuple[str, ...],
        no_capability_tool["expand_functions"],
    )
    github_expand_functions = cast(tuple[str, ...], github_tool["expand_functions"])
    assert "yb.delegate" in no_capability_imports
    assert "tim" in no_capability_imports
    assert "yb.schedule" in no_capability_imports
    assert "yext.github" not in no_capability_imports
    assert "yext.github" in github_imports
    assert "yb.delegate.*" in no_capability_expand_functions
    assert "tim.*" in no_capability_expand_functions
    assert "yb.schedule.*" in no_capability_expand_functions
    assert "yext.github.*" not in no_capability_expand_functions
    assert "yext.github.*" in github_expand_functions


def test_agent_prompt_guidance_is_mode_specific(tmp_path: Path) -> None:
    character = make_character_record("actor-1", system_prompt="Base prompt.")
    backend = make_llm_backend_record("actor-1")
    actor = make_actor_record(
        "actor-1",
        character=character,
        llm_backend=backend,
    )
    binding = ActorBinding(actor=actor, workspace_path=tmp_path).default_agent_binding()

    im_prompt = build_agent_definition(binding, mode="im").prompt.system
    conversation_prompt = build_agent_definition(
        binding,
        mode="conversation",
    ).prompt.system

    assert "tim.Channel" in im_prompt
    assert conversation_prompt.endswith("Base prompt.")


def test_execute_python_tool_renders_plain_text_result() -> None:
    result = ExecutePythonTool._render_result(
        PythonExecResult(
            status="ok",
            stdout="hello\n",
        )
    )

    assert isinstance(result, str)
    assert result == "Captured stdout:\nhello\n"
    assert "output=" not in result


async def test_execute_python_tool_reports_crash_and_resets_session() -> None:
    tool = ExecutePythonTool(
        runtime=None,
        config=PythonRuntime(state={"agent_name": "actor-1"}),
    )
    session = _CrashingPythonSession()
    tool._session = cast(PythonSession, session)

    result = await tool.create_coro(
        ToolCallTask(
            id="task-1",
            owner=Owner(type=OwnerType.AGENT, id="actor-1"),
            coro=None,
            tool_call_params=ToolCallParams(
                tool_call_id="call-1",
                tool_name="execute_python",
                params=ExecutePythonParams(code="print('hello')"),
            ),
        ),
        ToolContext(
            agent_id="actor-1",
            tool_call_id="call-1",
            eventbus=None,
            entity_log=EntityLog(),
        ),
    )

    assert "Python execution crashed:" in result
    assert "RuntimeError: startup failed" in result
    assert "The Python session was reset" in result
    assert session.closed
    assert tool._session is None


def test_builtin_capabilities_create_file_tool_configs(tmp_path: Path) -> None:
    character = make_character_record("char-1")
    llm_backend = make_llm_backend_record("llm-1")
    capability_set = make_capability_set_record(
        "actor-1",
        integration_capability_ids=(
            "builtin.read",
            "builtin.edit",
            "builtin.write",
            "builtin.bash",
        ),
    )
    actor = make_actor_record(
        "actor-1",
        character=character,
        llm_backend=llm_backend,
        capability_set=capability_set,
    )
    binding = ActorBinding(actor=actor).default_agent_binding(
        workspace_path=tmp_path / "workspace",
    )

    definition = build_agent_definition(
        binding,
        facade=_facade(tmp_path, capabilities=[]),
        workspace_path=str(tmp_path / "workspace"),
    )

    assert definition.tools["read"]["workspace_root"] == str(tmp_path / "workspace")
    assert definition.tools["edit"]["workspace_root"] == str(tmp_path / "workspace")
    assert definition.tools["write"]["workspace_root"] == str(tmp_path / "workspace")
    assert definition.tools["bash"]["workspace_root"] == str(tmp_path / "workspace")


def test_execute_python_description_can_import_github_facade(tmp_path: Path) -> None:
    character = make_character_record("char-1")
    llm_backend = make_llm_backend_record("llm-1")
    actor = make_actor_record(
        "actor-1",
        character=character,
        llm_backend=llm_backend,
    )
    binding = ActorBinding(actor=actor).default_agent_binding(
        workspace_path=tmp_path / "workspace",
    )
    definition = build_agent_definition(
        binding,
        facade=_facade(tmp_path, capabilities=(_github_capability(),)),
        workspace_path=str(tmp_path / "workspace"),
    )
    tool = ExecutePythonTool(
        runtime=None,
        config=msgspec.convert(
            definition.tools["execute_python"],
            type=PythonRuntime,
            strict=False,
        ),
    )

    description = tool.definition.description

    assert "import yext.github" in description
    assert "metadata unavailable" not in description


def test_integration_capability_prompt_explains_yext_usage(tmp_path: Path) -> None:
    character = make_character_record("char-1")
    llm_backend = make_llm_backend_record("llm-1")
    capability_set = make_capability_set_record(
        "actor-1",
        integration_capability_ids=("github.issue.list",),
    )
    actor = make_actor_record(
        "actor-1",
        character=character,
        llm_backend=llm_backend,
        capability_set=capability_set,
    )
    binding = ActorBinding(actor=actor).default_agent_binding(
        workspace_path=tmp_path / "workspace",
    )

    system = build_agent_definition(binding, mode="conversation").prompt.system

    assert "github.issue.list" in system
    assert "Non-builtin capabilities are async Python facade functions" in system
    assert "Do not call github.* capability ids as top-level tools" in system


class _CrashingPythonSession:
    closed = False

    async def execute(
        self,
        code: str,
        *,
        timeout_s: float | None = None,
        call_id: str | None = None,
        entitylog: EntityLog | None = None,
    ) -> PythonExecResult:
        return PythonExecResult(
            status="crashed",
            traceback=("RuntimeError: startup failed",),
        )

    async def close(self) -> None:
        self.closed = True


def _facade(tmp_path, *, capabilities):
    return ActorFacadeBinding(
        actor_id="actor-1",
        agent_name="actor-1",
        session_id="session-1",
        mailbox_id="actor:actor-1",
        capabilities=tuple(capabilities),
        root=tmp_path,
        sys_path=[str(tmp_path)],
        startup_code="import yb\nimport tim\nimport yext.github",
        session_state={},
    )


def _github_capability():
    from yuubot.core.integrations.impls.github.integration import (
        GITHUB_ISSUE_LIST_CAPABILITY_SPEC,
    )

    return GITHUB_ISSUE_LIST_CAPABILITY_SPEC

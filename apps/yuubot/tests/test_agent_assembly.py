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
    make_actor_binding,
    make_actor_record,
    make_capability_set_record,
    make_llm_backend_record,
)
from yuubot.core.assembly._python_tool import ExecutePythonParams, ExecutePythonTool
from yuubot.core.assembly import build_agent_definition
from yuubot.core.facade import ActorFacadeBinding
from yuubot.resources.records import ToolSelection


def test_python_tool_facade_imports_include_supported_surfaces(tmp_path: Path) -> None:
    backend = make_llm_backend_record("actor-1")
    actor = make_actor_record(
        "actor-1",
        llm_backend=backend,
    )
    binding = make_actor_binding(
        actor,
        capability_set=make_capability_set_record(
            "actor-1",
            tools=(ToolSelection("execute_python"),),
        ),
        llm_backend=backend,
        workspace_path=tmp_path,
    ).default_agent_binding()

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
    """IM-mode user-visibility guidance is rendered inside Section 2.

    Replaces the legacy ``endswith("Base prompt.")`` assertion. Under the
    five-section contract, ``tim.Channel`` is system-level IM-mode semantics
    that lives in the body of ``# System Instructions`` (only when
    ``mode == "im"``), not a separate extension section and not inside the
    integration capability section.
    """
    llm_backend = make_llm_backend_record("actor-1")
    actor = make_actor_record(
        "actor-1",
        llm_backend=llm_backend,
    )
    binding = make_actor_binding(
        actor,
        capability_set=make_capability_set_record("actor-1"),
        llm_backend=llm_backend,
        workspace_path=tmp_path,
    ).default_agent_binding()
    _write_agents_md(tmp_path, "__MARKER_AGENTS_V1__")

    im_prompt = build_agent_definition(binding, mode="im").prompt.system
    conversation_prompt = build_agent_definition(
        binding,
        mode="conversation",
    ).prompt.system

    _assert_section_order(im_prompt)

    # IM guidance lives inside Section 2 (between the System Instructions
    # header and the Integration SDKs header).
    sys_inst_pos = im_prompt.find("# System Instructions")
    integ_pos = im_prompt.find("# Integration SDKs")
    tim_channel_pos = im_prompt.find("tim.Channel")

    assert "tim.Channel" in im_prompt
    assert sys_inst_pos != -1, "missing # System Instructions header"
    assert integ_pos != -1, "missing # Integration SDKs header"
    assert tim_channel_pos != -1, "missing tim.Channel substring"
    assert sys_inst_pos < tim_channel_pos < integ_pos, (
        "tim.Channel guidance must appear inside Section 2 (System Instructions), "
        "not as a standalone section and not inside Section 3."
    )

    # Conversation mode MUST NOT carry IM-mode user-visibility semantics.
    assert "tim.Channel" not in conversation_prompt


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
    llm_backend = make_llm_backend_record("llm-1")
    capability_set = make_capability_set_record(
        "actor-1",
        tools=(
            ToolSelection("read"),
            ToolSelection("edit"),
            ToolSelection("write"),
            ToolSelection("bash"),
        ),
    )
    actor = make_actor_record(
        "actor-1",
        persona_prompt="Base prompt.",
        llm_backend=llm_backend,
        capability_set=capability_set,
    )
    binding = make_actor_binding(
        actor,
        capability_set=capability_set,
        llm_backend=llm_backend,
    ).default_agent_binding(
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
    llm_backend = make_llm_backend_record("llm-1")
    actor = make_actor_record(
        "actor-1",
        llm_backend=llm_backend,
    )
    binding = make_actor_binding(
        actor,
        capability_set=make_capability_set_record(
            "actor-1",
            tools=(ToolSelection("execute_python"),),
        ),
        llm_backend=llm_backend,
    ).default_agent_binding(
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
    """Five-section system prompt contract: section order and content.

    Under the §2.7.1 CapabilitySet model the integration section (``# Integration
    SDKs``) renders an interim placeholder: an empty line when no
    ``integration_ids`` are selected, and a concise "import yext" hint
    otherwise. Full per-integration ``IntegrationSdkSpec`` rendering is T5.

    Contract checks:
    - Persona leads the prompt; ``# Persona\nBase prompt.`` is the prefix.
    - The five visible section headers appear in canonical order.
    - No ``# Extension Section`` header is rendered (extension zone is
      code-only).
    - Section 2 documents the hand-written tool surface and workspace
      conventions; it MUST NOT duplicate the ``execute_python`` tool-spec
      description.
    - Section 3 renders the integration placeholder (no legacy mechanical
      github id→module mapping text).
    - Section 4 includes the full AGENTS.md text and the freeze note.
    - Section 5 carries platform / datetime / timezone tokens.
    - No sentinel/default object representations leak into the prompt.
    """
    llm_backend = make_llm_backend_record("llm-1")
    capability_set = make_capability_set_record(
        "actor-1",
        integration_ids=("github-main",),
    )
    actor = make_actor_record(
        "actor-1",
        persona_prompt="Base prompt.",
        llm_backend=llm_backend,
        capability_set=capability_set,
    )
    binding = make_actor_binding(
        actor,
        capability_set=capability_set,
        llm_backend=llm_backend,
        workspace_path=tmp_path,
    ).default_agent_binding()
    _write_agents_md(tmp_path, "__MARKER_AGENTS_V1__")

    system = build_agent_definition(binding, mode="conversation").prompt.system

    _assert_section_order(system)

    # Section 1 (Persona) leads the prompt.
    assert system.startswith("# Persona\nBase prompt.")

    # The extension zone is code-only; never a visible header.
    assert "# Extension Section" not in system

    section2 = _section_body(
        system, "# System Instructions", "# Integration SDKs"
    )
    section3 = _section_body(
        system,
        "# Integration SDKs",
        "# AGENTS.md Context",
    )
    section4 = _section_body(
        system,
        "# AGENTS.md Context",
        "# Real-Time Data",
    )
    section5 = (
        system.split("# Real-Time Data\n", 1)[1]
        if "# Real-Time Data\n" in system
        else ""
    )

    # Section 2 — hand-written tool-surface prose + workspace conventions.
    assert str(tmp_path.resolve()) in section2, (
        "workspace absolute path missing from Section 2"
    )
    assert "do not bypass" in section2, "'do not bypass' prose missing from Section 2"
    # Section 2 must NOT duplicate the execute_python tool-spec description.
    tool_spec_phrases = (
        "jupyter cell",
        "ipykernel",
        "Python session rules",
        "SESSION_STATE",
        "crashed",
        "crash and resets",
    )
    leaked = [phrase for phrase in tool_spec_phrases if phrase in section2]
    assert not leaked, (
        f"Section 2 duplicates execute_python tool-spec phrases: {leaked}"
    )

    # Section 3 — interim integration SDK placeholder (T5 replaces this).
    assert "yext" in section3, "integration SDK placeholder must mention yext"
    # Legacy mechanical id-to-module mapping text MUST NOT appear.
    assert "Map a capability id to yext by keeping the prefix" not in section3
    assert "Non-builtin capabilities are async Python facade functions" not in section3
    assert "github.issue.list -> await yext.github.issue.list(" not in section3

    # Section 4 — AGENTS.md context + freeze note.
    assert "__MARKER_AGENTS_V1__" in section4, "AGENTS.md marker missing from Section 4"
    assert "only affects future agent instantiations" in section4, (
        "freeze note missing from Section 4"
    )

    # Section 5 — real-time data.
    assert "Platform:" in section5, "platform token missing from Section 5"
    assert "Datetime:" in section5, "datetime token missing from Section 5"
    assert "Timezone:" in section5, "timezone token missing from Section 5"
    # Some ISO-style date substring must be present without asserting on now().
    import re

    assert re.search(r"\d{4}-\d{2}-\d{2}", section5), (
        "no ISO date substring in Section 5"
    )

    # Legacy mechanical mapping and flat guidance substrings never leak.
    assert "Map a capability id to yext by keeping the prefix" not in system
    assert "Non-builtin capabilities are async Python facade functions" not in system
    # Sentinel / default object repr guards.
    assert "representation at 0x" not in system
    assert "<object" not in system


def test_integration_sdk_section_empty_when_no_integrations(tmp_path: Path) -> None:
    """When ``integration_ids`` is empty, Section 3 renders the empty default."""
    llm_backend = make_llm_backend_record("llm-1")
    actor = make_actor_record(
        "actor-1",
        persona_prompt="Base prompt.",
        llm_backend=llm_backend,
    )
    binding = make_actor_binding(
        actor,
        capability_set=make_capability_set_record("actor-1"),
        llm_backend=llm_backend,
        workspace_path=tmp_path,
    ).default_agent_binding()

    system = build_agent_definition(binding, mode="conversation").prompt.system

    section3 = _section_body(system, "# Integration SDKs", "# AGENTS.md Context")
    assert section3 == "No integration SDKs configured."


def _write_agents_md(workspace: Path, text: str) -> None:
    """Write the marker AGENTS.md at the workspace root (used by Red tests)."""
    (workspace / "AGENTS.md").write_text(text, encoding="utf-8")


def _section_body(system: str, start_header: str, next_header: str) -> str:
    """Return the body of a single section between two header markers.

    The body excludes the leading header line (``# Header``) and ends just
    before ``next_header``. Both headers must be present.
    """
    assert start_header in system, f"missing header {start_header!r}"
    assert next_header in system, f"missing header {next_header!r}"
    start = system.find(start_header)
    next_pos = system.find(next_header, start + len(start_header))
    assert next_pos != -1, f"{next_header!r} does not follow {start_header!r}"
    # Skip the header line itself.
    body_start = start + len(start_header)
    # Skip the separating newline(s).
    return system[body_start:next_pos].strip()


def _assert_section_order(system: str) -> None:
    expected = (
        "# Persona",
        "# System Instructions",
        "# Integration SDKs",
        "# AGENTS.md Context",
        "# Real-Time Data",
    )
    positions: list[int] = []
    last = -1
    for header in expected:
        pos = system.find(header)
        assert pos != -1, f"header {header!r} not present in system prompt"
        assert pos > last, (
            f"header {header!r} at {pos} violates expected order (previous at {last})"
        )
        positions.append(pos)
        last = pos


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

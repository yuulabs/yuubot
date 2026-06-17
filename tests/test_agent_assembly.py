"""Agent definition assembly: LLM-facing prompt construction and capability wiring."""

from __future__ import annotations

from pathlib import Path

from tests.helpers import (
    make_actor_record,
    make_character_record,
    make_llm_backend_record,
)
from yuubot.core.assembly import build_agent_definition
from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import ActorFacadeBinding
from yuubot.core.integrations.impls.echo import ECHO_CAPABILITY_SPEC


def test_python_tool_facade_imports_follow_visible_capabilities(tmp_path: Path) -> None:
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
    ).tools["ipykernel"]
    echo_tool = build_agent_definition(
        binding,
        facade=_facade(tmp_path, capabilities=(ECHO_CAPABILITY_SPEC,)),
    ).tools["ipykernel"]

    no_capability_imports = {
        item["module"] for item in no_capability_tool.config["imports"]
    }
    echo_imports = {item["module"] for item in echo_tool.config["imports"]}
    assert "yb.delegate" in no_capability_imports
    assert "tim" in no_capability_imports
    assert "yb.schedule" in no_capability_imports
    assert "yext.echo" not in no_capability_imports
    assert "yext.echo" in echo_imports
    assert "yb.delegate.*" in no_capability_tool.config["expand_functions"]
    assert "tim.*" in no_capability_tool.config["expand_functions"]
    assert "yb.schedule.*" in no_capability_tool.config["expand_functions"]
    assert "yext.echo.*" not in no_capability_tool.config["expand_functions"]
    assert "yext.echo.*" in echo_tool.config["expand_functions"]


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


def _facade(tmp_path, *, capabilities):
    return ActorFacadeBinding(
        actor_id="actor-1",
        agent_name="actor-1",
        session_id="session-1",
        mailbox_id="actor:actor-1",
        capabilities=tuple(capabilities),
        root=tmp_path,
        sys_path=[str(tmp_path)],
        startup_code="import yb\nimport yext",
        session_state={},
    )

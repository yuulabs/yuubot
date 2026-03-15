from __future__ import annotations

from yuubot.prompt import AgentSpec, Character, RuntimeInfo, build_prompt_spec, build_system_prompt


def test_control_tools_section_added_when_agent_has_control_tools():
    char = Character(
        name="worker",
        description="",
        min_role="master",
        persona="persona",
        spec=AgentSpec(
            tools=["sleep", "inspect_background", "wait_background", "delegate"],
        ),
    )

    spec = build_prompt_spec(char, RuntimeInfo(provider="test", model="m"))
    prompt = build_system_prompt(spec)

    assert "control_tools" in {name for name, _ in spec.resolved_sections}
    assert "inspect_background" in prompt
    assert "wait_background" in prompt
    assert "delegate defer 到后台" in prompt


def test_control_tools_section_not_added_without_control_tools():
    char = Character(
        name="main",
        description="",
        min_role="folk",
        persona="persona",
        spec=AgentSpec(
            tools=["call_cap_cli", "read_cap_doc"],
        ),
    )

    spec = build_prompt_spec(char, RuntimeInfo(provider="test", model="m"))

    assert "control_tools" not in {name for name, _ in spec.resolved_sections}

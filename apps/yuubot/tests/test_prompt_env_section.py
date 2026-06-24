"""System prompt env-management section (Phase 2 wiring).

Public boundary: ``yuubot.core.assembly._prompt._system_prompt`` (or
``_render_system_instructions``) — the env-management bullets must appear in
the rendered system prompt when an agent has a workspace.

Observable outcome (Acceptance Criterion #5): the prompt contains
``uv add``, ``restart_kernel``, and the self-check guidance (``uv pip list``).
"""

from __future__ import annotations

from pathlib import Path

from yuubot.core.assembly._prompt import _render_system_instructions
from yuubot.core.bindings import AgentBinding
from yuubot.resources.records import (
    BudgetPolicy,
    CapabilitySetRecord,
    CharacterHints,
    CharacterRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)


ENV_BULLETS = (
    "uv add",
    "restart_kernel",
    "uv pip list",
    "execute_python",
)


def test_env_management_bullets_present_with_workspace(tmp_path: Path) -> None:
    binding = _make_binding(workspace_path=tmp_path)
    rendered = _render_system_instructions(binding, mode="conversation")
    for needle in ENV_BULLETS:
        assert needle in rendered, (
            f"env-management bullet {needle!r} missing from prompt:\n{rendered}"
        )


def test_env_management_bullets_absent_without_workspace() -> None:
    """Without a workspace there is no venv to manage; the env bullets must
    not leak into the prompt."""
    binding = _make_binding(workspace_path=None)
    rendered = _render_system_instructions(binding, mode="conversation")
    for needle in ("uv add", "restart_kernel", "uv pip list"):
        assert needle not in rendered


def test_env_management_bullets_live_inside_system_instructions_section(
    tmp_path: Path,
) -> None:
    from yuubot.core.assembly._prompt import _system_prompt

    binding = _make_binding(workspace_path=tmp_path)
    system = _system_prompt(binding, mode="conversation")

    sys_inst_pos = system.find("# System Instructions")
    integ_pos = system.find("# Integration Prompt Sections")
    assert sys_inst_pos != -1 and integ_pos != -1
    section_two = system[sys_inst_pos:integ_pos]

    for needle in ENV_BULLETS:
        assert needle in section_two, (
            f"{needle!r} not inside Section 2 (System Instructions):\n{section_two}"
        )


# ── figure-delivery contract ──────────────────────────────────────

FIGURE_BULLETS = (
    "Save any output files under the workspace",
    "Do NOT fabricate external URLs",
    "Label charts in English by default",
)


def _make_binding_with_segment(
    tmp_path: Path,
    *,
    segment: str,
) -> AgentBinding:
    return _make_binding(
        workspace_path=tmp_path,
        cap_workspace_path=segment,
    )


def test_figure_delivery_bullets_present_with_workspace_segment(
    tmp_path: Path,
) -> None:
    binding = _make_binding_with_segment(tmp_path, segment="test")
    rendered = _render_system_instructions(binding, mode="conversation")

    assert "Save any output files under the workspace" in rendered, (
        f"file delivery guidance missing from prompt:\n{rendered}"
    )
    assert "/workspace/test/" in rendered, (
        f"workspace browser URL base missing from prompt:\n{rendered}"
    )
    for needle in FIGURE_BULLETS:
        assert needle in rendered, (
            f"figure bullet {needle!r} missing from prompt:\n{rendered}"
        )


def test_figure_delivery_falls_back_when_segment_empty(tmp_path: Path) -> None:
    """An empty capability_set.workspace_path must not produce a '/workspace//'
    double-slash URL; the relative-path fallback bullet renders instead."""
    binding = _make_binding(workspace_path=tmp_path)  # default segment == ""
    rendered = _render_system_instructions(binding, mode="conversation")

    assert "/workspace//" not in rendered, (
        f"double-slash workspace URL leaked into prompt:\n{rendered}"
    )
    assert "artifacts/<name>.png" in rendered, (
        f"relative-path fallback missing from prompt:\n{rendered}"
    )
    assert "Do NOT fabricate external URLs" in rendered, (
        f"prohibition bullet missing from prompt:\n{rendered}"
    )


def test_figure_delivery_bullets_absent_without_workspace() -> None:
    binding = _make_binding(workspace_path=None)
    rendered = _render_system_instructions(binding, mode="conversation")

    assert "Save any output files under the workspace" not in rendered, (
        f"file delivery bullets leaked without workspace:\n{rendered}"
    )
    assert "Delivering files to the user:" not in rendered


def test_figure_delivery_bullets_live_inside_system_instructions_section(
    tmp_path: Path,
) -> None:
    from yuubot.core.assembly._prompt import _system_prompt

    binding = _make_binding_with_segment(tmp_path, segment="test")
    system = _system_prompt(binding, mode="conversation")

    sys_inst_pos = system.find("# System Instructions")
    integ_pos = system.find("# Integration Prompt Sections")
    section_two = system[sys_inst_pos:integ_pos]

    for needle in ("/workspace/test/", *FIGURE_BULLETS):
        assert needle in section_two, (
            f"{needle!r} not inside Section 2 (System Instructions):\n{section_two}"
        )


# ── helpers (mirror tests/test_system_prompt_bullets.py) ──────────


def _make_binding(
    *,
    workspace_path: Path | None,
    cap_workspace_path: str = "",
) -> AgentBinding:
    cap_set = CapabilitySetRecord(name="test-cap", workspace_path=cap_workspace_path)
    return AgentBinding(
        owner_id="test-owner",
        agent_name="test-agent",
        character=_dummy_character(),
        capability_set=cap_set,
        llm=_dummy_llm(),
        llm_options=_dummy_llm_options(),
        budget=_dummy_budget(),
        workspace_path=workspace_path,
    )


def _dummy_character() -> CharacterRecord:
    return CharacterRecord(
        name="test-char",
        description="Test character",
        system_prompt="",
        facade_module="",
        default_hints=CharacterHints(),
    )


def _dummy_llm():
    from yuubot.core.llm import BoundLLM

    backend = LLMBackendRecord(
        name="test-backend",
        yuuagents_provider="openai",
        default_model="gpt-4",
        model_capabilities=ModelCapabilities(),
        models=ModelCatalog(),
        pricing=PricingTable(),
        budget=BudgetPolicy(),
    )
    return BoundLLM(
        backend=backend,
        model="gpt-4",
        stream_options={},
    )


def _dummy_llm_options() -> YuuAgentLLMOptions:
    return YuuAgentLLMOptions()


def _dummy_budget() -> YuuAgentBudget:
    return YuuAgentBudget()

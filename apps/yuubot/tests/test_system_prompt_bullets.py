"""Additive bullet tests for ``_render_system_instructions``.

Scope of this PR (Phase B):

- The math bullet is unconditional and lands this PR.
- The workspace browse bullet is DEFERRED. The ``AgentBinding`` dataclass does
  not carry ``admin_url`` nor ``conversation_id`` (nor a bare ``actor_id``
  for conversation-mode bindings — ``owner_id`` is ``"conversation:<id>"``),
  so ``_render_system_instructions`` cannot compose a fully-qualified
  workspace browse URL from the binding alone. Extending ``AgentBinding`` is a
  planner decision and is tracked as a blocker in the PR document. The gated
  test below is skipped until the binding shape is extended.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_math_bullet_is_unconditional() -> None:
    """The LaTeX math bullet must render even when there is no workspace.

    The math bullet is gated only on Phase A-4 (KaTeX rendering in text blocks),
    which is already merged — not on workspace state or admin URL availability.
    """
    binding = _make_binding(workspace_path=None)
    rendered = _render_system_instructions(binding, mode="conversation")
    assert "LaTeX math" in rendered, "LaTeX math bullet missing from system instructions"
    assert "$...$" in rendered, "inline LaTeX marker missing"
    assert "$$...$$" in rendered, "block LaTeX marker missing"


@pytest.mark.skip(
    reason=(
        "deferred: AgentBinding lacks admin_url/conversation_id (and a bare "
        "actor_id for conversation-mode bindings); see PR doc blocker"
    ),
)
async def test_workspace_browse_bullet_gated_on_admin_url(tmp_path: Path) -> None:
    """Workspace browse bullet appears only when ``admin_url`` + ``conversation_id`` are available.

    DEFERRED: ``AgentBinding`` does not yet carry ``admin_url`` or
    ``conversation_id`` at prompt assembly time. Extending the binding shape
    is a planner decision (see PR doc blocker). When the binding carries those
    fields, this test should assert:
      (a) binding with workspace_path + admin_url + conversation_id → contains
          "browse this workspace over HTTP"
      (b) binding without admin_url → missing that bullet
      (c) both cases contain "LaTeX math" bullet
    """
    raise AssertionError("deferred workspace browse bullet test was run unexpectedly")


# ── Helpers ──────────────────────────────────────────────────────


def _make_binding(*, workspace_path: Path | None) -> AgentBinding:
    cap_set = CapabilitySetRecord(name="test-cap")
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

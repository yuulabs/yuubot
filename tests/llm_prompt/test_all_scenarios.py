"""Parameterized runner for all LLM prompt visibility scenarios.

Add new scenarios to the ``ALL_SCENARIOS`` list.
"""

from __future__ import annotations

import pytest

from tests.llm_prompt.framework import ScenarioRunner
from tests.llm_prompt.scenario import PromptScenario
from tests.llm_prompt.scenarios.execute_python_visibility import (
    ExecutePythonToolVisibility,
)

ALL_SCENARIOS: list[PromptScenario] = [
    ExecutePythonToolVisibility(),
]


@pytest.mark.parametrize(
    "scenario",
    ALL_SCENARIOS,
    ids=lambda s: s.name,
)
async def test_llm_prompt_scenario(
    scenario: PromptScenario,
    yuubot_config: object,
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Run a prompt visibility scenario end-to-end."""
    runner = ScenarioRunner()
    result = await runner.run(
        scenario,
        config=yuubot_config,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert result.passed, result.summary
